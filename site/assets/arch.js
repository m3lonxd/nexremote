/* =========================================================================
   NexRemote — mimari diyagram (animasyonlu inline SVG)
   -------------------------------------------------------------------------
   #arch-diagram içine saf SVG enjekte eder: bir ajan isteğinin adaptörden
   geçip çekirdeğe, oradan GUI/TUI arka ucuna gidişini ve sonucun geri
   dönüşünü gösterir.

   Kurallar:
   - Harici kütüphane / CDN / resim YOK. Her şey bu dosyanın içinde.
   - Renkler yalnızca CSS değişkenleriyle (var(--panel), var(--accent) ...),
     böylece açık/koyu tema kendiliğinden çalışır.
   - Animasyon az ve amaçlı: kesikli çizgi akışı, düğüm belirme sırası ve
     hat boyunca ilerleyen TEK bir paket noktası.
   - `prefers-reduced-motion: reduce` ise hiçbir hareket kalmaz ve düğümler
     doğrudan görünür.

   `defer` ile yüklenir; DOM hazır olduğunda çalışır ve kendini korur.
   ========================================================================= */
(function () {
  'use strict';

  var mount = document.getElementById('arch-diagram');
  if (!mount) return;                                   // bölüm yoksa sessizce çık
  if (mount.querySelector('#nr-arch-svg')) return;      // iki kez enjekte etme

  /* ---------------------------------------------------------------- motion */
  var reduce = false;
  try {
    reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (err) {
    reduce = false;
  }

  /* ----------------------------------------------------------------- nodes */
  /* Soldan sağa akış. accent: true → çekirdek düğüm (yeşil çerçeve). */
  var NODES = [
    { x: 30,  y: 140, w: 150, h: 80,  label: 'Agent',           sub: 'intent' },
    { x: 230, y: 130, w: 190, h: 100, label: 'Adapter',         sub: 'MCP · CLI · HTTP · text' },
    { x: 460, y: 130, w: 200, h: 100, label: 'NexRemote core',  sub: 'see · find · click · keys', accent: true },
    { x: 700, y: 45,  w: 250, h: 90,  label: 'GUI backend',     sub: 'AT-SPI · by name' },
    { x: 700, y: 225, w: 250, h: 90,  label: 'TUI backend',     sub: 'tmux · by text' }
  ];

  /* Düğümü <g> olarak üret: yuvarlatılmış kutu + etiket + alt yazı. */
  function nodeMarkup(n) {
    var cx = n.x + n.w / 2;
    var cy = n.y + n.h / 2;
    return '' +
      '<g class="nr-node">' +
        '<rect class="node-box' + (n.accent ? ' accent' : '') + '" ' +
              'x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="10"/>' +
        '<text class="node-label" x="' + cx + '" y="' + (cy - 6) + '" text-anchor="middle">' +
          esc(n.label) +
        '</text>' +
        '<text class="node-sub" x="' + cx + '" y="' + (cy + 14) + '" text-anchor="middle">' +
          esc(n.sub) +
        '</text>' +
      '</g>';
  }

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* ------------------------------------------------------------- geometry */
  /* İleri yol: Agent → Adapter → Core → (GUI | TUI). Geri dönüş: sağdan
     aşağı, alttan sola, Agent'ın altından yukarı. */
  var PATH = {
    toAdapter: 'M180,180 H230',
    toCore:    'M420,180 H460',
    toGui:     'M660,180 H675 Q685,180 685,170 V100 Q685,90 695,90 H700',
    toTui:     'M660,180 H675 Q685,180 685,190 V260 Q685,270 695,270 H700',
    fromTui:   'M950,270 H975',
    result:    'M950,90 H965 Q975,90 975,100 V330 Q975,340 965,340 H115 Q105,340 105,330 V222'
  };
  /* Paket noktasının izleyeceği kesintisiz güzergâh (Agent → GUI). */
  var ROUTE = 'M180,180 H675 Q685,180 685,170 V100 Q685,90 695,90 H700';

  /* ------------------------------------------------------------------ SVG */
  var style = '' +
    '<style>' +
      /* Düğüm belirmesi: kısa, gecikmesiz başlar; stagger JS ile verilir. */
      '#nr-arch-svg .nr-node{opacity:0;transform:translateY(7px);' +
        'transition:opacity .4s ease,transform .4s cubic-bezier(.2,.7,.2,1)}' +
      '#nr-arch-svg .nr-node.nr-in{opacity:1;transform:translateY(0)}' +
      /* Canlı hat: kesikli desen akar (6 8 → periyot 14, iki periyot/tur). */
      '#nr-arch-svg .nr-live{animation:nr-flow 1.5s linear infinite}' +
      '@keyframes nr-flow{from{stroke-dashoffset:0}to{stroke-dashoffset:-28}}' +
      /* Ok başları ve paket: renkler daima token üzerinden. */
      '#nr-arch-svg .nr-head{fill:var(--line-2)}' +
      '#nr-arch-svg .nr-head-live{fill:var(--accent)}' +
      '#nr-arch-svg .nr-packet{fill:var(--accent)}' +
      /* Hareket azaltma tercihi: her şey durur, içerik görünür kalır. */
      '@media (prefers-reduced-motion:reduce){' +
        '#nr-arch-svg .nr-node{opacity:1;transform:none;transition:none}' +
        '#nr-arch-svg .nr-live{animation:none}' +
      '}' +
    '</style>';

  var defs = '' +
    '<defs>' +
      '<marker id="nr-arrow" viewBox="0 0 9 9" refX="8" refY="4.5" ' +
              'markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">' +
        '<path class="nr-head" d="M0,0 L9,4.5 L0,9 Z"/>' +
      '</marker>' +
      '<marker id="nr-arrow-live" viewBox="0 0 9 9" refX="8" refY="4.5" ' +
              'markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto">' +
        '<path class="nr-head-live" d="M0,0 L9,4.5 L0,9 Z"/>' +
      '</marker>' +
      /* Yalnızca paket noktası için görünmez güzergâh. */
      '<path id="nr-route" d="' + ROUTE + '" fill="none" stroke="none"/>' +
    '</defs>';

  /* İleri hatlar: canlı (kesikli, animasyonlu) + ok başı. */
  var links = '' +
    '<g class="nr-links">' +
      '<path class="flow-live nr-live" d="' + PATH.toAdapter + '" marker-end="url(#nr-arrow-live)"/>' +
      '<path class="flow-live nr-live" d="' + PATH.toCore + '" marker-end="url(#nr-arrow-live)"/>' +
      '<path class="flow-live nr-live" d="' + PATH.toGui + '" marker-end="url(#nr-arrow-live)"/>' +
      '<path class="flow-live nr-live" d="' + PATH.toTui + '" marker-end="url(#nr-arrow-live)"/>' +
      /* TUI çıkışı geri dönüş hattına katılır (statik bağlantı). */
      '<path class="flow" d="' + PATH.fromTui + '"/>' +
      /* Sonucun ajana dönüşü. */
      '<path class="flow-live nr-live" d="' + PATH.result + '" marker-end="url(#nr-arrow-live)"/>' +
    '</g>';

  /* Akış etiketleri: sade, mono, düşük kontrast. */
  var labels = '' +
    '<g class="nr-labels">' +
      '<text class="flow-label" x="205" y="170" text-anchor="middle">intent</text>' +
      '<text class="flow-label" x="440" y="170" text-anchor="middle">call</text>' +
      '<text class="flow-label" x="500" y="356" text-anchor="middle">result returns to the agent</text>' +
    '</g>';

  /* Paket: hat boyunca ilerleyen tek küçük daire. Hareket azaltmada eklenmez. */
  var packet = reduce ? '' :
    '<circle class="nr-packet" r="3.5" cx="0" cy="0">' +
      '<animateMotion dur="6s" repeatCount="indefinite" rotate="0" ' +
                     'keyPoints="0;1" keyTimes="0;1" calcMode="linear">' +
        '<mpath href="#nr-route" xlink:href="#nr-route"/>' +
      '</animateMotion>' +
    '</circle>';

  var svg = '' +
    '<svg id="nr-arch-svg" xmlns="http://www.w3.org/2000/svg" ' +
         'xmlns:xlink="http://www.w3.org/1999/xlink" ' +
         'viewBox="0 0 1000 360" preserveAspectRatio="xMidYMid meet" ' +
         'role="img" aria-labelledby="nr-arch-title">' +
      '<title id="nr-arch-title">' +
        'An agent request travels through an adapter into the NexRemote core, ' +
        'which drives the GUI (AT-SPI) or terminal (tmux) backend, and the result ' +
        'returns to the agent.' +
      '</title>' +
      style + defs + links +
      /* Paket düğümlerin altında kalır: kutunun içine girer, aralarında görünür. */
      packet +
      '<g class="nr-nodes">' + NODES.map(nodeMarkup).join('') + '</g>' +
      labels +
    '</svg>';

  /* noscript yedeğini korumak için içeriği silmek yerine başa ekliyoruz. */
  mount.insertAdjacentHTML('afterbegin', svg);

  /* ------------------------------------------------------------- reveal */
  var root = mount.querySelector('#nr-arch-svg');
  if (!root) return;
  var nodes = root.querySelectorAll('.nr-node');

  function showAll() {
    for (var i = 0; i < nodes.length; i++) nodes[i].classList.add('nr-in');
  }

  if (reduce || typeof IntersectionObserver !== 'function') {
    showAll();
    return;
  }

  /* Görünür olunca kısa bir stagger ile belir — tek seferlik. */
  var io = new IntersectionObserver(function (entries) {
    for (var i = 0; i < entries.length; i++) {
      if (!entries[i].isIntersecting) continue;
      for (var j = 0; j < nodes.length; j++) {
        nodes[j].style.transitionDelay = (j * 70) + 'ms';
        nodes[j].classList.add('nr-in');
      }
      io.disconnect();
      break;
    }
  }, { threshold: 0.25 });

  io.observe(mount);
})();
