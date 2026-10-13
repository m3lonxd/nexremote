/* =========================================================================
   NexRemote — hero terminal animasyonu ("canlı ajan oturumu")

   #hero-term içine gerçek bir NexRemote oturumunu adım adım "yazar".
   Sadece daktilo efekti + yanıp sönen imleç: konfeti, glow, renk geçişi YOK.
   Terminallerin doğallığı korunur.

   Sözleşme (HTML tarafı):
     <pre class="term" id="hero-term" aria-live="polite"></pre>
   Renk sınıfları style.css'ten gelir: .c-prompt (yeşil), .c-dim (soluk),
   .caret (yanıp sönen imleç).

   Davranış:
     - Karakter karakter daktilo (~18-26 ms), çıktı satırları satır satır daha hızlı.
     - Dizi bitince ~4 sn bekler, ekranı temizler ve baştan başlar (sonsuz döngü).
     - prefers-reduced-motion: reduce → animasyon YOK, dizi anında statik basılır.
     - IntersectionObserver: panel ekranda değilken animasyon duraklar (CPU boşa yanmasın).
     - XSS güvenli: tüm metinler textContent ile yazılır; HTML string enjeksiyonu yok.
     - aria-live="polite": daktilo kopyası aria-hidden, tam metin bir kez sessizce duyurulur.
   ========================================================================= */
