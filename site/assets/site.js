/* NexRemote — site davranışı: tema, kopyala, sekmeler, dil, oturum tekrarı. */
(function () {
  "use strict";

  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* özel pencere */ } }
  };
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------- dil
  // İlk ziyarette Türkçe tarayıcıyı /tr/'ye yönlendir; seçim yapıldıysa ona uy.
  var lang = document.documentElement.lang;
  var pref = store.get("nr-lang");
  if (!pref && lang === "en" && /^tr\b/i.test((navigator.languages && navigator.languages[0]) || navigator.language || "")) {
    location.replace("/tr/" + location.hash);
    return;
  }
  document.querySelectorAll("[data-lang]").forEach(function (a) {
    a.addEventListener("click", function () { store.set("nr-lang", a.dataset.lang); });
  });

  // ------------------------------------------------------------- tema
  var themeBtn = document.querySelector(".theme");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var cur = document.documentElement.dataset.theme ||
        (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      var next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      store.set("nr-theme", next);
    });
  }

  // ---------------------------------------------------------- kopyala
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    var label = btn.textContent;
    btn.addEventListener("click", function () {
      var el = document.querySelector(btn.dataset.copy);
      if (!el) return;
      var text = el.textContent;
      var done = function () {
        btn.textContent = btn.dataset.done || label;
        setTimeout(function () { btn.textContent = label; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { selectText(el); });
      } else {
        selectText(el);
      }
    });
  });
  function selectText(el) {
    var r = document.createRange(); r.selectNodeContents(el);
    var s = getSelection(); s.removeAllRanges(); s.addRange(r);
  }

  // ---------------------------------------------------------- sekmeler
  document.querySelectorAll("[data-tabs]").forEach(function (root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
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
  });

  // ------------------------------------------------------------ replay
  var stage = document.querySelector("[data-replay]");
  if (!stage) return;
  var i18n = JSON.parse(document.getElementById("replay-i18n").textContent);
  var desk = stage.querySelector(".desk");
  var log = stage.querySelector("[data-log]");
  var caption = stage.querySelector("[data-caption]");
  var toggle = stage.querySelector("[data-toggle]");
  var chButtons = Array.prototype.slice.call(stage.querySelectorAll("[data-ch]"));
  var agent = desk.querySelector("[data-agent]");
  var win = function (n) { return desk.querySelector('[data-win="' + n + '"]'); };

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
      { fx: function () { desk.querySelectorAll(".hl-target").forEach(function (p) { p.classList.add("read"); }); } },
      { out: "● Running test suite…\n  tests/test_api.py ........F.F\n✗ 2 failed · test_login, test_refresh\nShould I fix them? (y/n, or tell me)\n›" }
    ],
    [
      { fx: function () { lit("term", true); } },
      { cmd: function () { return 'nexremote term-send konsole:7891:2 "' + i18n.termMsg + '" --enter --json'; } },
      { typeTerm: true },
      { out: '{\n  "ok": true,\n  "ref": "konsole:7891:2"\n}' },
      { fx: function () { desk.querySelector("[data-reply]").classList.add("on"); } }
    ],
    [
      { fx: function () { lit("term", false); lit("set", true); tag("set"); } },
      { cmd: 'nexremote find "Dark" --window win:{8c1e…}' },
      { fx: function () { win("set").classList.add("tagged-el"); desk.querySelector("[data-dark-row]").classList.add("found"); } },
      { out: "atspi:4:0/2/7\t[checkbox] Dark  <toggle,click>" },
      { cmd: "nexremote click atspi:4:0/2/7 --json" },
      { fx: function () { desk.querySelector("[data-switch]").classList.add("on"); win("set").classList.add("dark"); } },
      { out: '{\n  "ok": true,\n  "ref": "atspi:4:0/2/7"\n}' }
    ],
    [
      { fx: function () { untagAll(); lit("set", false); lit("web", true); tag("web"); } },
      { cmd: "nexremote screenshot --window win:{a41f…} --max-width 1568 --json" },
      { fx: function () { flash(desk.querySelector("[data-shutter]")); } },
      { out: '{\n  "path": "/run/user/1000/nexremote-shot.png",\n  "width": 880,\n  "height": 560,\n  …\n  "scale": 1.0,\n  "origin": [1060, 520]\n}' },
      { cmd: "nexremote click 440 190 --from-shot --json" },
      { cursorTo: "[data-play]" },
      { fx: function () {
          desk.querySelector("[data-play]").classList.add("on");
          desk.querySelector("[data-progress]").classList.add("run");
        } },
      { out: '{\n  "ok": true,\n  "x": 1500,\n  "y": 710,\n  "button": "left"\n}' },
      { cursorHome: true }
    ]
  ];

  function tag(n) { win(n).classList.add("tagged"); }
  function untagAll() { desk.querySelectorAll(".win").forEach(function (w) { w.classList.remove("tagged", "tagged-el"); }); }
  function lit(n, on) { win(n).classList.toggle("lit", on); }
  function flash(el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }

  function resetDesk() {
    desk.querySelectorAll(".win").forEach(function (w) { w.classList.remove("lit", "tagged", "tagged-el", "dark"); });
    desk.querySelectorAll(".read, .found, .on, .run").forEach(function (el) {
      if (el !== agent) el.classList.remove("read", "found", "on", "run");
    });
    desk.querySelector("[data-typed]").textContent = "";
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
    var r = desk.getBoundingClientRect(), t = desk.querySelector(sel).getBoundingClientRect();
    placeAgent((t.left + t.width / 2 - r.left) / r.width, (t.top + t.height / 2 - r.top) / r.height, instant);
  }

  // --- zamanlama: her çalıştırmanın kimliği var; yenisi başlayınca eskisi durur
  var run = 0, playing = false, current = 0;
  function sleep(ms, id) {
    return new Promise(function (res, rej) {
      setTimeout(function () { id === run ? res() : rej("cancel"); }, ms);
    });
  }

  function line(cls, text) {
    var el = document.createElement("div");
    el.className = cls; el.textContent = text;
    log.appendChild(el); log.scrollTop = log.scrollHeight;
    return el;
  }

  async function typeInto(el, text, id, speed) {
    if (!speed) { el.textContent = text; return; }
    for (var i = 1; i <= text.length; i++) {
      el.textContent = text.slice(0, i);
      log.scrollTop = log.scrollHeight;
      await sleep(speed, id);
    }
  }

  async function playChapter(n, id, animate) {
    var ops = CH[n];
    for (var k = 0; k < ops.length; k++) {
      var op = ops[k];
      if (op.cmd) {
        var text = typeof op.cmd === "function" ? op.cmd() : op.cmd;
        await typeInto(line("cmd", ""), text, id, animate ? 22 : 0);
        if (animate) await sleep(380, id);
      } else if (op.out) {
        line("out", op.out);
        if (animate) await sleep(900, id);
      } else if (op.fx) {
        op.fx();
        if (animate) await sleep(450, id);
      } else if (op.typeTerm) {
        await typeInto(desk.querySelector("[data-typed]"), i18n.termMsg, id, animate ? 30 : 0);
        if (animate) await sleep(300, id);
      } else if (op.cursorTo) {
        agent.classList.add("on");
        agentTo(op.cursorTo, !animate);
        if (animate) await sleep(800, id);
        agent.classList.remove("click"); void agent.offsetWidth; agent.classList.add("click");
        if (animate) await sleep(350, id);
      } else if (op.cursorHome) {
        if (animate) { await sleep(500, id); placeAgent(0.5, 0.92); await sleep(700, id); }
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
        await sleep(1400, id);
        select(j);
        await playChapter(j, id, true);
      }
      await sleep(400, id);
      setPlaying(false, true);
    } catch (e) { if (e !== "cancel") throw e; }
  }

  function setPlaying(on, ended) {
    playing = on;
    toggle.textContent = on ? toggle.dataset.pauseLabel : ended ? toggle.dataset.againLabel : toggle.dataset.playLabel;
  }

  toggle.addEventListener("click", function () {
    if (playing) { run++; setPlaying(false); return; }
    var restart = toggle.textContent === toggle.dataset.againLabel;
    setPlaying(true);
    goTo(restart ? 0 : current, true);
  });
  chButtons.forEach(function (b, i) {
    b.addEventListener("click", function () {
      if (reduced) { run++; setPlaying(false); goTo(i, false); return; }
      setPlaying(true);
      goTo(i, true);
    });
  });

  window.addEventListener("resize", function () { if (!playing) agent.classList.remove("on"); });

  // İlk durum: hareket istemeyenler için 1. bölüm hazır; diğerleri görünce bir kez oynar.
  goTo(0, false);
  if (!reduced && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) {
        io.disconnect();
        setPlaying(true);
        goTo(0, true);
      }
    }, { threshold: 0.35 });
    io.observe(stage);
  }
})();
