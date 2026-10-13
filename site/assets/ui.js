/* NexRemote site — etkileşim katmanı
   Sekmeler, scroll-reveal, kod kopyalama, sticky nav. Bağımlılık yok. */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------- sekmeler */
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  function selectTab(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.classList.toggle("is-active", on);
    });
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { selectTab(tab); });
    // klavye: sol/sağ ok sekmeler arası gezinir (WAI-ARIA tab kalıbı)
    tab.addEventListener("keydown", function (e) {
      var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      var next = tabs[(i + d + tabs.length) % tabs.length];
      next.focus();
      selectTab(next);
    });
  });

  /* ------------------------------------------------------- kod kopyala */
  document.querySelectorAll("[data-copy] .copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var block = btn.closest("[data-copy]");
      var code = block && block.querySelector("code");
      if (!code) return;
      var text = code.innerText;
      var done = function () {
        var prev = btn.textContent;
        btn.textContent = "copied";
        btn.classList.add("is-copied");
        setTimeout(function () {
          btn.textContent = prev;
          btn.classList.remove("is-copied");
        }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(fallback);
      } else {
        fallback();
      }
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) { /* sessizce geç */ }
        document.body.removeChild(ta);
      }
    });
  });

  /* ------------------------------------------------------- sticky nav */
  var nav = document.querySelector(".nav");
  if (nav) {
    var onScroll = function () {
      nav.classList.toggle("is-stuck", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ------------------------------------------------- scroll-reveal */
  var targets = document.querySelectorAll(
    ".hero-copy, .hero-panel, .band-left, .band-right, .section-title, " +
    ".section-sub, .how-item, .arch-diagram, .tabs, .tab-panels, " +
    ".install-main, .deps, .limits-list > div, .foot"
  );
  targets.forEach(function (el) { el.setAttribute("data-reveal", ""); });

  if (reduced || !("IntersectionObserver" in window)) {
    targets.forEach(function (el) { el.classList.add("is-in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          // kardeşler arasında hafif kademe — abartısız
          e.target.style.transitionDelay =
            Math.min(e.target.dataset.i || 0, 4) * 55 + "ms";
          e.target.classList.add("is-in");
          io.unobserve(e.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    // her grubun kendi içindeki sırasını ata
    [".how-item", ".limits-list > div"].forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el, i) {
        el.dataset.i = i;
      });
    });
    targets.forEach(function (el) { io.observe(el); });
  }

  /* ------------------------------------------------ dil seçimi (TR/EN) */
  // Kullanıcı dil değiştiriciye tıklarsa tercihi sakla (kişisel site
  // konvansiyonu: localStorage.site-lang). Otomatik yönlendirme bu anahtarı
  // okur ve bir daha zorlamaz.
  document.querySelectorAll(".lang-switch").forEach(function (a) {
    a.addEventListener("click", function () {
      try { localStorage.setItem("site-lang", a.dataset.lang || ""); } catch (e) {}
    });
  });

  /* --------------------------------------- (kaldırıldı) GitHub linkleri
     GitHub askıya alındığı sürece indirmeler doğrudan /download/*.zip
     üzerinden veriliyor; index.html'de data-gh yok. Repo açıldığında
     aşağıdaki bloğu geri açıp REPO değerini güncelle. */
  // var REPO = "https://github.com/<you>/nexremote";
  // document.querySelectorAll("[data-gh]").forEach(function (a) {
  //   a.setAttribute("href", REPO);
  //   if (!a.rel) a.rel = "noopener";
  // });
})();