(() => {
  "use strict";

  const term = document.getElementById("hero-term");
  if (!term) return;

  /* ------------------------------------------------------------ ayarlar */
  const TYPE_MIN = 18;   // ms / karakter (daktilo alt sınır)
  const TYPE_MAX = 26;   // ms / karakter (daktilo üst sınır)
  const OUT_MIN  = 110;  // ms — çıktı satırları arası gecikme
  const OUT_MAX  = 190;
  const GAP_MS   = 260;  // adımlar arası boş satır gecikmesi
  const HOLD_MS  = 4000; // dizi bitince bekleme
  const WIPE_MS  = 26;   // temizleme sırasında düğüm başına gecikme

  const motionMq = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ------------------------------------------------------------ senaryo */
  /* Bu dizi gerçek bir oturumun çıktısıdır — içerik değişmez. */
  const STEPS = [
    {
      cmd: "nexremote observe",
      out: [
        { text: "  windows  · 5",                   cls: "c-dim" },
        { text: "  gui      · AT-SPI active",        cls: "c-dim" },
        { text: "  tui      · tmux session 'build'", cls: "c-dim" }
      ]
    },
    {
      cmd: 'nexremote find "Save"',
      out: [
        { text: "  atspi:9:0/0/12  [button] Save  <click>", cls: "c-dim" }
      ]
    },
    {
      cmd: "nexremote click atspi:9:0/0/12",
      out: [
        // "clicked" olumlu sonuç: vurgu yeşili (.c-prompt ile aynı renk)
        { text: "  clicked", cls: "c-prompt" }
      ]
    },
    {
      cmd: 'nexremote run "make test" --session build',
      out: [
        { text: "  ... 42 passed", cls: "c-dim" },
        { text: "  build ok",      cls: "c-dim" }
      ]
    },
    {
      // Son adım: çıktı yok — imleç yanıp sönerek bekler.
      cmd: "nexremote screen --session build",
      out: []
    }
  ];

  /* Ekran okuyucu için dizinin tam düz metni (bir kez duyurulur). */
  const PLAIN_TEXT = STEPS
    .map((s) => ["$ " + s.cmd].concat(s.out.map((o) => o.text)).join("\n"))
    .join("\n");

  /* ------------------------------------------------------------ DOM parçaları */
  /* Daktilo kopyası aria-hidden: ekran okuyucuyu harf harf boğmasın. */
  const stage = document.createElement("div");
  stage.setAttribute("aria-hidden", "true");

  const caret = document.createElement("span");
  caret.className = "caret";

  /* Metin parçası — her zaman textContent; ham HTML asla yazılmaz. */
  function makeSpan(text, cls) {
    const el = document.createElement("span");
    if (cls) el.className = cls;
    if (text) el.textContent = text;
    return el;
  }

  /* Satır sonu. `.term` white-space: pre-wrap olduğu için "\n" gerçek satır kırar. */
  function newline() {
    stage.appendChild(document.createTextNode("\n"));
  }

  /* İmleci her zaman sahnenin en sonuna taşır (satırın ucu). */
  function caretToEnd() {
    stage.appendChild(caret);
  }

  /* Hedef <pre> içine iskeleti kur: sahne + görünmez tam metin. */
  function mount() {
    const sr = document.createElement("span");
    // Görsel olarak gizli ama erişilebilirlik ağacında okunur (sr-only deseni).
    sr.style.cssText =
      "position:absolute;width:1px;height:1px;margin:-1px;padding:0;" +
      "overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0;";
    sr.textContent = PLAIN_TEXT;
    term.replaceChildren(stage, sr);
  }

  /* ------------------------------------------------------------ zamanlama */
  const rand = (min, max) => min + Math.random() * (max - min);

  const CANCELLED = Symbol("iptal");   // kurallı döngü kesme sinyali
  let paused = false;                  // sekme/panel görünür değil mi?
  let epoch = 0;                       // her turda artar; eski zincirleri iptal eder

  function isCancelled(ep) { return ep !== epoch; }

  /**
   * Duraklatılabilir uyku. Duraklamadayken geçen süre hesaba katılmaz,
   * böylece panel ekrana döndüğünde animasyon kaldığı yerden devam eder.
   */
  function sleep(ms, ep) {
    return new Promise((resolve, reject) => {
      let left = ms;
      let last = performance.now();

      const tick = () => {
        if (isCancelled(ep)) return reject(CANCELLED);
        const now = performance.now();
        if (!paused) left -= now - last;
        last = now;
        if (left <= 0) return resolve();
        const wait = paused ? 150 : Math.max(8, Math.min(left, 24));
        setTimeout(tick, wait);
      };

      const wait = paused ? 150 : Math.max(8, Math.min(left, 24));
      setTimeout(tick, wait);
    });
  }

  /* ------------------------------------------------------------ yazma eylemleri */

  /* `$ komut` satırını karakter karakter yazar. Prompt yeşil (.c-prompt). */
  async function typeCommand(cmd, ep) {
    newline();
    const body = makeSpan("", null);
    stage.append(makeSpan("$ ", "c-prompt"), body);
    caretToEnd();

    for (const ch of cmd) {
      if (isCancelled(ep)) throw CANCELLED;
      body.textContent += ch;
      await sleep(rand(TYPE_MIN, TYPE_MAX), ep);
    }
  }

  /* Çıktı satırını tek seferde basar; imleci satır sonuna taşır. */
  async function printOut(text, cls, ep, delay) {
    newline();
    if (text) stage.appendChild(makeSpan(text, cls));
    caretToEnd();
    await sleep(delay === undefined ? rand(OUT_MIN, OUT_MAX) : delay, ep);
  }

  /* Dizinin tamamını sırayla oynatır. */
  async function play(ep) {
    for (let i = 0; i < STEPS.length; i++) {
      const step = STEPS[i];

      await typeCommand(step.cmd, ep);
      await sleep(220, ep);                      // komutun "çalıştığı" kısa an

      for (const line of step.out) {
        if (isCancelled(ep)) throw CANCELLED;
        await printOut(line.text, line.cls, ep);
      }

      // Adımlar arası boş satır (son adımda yok — orada imleç bekler).
      if (i < STEPS.length - 1) await printOut("", null, ep, GAP_MS);
    }
  }

  /* Ekranı alttan üste doğru temizler (hızlı "clear" hissi). */
  async function wipe(ep) {
    while (stage.lastChild) {
      if (isCancelled(ep)) throw CANCELLED;
      stage.removeChild(stage.lastChild);
      await sleep(WIPE_MS, ep);
    }
  }

  /* Sonsuz oturum döngüsü: oynat → bekle → temizle → baştan. */
  async function loop(ep) {
    try {
      for (;;) {
        if (isCancelled(ep)) return;
        await play(ep);
        await sleep(HOLD_MS, ep);
        await wipe(ep);
        await sleep(320, ep);
      }
    } catch (err) {
      if (err !== CANCELLED) throw err;   // iptal normal akış; gerçek hatayı yutma
    }
  }

  /* ------------------------------------------------------------ reduced motion */
  /* Hareket azaltma açıkken animasyon yok: tüm dizi anında, statik basılır. */
  function renderStatic() {
    stage.replaceChildren();

    STEPS.forEach((step, i) => {
      if (i > 0) newline();                       // adımlar arası boş satır

      stage.append(makeSpan("$ ", "c-prompt"), makeSpan(step.cmd, null));

      for (const line of step.out) {
        newline();
        stage.appendChild(makeSpan(line.text, line.cls));
      }
    });

    caretToEnd();                                 // statik imleç: CSS onu söndürür
  }

  /* ------------------------------------------------------------ görünürlük */
  /* Panel ekranda değilken duraklat: boşa CPU yakmaz, dönünce devam eder. */
  let onScreen = true;

  function syncPause() {
    paused = !onScreen || document.hidden;
  }

  function observeVisibility() {
    if (typeof IntersectionObserver !== "function") return;  // yoksa hep çalışır
    const io = new IntersectionObserver((entries) => {
      onScreen = entries[entries.length - 1].isIntersecting;
      syncPause();
    }, { threshold: 0 });
    io.observe(term);
  }

  document.addEventListener("visibilitychange", syncPause);

  /* ------------------------------------------------------------ başlat */
  mount();

  if (motionMq.matches) {
    renderStatic();

    // Kullanıcı tercihi sonradan "reduce" olursa: durdur, statik bas.
    if (typeof motionMq.addEventListener === "function") {
      motionMq.addEventListener("change", (e) => {
        if (!e.matches) return;
        epoch++;                 // çalışan zinciri iptal et
        paused = false;
        renderStatic();
      });
    }
  } else {
    observeVisibility();
    syncPause();                 // sekme arka planda açıldıysa bekle
    loop(++epoch);
  }
})();
