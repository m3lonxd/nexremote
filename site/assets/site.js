/* NexRemote — site davranışı.
   Tek rAF kaydırma döngüsü (oku → yaz), IntersectionObserver ile görünme,
   ekran dışındaki animasyonlar durur. prefers-reduced-motion: son durum, hareket yok. */
(function () {
  "use strict";

  var D = document, H = D.documentElement;
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* özel pencere */ } }
  };
  var mm = function (q) { return !!(window.matchMedia && window.matchMedia(q).matches); };
  var reduced = mm("(prefers-reduced-motion: reduce)");
  var fine = mm("(hover: hover) and (pointer: fine)");
  var hasIO = "IntersectionObserver" in window;
  function $(s, r) { return (r || D).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || D).querySelectorAll(s)); }
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // ------------------------------------------------------------------ dil
  // İlk ziyarette Türkçe tarayıcıyı /tr/'ye yönlendir; seçim yapıldıysa ona uy.
  var lang = H.lang;
  if (!store.get("nr-lang") && lang === "en" &&
      /^tr\b/i.test((navigator.languages && navigator.languages[0]) || navigator.language || "")) {
    location.replace("/tr/" + location.hash);
    return;
  }
  $$("[data-lang]").forEach(function (a) {
    a.addEventListener("click", function () { store.set("nr-lang", a.dataset.lang); });
  });

  // ----------------------------------------------------------------- tema
  var themeBtn = $(".theme");
  if (themeBtn) themeBtn.addEventListener("click", function () {
    var cur = H.dataset.theme || (mm("(prefers-color-scheme: dark)") ? "dark" : "light");
    var next = cur === "dark" ? "light" : "dark";
    H.dataset.theme = next;
    store.set("nr-theme", next);
  });

  // -------------------------------------------------------------- kopyala
  $$("[data-copy]").forEach(function (btn) {
    var lab = $("[data-label]", btn), label = lab.textContent, timer;
    btn.addEventListener("click", function () {
      var el = $(btn.dataset.copy);
      if (!el) return;
      var text = el.dataset.full || el.textContent;
      var done = function () {
        btn.classList.add("done"); lab.textContent = btn.dataset.done || label;
        clearTimeout(timer);
        timer = setTimeout(function () { btn.classList.remove("done"); lab.textContent = label; }, 1700);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { selectText(el); });
      } else selectText(el);
    });
  });
  function selectText(el) {
    var r = D.createRange(); r.selectNodeContents(el);
    var s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }

  // ------------------------------------------------------------- sekmeler
  $$("[data-tabs]").forEach(function (root) {
    var list = $('[role="tablist"]', root), ind = $(".tab-ind", root);
    var tabs = $$('[role="tab"]', root);
    function place(tab) {
      if (!ind) return;
      list.classList.add("has-ind");
      ind.style.transform = "translateX(" + tab.offsetLeft + "px) scaleX(" + (tab.offsetWidth / 100) + ")";
    }
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab, panel = D.getElementById(t.getAttribute("aria-controls"));
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        panel.hidden = !on;
        if (on && !reduced) { panel.classList.remove("show"); void panel.offsetWidth; panel.classList.add("show"); }
      });
      place(tab);
      if (focus) tab.focus();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t); });
      t.addEventListener("keydown", function (e) {
        var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 :
                e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
        if (j === null) return;
        e.preventDefault();
        select(tabs[(j + tabs.length) % tabs.length], true);
      });
    });
    var cur = function () { return tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0]; };
    place(cur());
    if (D.fonts && D.fonts.ready) D.fonts.ready.then(function () { place(cur()); });
    addEventListener("resize", function () { place(cur()); });
  });

  // -------------------------------------------------------------- görünme
  // Her öğe bir kez görünür; "reveal" olayı bileşenlere haber verir.
  $$("[data-stagger]").forEach(function (p) {
    var i = 0;
    Array.prototype.forEach.call(p.children, function (c) {
      if (c.hasAttribute("data-reveal")) c.style.setProperty("--i", i++);
    });
  });
  function revealed(el) {
    el.classList.add("in");
    el.dispatchEvent(new CustomEvent("reveal"));
  }
  var revealEls = $$("[data-reveal]");
  if (reduced || !hasIO) {
    revealEls.forEach(revealed);
  } else {
    var rio = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { rio.unobserve(e.target); revealed(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0 });
    revealEls.forEach(function (el) { rio.observe(el); });
  }
  function onReveal(el, fn) {
    if (!el) return;
    if (el.classList.contains("in")) fn(); else el.addEventListener("reveal", fn, { once: true });
  }
  // Görünürlük takibi (animasyonları ekran dışında durdurmak için)
  function watch(el, fn, margin) {
    if (!el) return;
    if (!hasIO) { fn(true); return; }
    new IntersectionObserver(function (es) { fn(es[es.length - 1].isIntersecting); },
                             { rootMargin: margin || "0px" }).observe(el);
  }

  // ---------------------------------------------- tek kaydırma döngüsü
  var tasks = [], ticking = false;
  function schedule() { if (!ticking) { ticking = true; requestAnimationFrame(frame); } }
  function frame() {
    ticking = false;
    var vh = innerHeight, i, writes = [];
    for (i = 0; i < tasks.length; i++) writes.push(tasks[i](vh));       // önce okumalar
    for (i = 0; i < writes.length; i++) if (writes[i]) writes[i]();       // sonra yazmalar
  }
  addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", schedule);

  // ilerleme çubuğu + üst barın koyu bantlar üstündeki rengi
  var bar = $("[data-progress-bar]"), top = $(".top"), bands = $$(".band");
  tasks.push(function () {
    var max = H.scrollHeight - innerHeight, p = max > 0 ? scrollY / max : 0;
    var over = bands.some(function (b) { var r = b.getBoundingClientRect(); return r.top <= 70 && r.bottom > 70; });
    return function () {
      if (bar) bar.style.transform = "scaleX(" + p.toFixed(4) + ")";
      if (top) top.classList.toggle("tk-band", over);
    };
  });

  // gezinme: geçerli bölüm
  var navLinks = $$(".nav a");
  var navSecs = navLinks.map(function (a) { return D.getElementById(a.getAttribute("href").slice(1)); });
  tasks.push(function (vh) {
    var cur = -1;
    navSecs.forEach(function (s, i) {
      if (!s) return;
      var r = s.getBoundingClientRect();
      if (r.top <= vh * 0.4 && r.bottom > vh * 0.4) cur = i;
    });
    return function () { navLinks.forEach(function (a, i) { a.classList.toggle("on", i === cur); }); };
  });

  if (!reduced) {
    // hero sahnesi: eğik başlar, kaydırdıkça düzleşir
    var tilt = $("[data-hero-tilt]");
    if (tilt) tasks.push(function (vh) {
      var p = clamp(scrollY / (vh * 0.55), 0, 1), e = 1 - Math.pow(1 - p, 3);
      if (p >= 1 && tilt.dataset.flat) return null;
      return function () {
        tilt.dataset.flat = p >= 1 ? "1" : "";
        tilt.style.transform = "rotateX(" + (22 * (1 - e)).toFixed(2) + "deg) scale(" + (0.9 + 0.1 * e).toFixed(4) + ")";
      };
    });

    // koyu bantlar: içeri girerken kenarlardan açılır (clip-path)
    $$("[data-wipe]").forEach(function (el) {
      tasks.push(function (vh) {
        var r = el.getBoundingClientRect();
        if (r.top > vh || r.bottom < 0) return null;
        var p = ss(0, 1, (vh - r.top) / (vh * 0.65)), inset = (1 - p) * Math.min(innerWidth * 0.035, 56), rad = (1 - p) * 28;
        return function () { el.style.clipPath = p >= 1 ? "none" : "inset(0 " + inset.toFixed(1) + "px round " + rad.toFixed(1) + "px)"; };
      });
    });

    // ızgara ve büyük numaralar: hafif paralaks
    $$("[data-parallax]").forEach(function (el) {
      var big = el.classList.contains("bignum");
      var host = big ? el.parentNode : (el.closest("section") || el.parentNode);
      var k = big ? -0.1 : 0.1, lim = big ? (innerWidth < 760 ? 12 : 40) : 1e4;
      tasks.push(function (vh) {
        var r = host.getBoundingClientRect();
        if (r.top > vh || r.bottom < 0) return null;
        var y = clamp((r.top + r.height / 2 - vh / 2) * k, -lim, lim);
        return function () { el.style.transform = "translate3d(0," + y.toFixed(1) + "px,0)"; };
      });
    });
  }

  // ======================================================== HERO döngüsü
  var hero = $("[data-hero]");
  if (hero && !reduced) (function () {
    var i18n = JSON.parse($("#replay-i18n").textContent);
    var cmdEl = $("[data-hd-cmd]", hero), outEl = $("[data-hd-out]", hero);
    var steps = $$("[data-hd-step]", hero), agent = $("[data-hd-agent]", hero);
    var typed = $("[data-hd-typed]", hero), caret = $(".hd-caret", hero);
    var visible = true, waiters = [];
    function active() { return visible && !D.hidden; }
    function wake() { if (active()) { var w = waiters; waiters = []; w.forEach(function (f) { f(); }); } }
    watch(hero, function (v) { visible = v; wake(); }, "60px");
    D.addEventListener("visibilitychange", wake);
    async function wait(ms) {
      await sleep(ms);
      while (!active()) await new Promise(function (r) { waiters.push(r); });
    }
    function set(c, on) { hero.classList.toggle(c, on); }
    function step(n) { steps.forEach(function (s, i) { s.classList.toggle("on", i === n); }); }
    function out(t) { outEl.textContent = t; outEl.classList.remove("fresh"); void outEl.offsetWidth; outEl.classList.add("fresh"); }
    function agentTo(x, y, instant) {
      agent.style.transition = instant ? "none" : "transform 1.1s cubic-bezier(.45,.05,.25,1), opacity .3s";
      agent.style.transform = "translate(" + x + "px," + y + "px)";
    }
    function click() { set("s-click", false); void hero.offsetWidth; set("s-click", true); }
    function moveCaret() {
      var w = 0; try { w = typed.getComputedTextLength(); } catch (e) { /* düzen yok */ }
      caret.setAttribute("x", 78 + w + (w ? 4 : 0));
    }
    async function type(el, text, speed, after) {
      for (var i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); if (after) after(); await wait(speed); }
    }
    async function run(cmd) {
      cmdEl.textContent = ""; outEl.textContent = "";
      await type(cmdEl, cmd, 24);
      await wait(320);
    }
    function reset() {
      hero.className = "hd";
      typed.textContent = ""; moveCaret();
      agentTo(1080, 640, true);
      step(-1);
    }
    async function loop() {
      reset();
      await wait(900);
      for (;;) {
        step(0);
        await run("nexremote windows");
        out("* win:{c31f…}  org.kde.konsole  1100x780+0+0      nexwatch : claude-bin — Konsole\n" +
            "  win:{8c1e…}  systemsettings   760x540+1180+60   Appearance — System Settings\n" +
            "  win:{a41f…}  zen              880x560+1060+520  Player — Zen Browser");
        set("s-tags", true);
        await wait(2300);

        step(1); set("s-tags", false);
        await run('nexremote find "Dark" --window win:{8c1e…}');
        set("s-node", true);
        out("atspi:4:0/2/7\t[checkbox] Dark  <toggle,click>");
        await wait(1700);
        await run("nexremote point atspi:4:0/2/7 --json");
        set("s-agent", true); agentTo(1122, 198);
        await wait(1150);
        click(); set("s-dark", true);
        out('{ "ok": true, "ref": "atspi:4:0/2/7", "x": 1702, "y": 258 }');
        await wait(1900);

        step(2); set("s-node", false); set("s-agent", false);
        await run("nexremote term-read konsole:7891:2");
        set("s-hl", true);
        out("✗ 2 failed · test_login, test_refresh\nShould I fix them? (y/n, or tell me)\n›");
        await wait(1900);
        await run('nexremote term-send konsole:7891:2 "' + i18n.termMsg + '" --enter');
        await type(typed, i18n.termMsg, 28, moveCaret);
        out('{ "ok": true, "ref": "konsole:7891:2" }');
        await wait(500);
        set("s-reply", true);
        await wait(1700);

        step(3); set("s-hl", false);
        await run("nexremote screenshot --window win:{a41f…} --max-width 1568 --json");
        set("s-flash", false); void hero.offsetWidth; set("s-flash", true); set("s-shot", true);
        out('{ "path": "/run/user/1000/nexremote-shot.png",\n  "width": 880, "height": 560,\n  "scale": 1.0, "origin": [1060, 520] }');
        await wait(1900);
        await run("nexremote click 440 190 --from-shot --json");
        set("s-agent", true); agentTo(805, 544);
        await wait(1150);
        click(); set("s-play", true); set("s-run", true);
        out('{ "ok": true, "x": 1500, "y": 710, "button": "left" }');
        await wait(3200);
        reset();
        await wait(700);
      }
    }
    loop();
  })();

  // ====================================================== kayan şerit
  var marquee = $("[data-marquee]");
  if (marquee && !reduced) watch(marquee, function (v) { marquee.classList.toggle("on", v); });

  // ======================================= "Nasıl çalışır": sabit sahne
  var story = $("[data-story]");
  if (story) (function () {
    var pinMQ = window.matchMedia ? matchMedia("(min-width: 1000px)") : null;
    var rungs = $$("[data-rung]", story), layers = $$(".ss-layer", story);
    var cmdEl = $("[data-ss-cmd]", story), getsEl = $("[data-ss-gets]", story), fill = $("[data-ss-fill]", story);
    var items = $$("[data-sc]", story).map(function (el) {
      var a = el.dataset.sc.split(",");
      var pt = function (s) { var v = (s || "0,0").split(",").map(Number); return v; };
      return { el: el, L: +a[0], a: +a[1], b: +a[2], m: a[3], f: pt(el.dataset.from), t: pt(el.dataset.to) };
    });
    var last = -1;
    function pinned() { return !reduced && pinMQ && pinMQ.matches; }
    function setPin() { H.classList.toggle("story-pin", pinned()); schedule(); }
    setPin();
    if (pinMQ && pinMQ.addEventListener) pinMQ.addEventListener("change", setPin);

    tasks.push(function (vh) {
      if (!pinned()) return null;
      var r = $(".rungs", story).getBoundingClientRect();
      if (r.top > vh || r.bottom < 0) return null;
      var p = clamp((vh / 2 - r.top) / r.height, 0, 1);
      return function () {
        var t = [0, 1, 2, 3].map(function (i) { return clamp(p * 4 - i + 0.2, 0, 1); });
        var inn = t.map(function (x) { return ss(0, 0.12, x); });
        layers.forEach(function (l, i) { l.style.opacity = (inn[i] * (i < 3 ? 1 - inn[i + 1] : 1)).toFixed(3); });
        items.forEach(function (it) {
          var v = ss(it.a, it.b, t[it.L]), s = it.el.style, next = it.L < 3 ? inn[it.L + 1] : 0;
          switch (it.m) {
            case "o": s.opacity = v; break;
            case "hide": s.opacity = 1 - v; break;
            case "d": s.strokeDashoffset = (1 - v).toFixed(4); break;
            case "x": s.opacity = v; s.transform = "translateY(" + ((1 - v) * 10).toFixed(2) + "px)"; break;
            case "dim": case "dimtext": s.opacity = (1 - 0.72 * v * (1 - next)).toFixed(3); break;
            case "on": it.el.style.fill = v > 0.5 ? "#3d8fd6" : ""; break;
            case "move": s.transform = "translate(" + lerp(it.f[0], it.t[0], v).toFixed(1) + "px," + lerp(it.f[1], it.t[1], v).toFixed(1) + "px)"; break;
            case "flash": s.opacity = v > 0 && v < 1 ? (Math.sin(v * Math.PI) * 0.8).toFixed(3) : 0; break;
            case "ring": s.opacity = v > 0 && v < 1 ? (1 - v).toFixed(3) : 0; s.transform = "scale(" + (1 + 2.4 * v).toFixed(3) + ")"; break;
          }
        });
        var idx = clamp(Math.floor(p * 4), 0, 3);
        if (idx !== last) {
          last = idx;
          rungs.forEach(function (rg, i) { rg.classList.toggle("on", i === idx); });
          cmdEl.textContent = rungs[idx].dataset.cmd;
          getsEl.textContent = rungs[idx].dataset.gets;
        }
        fill.style.transform = "scaleX(" + p.toFixed(4) + ")";
      };
    });
  })();

  // ===================================================== mimari diyagram
  var archWrap = $("[data-arch]");
  if (archWrap) (function () {
    var svgs = $$(".arch-svg", archWrap), cur = null, paths = {}, nodes = {}, lens = {};
    function pick() {
      cur = svgs.filter(function (s) { return getComputedStyle(s).display !== "none"; })[0] || svgs[0];
      paths = {}; nodes = {}; lens = {};
      $$(".ap", cur).forEach(function (p) { paths[p.dataset.ap] = p; lens[p.dataset.ap] = p.getTotalLength(); });
      $$(".an", cur).forEach(function (n) { nodes[n.dataset.an] = n; });
    }
    pick();
    addEventListener("resize", function () { var old = cur; pick(); if (old !== cur) drawn = -1; schedule(); });
    if (reduced) {
      svgs.forEach(function (s) { $$(".an", s).forEach(function (n) { n.classList.add("lit"); }); });
      return;
    }
    var drawn = -1, live = false;
    tasks.push(function (vh) {
      var r = cur.getBoundingClientRect();
      if (r.top > vh || r.bottom < 0) return null;
      var p = clamp((vh - r.top) / (vh * 0.45 + r.height * 0.55), 0, 1);
      if (p === drawn) return null;
      return function () {
        drawn = p;
        var st = [0, 1, 2, 3].map(function (s) { return ss(s * 0.2, s * 0.2 + 0.32, p); });
        Object.keys(paths).forEach(function (k) { paths[k].style.strokeDashoffset = (1 - st[+paths[k].dataset.stage]).toFixed(4); });
        Object.keys(nodes).forEach(function (k) {
          var need = k === "A" ? p > 0.01 : k.indexOf("ad") === 0 ? st[0] > 0.9 : k === "C" ? st[1] > 0.9 : k.indexOf("ch") === 0 ? st[2] > 0.9 : st[3] > 0.9;
          nodes[k].classList.toggle("lit", need);
        });
        live = st[3] >= 1;
        if (live) startPackets();
      };
    });

    // veri paketleri: ajan → adaptör → çekirdek → kanal → masaüstü
    var inView = false, running = false, trips = [], nextLaunch = 0, tripN = 0;
    watch(archWrap, function (v) { inView = v; if (v && live) startPackets(); }, "40px");
    var NS = "http://www.w3.org/2000/svg";
    function hit(id) {
      var n = nodes[id]; if (!n) return;
      n.classList.add("hit"); clearTimeout(n._t);
      n._t = setTimeout(function () { n.classList.remove("hit"); }, 260);
    }
    function startPackets() { if (!running && inView) { running = true; nextLaunch = 0; requestAnimationFrame(tick); } }
    function tick(now) {
      if (!inView || D.hidden) { running = false; return; }
      if (now >= nextLaunch && trips.length < 4) {
        var k = tripN % 4, j = (tripN * 5 + 1) % 6; tripN++;
        var g = $(".pk-g", cur), halo = D.createElementNS(NS, "circle"), dot = D.createElementNS(NS, "circle");
        halo.setAttribute("r", 9); halo.setAttribute("class", "pk-h");
        dot.setAttribute("r", 4.5); dot.setAttribute("class", "pk");
        g.appendChild(halo); g.appendChild(dot);
        trips.push({ segs: ["a" + k, "b" + k, "c" + j, "d" + j], dest: ["ad" + k, "C", "ch" + j, "D"], i: 0, t0: now, els: [halo, dot] });
        hit("A");
        nextLaunch = now + 820;
      }
      trips = trips.filter(function (tr) {
        var dur = 620, f = (now - tr.t0) / dur;
        while (f >= 1 && tr.i < 4) { hit(tr.dest[tr.i]); tr.i++; tr.t0 += dur; f = (now - tr.t0) / dur; }
        if (tr.i >= 4) { tr.els.forEach(function (e) { e.remove(); }); return false; }
        var id = tr.segs[tr.i], path = paths[id];
        if (!path) { tr.els.forEach(function (e) { e.remove(); }); return false; }
        var e = f < 0.5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
        var pt = path.getPointAtLength(lens[id] * e);
        var tf = "translate(" + pt.x.toFixed(1) + "px," + pt.y.toFixed(1) + "px)";
        tr.els[0].style.transform = tf; tr.els[1].style.transform = tf;
        return true;
      });
      requestAnimationFrame(tick);
    }
  })();

  // ================================================== sayaçlar (odometre)
  $$(".stat dd[data-count]").forEach(function (dd) {
    if (reduced) return;
    var text = dd.textContent.trim();
    var wrap = D.createElement("span"); wrap.className = "odo"; wrap.setAttribute("aria-hidden", "true");
    var strips = [];
    Array.prototype.forEach.call(text, function (ch) {
      if (/\d/.test(ch)) {
        var d = D.createElement("span"); d.className = "odo-d";
        var s = D.createElement("span"); s.className = "odo-s";
        for (var i = 0; i < 20; i++) { var n = D.createElement("span"); n.textContent = i % 10; s.appendChild(n); }
        d.appendChild(s); wrap.appendChild(d); strips.push([s, +ch]);
      } else {
        var sep = D.createElement("span"); sep.className = "odo-sep"; sep.textContent = ch; wrap.appendChild(sep);
      }
    });
    var sr = D.createElement("span"); sr.className = "sr"; sr.textContent = text;
    dd.textContent = ""; dd.appendChild(sr); dd.appendChild(wrap);
    onReveal(dd.closest("[data-reveal]"), function () {
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        strips.forEach(function (p, i) {
          p[0].style.transition = "transform " + (1.3 + i * 0.18).toFixed(2) + "s cubic-bezier(.2,.7,.2,1)";
          p[0].style.transform = "translate3d(0," + -(10 + p[1]) + "em,0)";
        });
      }); });
    });
  });

  // ============================================== yazma efektleri (görününce)
  var typeon = $("[data-typeon]");
  if (typeon && !reduced) (function () {
    var src = typeon.innerHTML, lines = src.split("\n");
    var PS = '<span class="c">$</span>';
    typeon.innerHTML = "";
    onReveal(typeon.closest("[data-reveal]"), async function () {
      var caret = D.createElement("span"); caret.className = "typing-caret";
      for (var i = 0; i < lines.length; i++) {
        var ln = lines[i];
        if (ln.indexOf(PS) === 0) {
          typeon.insertAdjacentHTML("beforeend", PS);
          var span = D.createElement("span"); typeon.appendChild(span); typeon.appendChild(caret);
          var tmp = D.createElement("div"); tmp.innerHTML = ln.slice(PS.length);
          var txt = tmp.textContent;
          for (var c = 1; c <= txt.length; c++) { span.textContent = txt.slice(0, c); await sleep(20); }
          await sleep(260);
        } else {
          typeon.insertAdjacentHTML("beforeend", ln);
          typeon.appendChild(caret);
          await sleep(70);
        }
        if (i < lines.length - 1) typeon.insertBefore(D.createTextNode("\n"), caret);
      }
      caret.remove();
      typeon.innerHTML = src;
    });
  })();
  $$("[data-typeline]").forEach(function (el) {
    var full = el.textContent;
    el.dataset.full = full;
    if (reduced) return;
    el.textContent = "";
    onReveal(el.closest("[data-reveal]"), async function () {
      await sleep(300);
      for (var i = 1; i <= full.length; i++) { el.textContent = full.slice(0, i); await sleep(38); }
    });
  });

  // ============================================== karşılaştırma: tarama çerçevesi
  var cmp = $("[data-cmp]");
  if (cmp) (function () {
    var scan = $("[data-scan]", cmp), table = $("table", cmp), th = $("thead th.us", cmp);
    var rects = $$("rect", scan);
    function place() {
      var x = table.offsetLeft + th.offsetLeft, y = table.offsetTop + th.offsetTop;
      var w = th.offsetWidth, h = table.offsetHeight - th.offsetTop;
      scan.style.left = x + "px"; scan.style.top = y + "px";
      scan.style.width = w + "px"; scan.style.height = h + "px";
      rects.forEach(function (r) { r.setAttribute("width", Math.max(0, w - 2)); r.setAttribute("height", Math.max(0, h - 2)); });
    }
    place();
    if (D.fonts && D.fonts.ready) D.fonts.ready.then(place);
    addEventListener("resize", place);
    if (!reduced) watch(cmp, function (v) { scan.style.animationPlayState = v ? "running" : "paused"; $$(".cs-run", scan).forEach(function (r) { r.style.animationPlayState = v ? "running" : "paused"; }); });
  })();

  // ================================================ mikro etkileşimler
  if (fine && !reduced) {
    $$("[data-magnet]").forEach(function (el) {
      el.addEventListener("pointermove", function (e) {
        var r = el.getBoundingClientRect();
        var dx = (e.clientX - r.left - r.width / 2) / r.width, dy = (e.clientY - r.top - r.height / 2) / r.height;
        el.style.transform = "translate(" + (dx * 10).toFixed(1) + "px," + (dy * 8).toFixed(1) + "px)";
      });
      el.addEventListener("pointerleave", function () { el.style.transform = ""; });
    });
    $$("[data-tilt]").forEach(function (el) {
      el.addEventListener("pointermove", function (e) {
        if (!el.classList.contains("in")) return;
        var r = el.getBoundingClientRect();
        var dx = (e.clientX - r.left) / r.width - 0.5, dy = (e.clientY - r.top) / r.height - 0.5;
        el.style.transition = "transform .15s ease-out, border-color .3s";
        el.style.transform = "perspective(900px) rotateX(" + (-dy * 6).toFixed(2) + "deg) rotateY(" + (dx * 7).toFixed(2) + "deg) translateY(-3px)";
      });
      el.addEventListener("pointerleave", function () { el.style.transition = ""; el.style.transform = ""; });
    });

    // ---------------------------------------------- imleç eşlikçisi
    // Sistemin imleci hiç gizlenmez; yeşil artı yanında yürür, düğme ve bağlantılara kilitlenir.
    var cc = D.createElement("div");
    cc.className = "cc"; cc.setAttribute("aria-hidden", "true");
    cc.innerHTML = '<div class="cc-x"><svg viewBox="-13 -13 26 26"><circle class="a" r="6"/><path class="a" d="M0 -12v4M0 8v4M-12 0h4M8 0h4"/><circle class="d" r="1.5"/></svg></div>' +
      '<span class="cc-c tl"></span><span class="cc-c tr"></span><span class="cc-c bl"></span><span class="cc-c br"></span><span class="cc-l"></span>';
    D.body.appendChild(cc);
    var X = $(".cc-x", cc), L = $(".cc-l", cc), C = $$(".cc-c", cc);
    var parts = [X, C[0], C[1], C[2], C[3], L].map(function (el) { return { el: el, x: -100, y: -100, vx: 0, vy: 0, tx: -100, ty: -100 }; });
    var mx = -100, my = -100, lock = null, ccRun = false, shown = false;
    function label(el) {
      var role = el.getAttribute("role") === "tab" ? "page tab" : el.tagName === "A" ? "link" : "push button";
      var name = (el.getAttribute("aria-label") || el.textContent || "").replace(/\s+/g, " ").trim();
      if (name.length > 22) name = name.slice(0, 21) + "…";
      return "[" + role + "] " + name;
    }
    function targets() {
      if (lock) {
        var r = lock.getBoundingClientRect(), g = 5;
        parts[0].tx = r.left + r.width / 2; parts[0].ty = r.top + r.height / 2;
        parts[1].tx = r.left - g; parts[1].ty = r.top - g;
        parts[2].tx = r.right + g; parts[2].ty = r.top - g;
        parts[3].tx = r.left - g; parts[3].ty = r.bottom + g;
        parts[4].tx = r.right + g; parts[4].ty = r.bottom + g;
        parts[5].tx = r.left - g; parts[5].ty = r.top - g - 4;
      } else {
        var x = mx + 22, y = my + 24;
        parts[0].tx = x; parts[0].ty = y;
        parts[1].tx = x - 10; parts[1].ty = y - 10; parts[2].tx = x + 10; parts[2].ty = y - 10;
        parts[3].tx = x - 10; parts[3].ty = y + 10; parts[4].tx = x + 10; parts[4].ty = y + 10;
        parts[5].tx = x - 10; parts[5].ty = y - 14;
      }
    }
    function ccTick() {
      targets();
      var moving = false;
      parts.forEach(function (p, i) {
        var k = i === 0 ? 0.16 : 0.2, d = 0.68;
        p.vx = p.vx * d + (p.tx - p.x) * k; p.vy = p.vy * d + (p.ty - p.y) * k;
        p.x += p.vx; p.y += p.vy;
        if (Math.abs(p.vx) + Math.abs(p.vy) + Math.abs(p.tx - p.x) + Math.abs(p.ty - p.y) > 0.2) moving = true;
        p.el.style.transform = "translate3d(" + p.x.toFixed(1) + "px," + p.y.toFixed(1) + "px,0)";
      });
      if (moving) requestAnimationFrame(ccTick); else ccRun = false;
    }
    function kick() { if (!ccRun) { ccRun = true; requestAnimationFrame(ccTick); } }
    D.addEventListener("pointermove", function (e) {
      if (e.pointerType && e.pointerType !== "mouse") return;
      mx = e.clientX; my = e.clientY;
      if (!shown) { shown = true; parts.forEach(function (p) { p.x = mx + 22; p.y = my + 24; }); cc.classList.add("shown"); }
      var t = e.target.closest ? e.target.closest('a[href], button, [role="tab"]') : null;
      if (t !== lock) {
        lock = t;
        cc.classList.toggle("lock", !!t);
        if (t) L.textContent = label(t);
      }
      kick();
    }, { passive: true });
    D.documentElement.addEventListener("mouseleave", function () { cc.classList.remove("shown"); shown = false; });
    addEventListener("scroll", function () { if (lock) kick(); }, { passive: true });
  }

  // ============================================================= replay
  var stage = $("[data-replay]");
  if (stage) (function () {
    var i18n = JSON.parse($("#replay-i18n").textContent);
    var desk = $(".desk", stage), log = $("[data-log]", stage), caption = $("[data-caption]", stage);
    var toggle = $("[data-toggle]", stage), chButtons = $$("[data-ch]", stage), agent = $("[data-agent]", desk);
    var win = function (n) { return $('[data-win="' + n + '"]', desk); };

    // Çıktılar CLI'ın gerçek biçimindedir; ref'lerdeki "…" kırpmadır.
    var CH = [
      [
        { cmd: "nexremote windows" },
        { fx: function () { tag("term"); tag("set"); tag("web"); } },
        { out: "* win:{c31f…}  org.kde.konsole  1100x780+0+0      nexwatch : claude-bin — Konsole\n" +
               "  win:{8c1e…}  systemsettings   760x540+1180+60   Appearance — System Settings\n" +
               "  win:{a41f…}  zen              880x560+1060+520  Player — Zen Browser" },
        { cmd: "nexremote terms" },
        { out: "konsole:7891:2       claude-bin     ~/nexwatch               nexwatch : claude-bin" }
      ],
      [
        { fx: function () { untagAll(); lit("term", true); } },
        { cmd: "nexremote term-read konsole:7891:2" },
        { fx: function () { $$(".hl-target", desk).forEach(function (p) { p.classList.add("read"); }); } },
        { out: "● Running test suite…\n  tests/test_api.py ........F.F\n✗ 2 failed · test_login, test_refresh\nShould I fix them? (y/n, or tell me)\n›" }
      ],
      [
        { fx: function () { lit("term", true); } },
        { cmd: function () { return 'nexremote term-send konsole:7891:2 "' + i18n.termMsg + '" --enter --json'; } },
        { typeTerm: true },
        { out: '{\n  "ok": true,\n  "ref": "konsole:7891:2"\n}' },
        { fx: function () { $("[data-reply]", desk).classList.add("on"); } }
      ],
      [
        { fx: function () { lit("term", false); lit("set", true); tag("set"); } },
        { cmd: 'nexremote find "Dark" --window win:{8c1e…}' },
        { fx: function () { win("set").classList.add("tagged-el"); $("[data-dark-row]", desk).classList.add("found"); } },
        { out: "atspi:4:0/2/7\t[checkbox] Dark  <toggle,click>" },
        { cmd: "nexremote click atspi:4:0/2/7 --json" },
        { fx: function () { $("[data-switch]", desk).classList.add("on"); win("set").classList.add("dark"); } },
        { out: '{\n  "ok": true,\n  "ref": "atspi:4:0/2/7"\n}' }
      ],
      [
        { fx: function () { untagAll(); lit("set", false); lit("web", true); tag("web"); } },
        { cmd: "nexremote screenshot --window win:{a41f…} --max-width 1568 --json" },
        { fx: function () { flash($("[data-shutter]", desk)); } },
        { out: '{\n  "path": "/run/user/1000/nexremote-shot.png",\n  "width": 880,\n  "height": 560,\n  …\n  "scale": 1.0,\n  "origin": [1060, 520]\n}' },
        { cmd: "nexremote click 440 190 --from-shot --json" },
        { cursorTo: "[data-play]" },
        { fx: function () { $("[data-play]", desk).classList.add("on"); $("[data-progress]", desk).classList.add("run"); } },
        { out: '{\n  "ok": true,\n  "x": 1500,\n  "y": 710,\n  "button": "left"\n}' },
        { cursorHome: true }
      ]
    ];

    function tag(n) { win(n).classList.add("tagged"); }
    function untagAll() { $$(".win", desk).forEach(function (w) { w.classList.remove("tagged", "tagged-el"); }); }
    function lit(n, on) { win(n).classList.toggle("lit", on); }
    function flash(el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }

    function resetDesk() {
      $$(".win", desk).forEach(function (w) { w.classList.remove("lit", "tagged", "tagged-el", "dark"); });
      $$(".read, .found, .on, .run", desk).forEach(function (el) {
        if (el !== agent) el.classList.remove("read", "found", "on", "run");
      });
      $("[data-typed]", desk).textContent = "";
      agent.classList.remove("on", "click");
      placeAgent(0.5, 0.92, true);
      log.textContent = "";
    }
    function placeAgent(fx, fy, instant) {
      var r = desk.getBoundingClientRect();
      agent.style.transition = instant ? "none" : "transform .7s cubic-bezier(.45,.05,.25,1), opacity .2s";
      agent.style.transform = "translate(" + (fx * r.width) + "px," + (fy * r.height) + "px)";
    }
    function agentTo(sel, instant) {
      var r = desk.getBoundingClientRect(), t = $(sel, desk).getBoundingClientRect();
      placeAgent((t.left + t.width / 2 - r.left) / r.width, (t.top + t.height / 2 - r.top) / r.height, instant);
    }

    // her çalıştırmanın kimliği var; yenisi başlayınca eskisi durur
    var run = 0, playing = false, current = 0, userPaused = false, ended = false;
    function wait(ms, id) {
      return new Promise(function (res, rej) { setTimeout(function () { id === run ? res() : rej("cancel"); }, ms); });
    }
    function line(cls, text) {
      var el = D.createElement("div");
      el.className = cls; el.textContent = text;
      log.appendChild(el); log.scrollTop = log.scrollHeight;
      return el;
    }
    async function typeInto(el, text, id, speed) {
      if (!speed) { el.textContent = text; return; }
      for (var i = 1; i <= text.length; i++) {
        el.textContent = text.slice(0, i);
        log.scrollTop = log.scrollHeight;
        await wait(speed, id);
      }
    }
    async function playChapter(n, id, animate) {
      var ops = CH[n];
      for (var k = 0; k < ops.length; k++) {
        var op = ops[k];
        if (op.cmd) {
          var text = typeof op.cmd === "function" ? op.cmd() : op.cmd;
          await typeInto(line("cmd", ""), text, id, animate ? 22 : 0);
          if (animate) await wait(380, id);
        } else if (op.out) {
          line("out", op.out);
          if (animate) await wait(900, id);
        } else if (op.fx) {
          op.fx();
          if (animate) await wait(450, id);
        } else if (op.typeTerm) {
          await typeInto($("[data-typed]", desk), i18n.termMsg, id, animate ? 30 : 0);
          if (animate) await wait(300, id);
        } else if (op.cursorTo) {
          agent.classList.add("on");
          agentTo(op.cursorTo, !animate);
          if (animate) await wait(800, id);
          agent.classList.remove("click"); void agent.offsetWidth; agent.classList.add("click");
          if (animate) await wait(350, id);
        } else if (op.cursorHome) {
          if (animate) { await wait(500, id); placeAgent(0.5, 0.92); await wait(700, id); }
          agent.classList.remove("on");
        }
      }
    }
    function select(n) {
      current = n;
      chButtons.forEach(function (b, i) { b.setAttribute("aria-selected", i === n ? "true" : "false"); });
      caption.textContent = i18n.captions[n];
    }
    // Bölüm n'e git: öncekiler anında uygulanır, n oynatılır (veya anında gösterilir).
    async function goTo(n, animate) {
      var id = ++run;
      resetDesk();
      select(n);
      try {
        for (var i = 0; i < n; i++) await playChapter(i, id, false);
        await playChapter(n, id, animate);
        if (!animate) return;
        for (var j = n + 1; j < CH.length && playing; j++) {
          await wait(1400, id);
          select(j);
          await playChapter(j, id, true);
        }
        await wait(400, id);
        ended = true;
        setPlaying(false, true);
      } catch (e) { if (e !== "cancel") throw e; }
    }
    function setPlaying(on, end) {
      playing = on;
      toggle.textContent = on ? toggle.dataset.pauseLabel : end ? toggle.dataset.againLabel : toggle.dataset.playLabel;
    }
    toggle.addEventListener("click", function () {
      if (playing) { run++; userPaused = true; setPlaying(false); return; }
      var restart = ended;
      ended = false; userPaused = false;
      setPlaying(true);
      goTo(restart ? 0 : current, true);
    });
    chButtons.forEach(function (b, i) {
      b.addEventListener("click", function () {
        ended = false;
        if (reduced) { run++; setPlaying(false); goTo(i, false); return; }
        userPaused = false;
        setPlaying(true);
        goTo(i, true);
      });
    });
    addEventListener("resize", function () { if (!playing) agent.classList.remove("on"); });

    // İlk durum: 1. bölüm hazır. Hareket isteyenlerde görününce oynar, ekrandan çıkınca durur.
    goTo(0, false);
    if (!reduced && hasIO) {
      new IntersectionObserver(function (entries) {
        var v = entries[entries.length - 1].isIntersecting;
        if (v && !playing && !userPaused && !ended) { setPlaying(true); goTo(current, true); }
        else if (!v && playing) { run++; setPlaying(false); }
      }, { threshold: 0.35 }).observe(stage);
    }
  })();

  schedule();
})();
