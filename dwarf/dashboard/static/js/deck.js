/* DWARF Basic view — "Evidence Deck" behaviour. Runs only under body[data-ui="basic"].
 * Progressive enhancement: every card is a real link/button or has a plain
 * fallback; this script adds foil tilt, flipping, binder filters and copy. */
(function () {
  var body = document.body;
  if (!body || body.getAttribute("data-ui") !== "basic") return;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia && window.matchMedia("(pointer: fine)").matches;

  /* Deterministic foil hue/angle from a seed string (e.g. the manifest hash). */
  function seedFoil(el) {
    var s = el.getAttribute("data-dk-seed") || el.textContent || "";
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    el.style.setProperty("--h", String(h % 360));
    el.style.setProperty("--ang", (95 + (h >> 9) % 50) + "deg");
    el.style.setProperty("--sw", String(8 + (h >> 17) % 6));
  }
  document.querySelectorAll(".dk-holo").forEach(seedFoil);

  /* Damped tilt + glare for holo cards (fine pointers only, motion allowed). */
  if (!reduce && finePointer) {
    document.querySelectorAll(".dk-holo").forEach(function (card) {
      var tx = 0, ty = 0, cx = 0, cy = 0, mx = 50, my = 50, raf = 0, active = false;
      function step() {
        cx += (tx - cx) * 0.14; cy += (ty - cy) * 0.14;
        card.style.transform = "rotateX(" + cy.toFixed(2) + "deg) rotateY(" + cx.toFixed(2) + "deg)";
        card.style.setProperty("--mx", mx.toFixed(1)); card.style.setProperty("--my", my.toFixed(1));
        if (active || Math.abs(tx - cx) > 0.05 || Math.abs(ty - cy) > 0.05) raf = requestAnimationFrame(step);
        else { raf = 0; if (!active) card.style.transform = ""; }
      }
      function kick() { if (!raf) raf = requestAnimationFrame(step); }
      card.addEventListener("pointermove", function (e) {
        var r = card.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
        tx = (px - 0.5) * 16; ty = (0.5 - py) * 12; mx = px * 100; my = py * 100; active = true; kick();
      });
      card.addEventListener("pointerleave", function () { tx = 0; ty = 0; mx = 50; my = 50; active = false; kick(); });
    });
  }

  /* Flip cards: click / Enter / Space toggles. Links inside a face still work. */
  document.querySelectorAll("[data-dk-flip]").forEach(function (card) {
    function toggle() {
      var on = card.classList.toggle("is-flipped");
      card.setAttribute("aria-pressed", on ? "true" : "false");
    }
    card.addEventListener("click", function (e) {
      if (e.target.closest("a, button:not([data-dk-flip]), input, code")) return;
      toggle();
    });
    card.addEventListener("keydown", function (e) {
      if (e.target !== card) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
  });

  /* Copy buttons. */
  document.querySelectorAll("[data-dk-copy]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var text = btn.getAttribute("data-dk-copy");
      var done = function () { var o = btn.textContent; btn.textContent = "copied"; setTimeout(function () { btn.textContent = o; }, 1200); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, function () { btn.textContent = "select"; });
      else { btn.textContent = "select"; }
    });
  });

  /* Runs hand: hover/focus readout. */
  var readout = document.querySelector("[data-dk-readout]");
  if (readout) {
    document.querySelectorAll(".dk-mini").forEach(function (m) {
      var show = function () { readout.textContent = m.getAttribute("aria-label") || ""; };
      m.addEventListener("mouseenter", show); m.addEventListener("focus", show);
    });
  }

  /* Binder: tier / kind filters + search, and a detail dialog. */
  var binder = document.querySelector("[data-dk-binder]");
  if (binder) {
    var state = { tier: "all", kind: "all", q: "" };
    var slots = Array.prototype.slice.call(binder.querySelectorAll("[data-dk-slot]"));
    var count = binder.querySelector("[data-dk-count]");
    function apply() {
      var shown = 0;
      slots.forEach(function (s) {
        var ok = (state.tier === "all" || s.getAttribute("data-tier") === state.tier) &&
                 (state.kind === "all" || s.getAttribute("data-kind") === state.kind) &&
                 (!state.q || (s.getAttribute("data-search") || "").indexOf(state.q) !== -1);
        s.hidden = !ok; if (ok) shown++;
      });
      binder.querySelectorAll("[data-dk-group]").forEach(function (g) {
        g.hidden = !g.querySelector("[data-dk-slot]:not([hidden])");
      });
      if (count) count.textContent = shown + " of " + slots.length + " shown";
    }
    binder.querySelectorAll("[data-dk-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.getAttribute("data-dk-filter");
        state[key] = btn.getAttribute("data-value");
        btn.parentNode.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
        apply();
      });
    });
    var search = binder.querySelector("[data-dk-search]");
    if (search) search.addEventListener("input", function () { state.q = search.value.trim().toLowerCase(); apply(); });
    var initial = (location.hash || "").match(/^#tier-(verified|mapped|gap)$/);
    if (initial) { var b = binder.querySelector('[data-dk-filter="tier"][data-value="' + initial[1] + '"]'); if (b) b.click(); }

    var dialog = document.querySelector("[data-dk-dialog]");
    if (dialog && typeof dialog.showModal === "function") {
      var dbody = dialog.querySelector("[data-dk-dialog-body]");
      slots.forEach(function (s) {
        s.addEventListener("click", function (e) {
          var target = document.getElementById(s.getAttribute("href").slice(1));
          if (!target) return;
          e.preventDefault();
          var clone = target.cloneNode(true);
          clone.open = true; clone.removeAttribute("id");
          dbody.innerHTML = ""; dbody.appendChild(clone);
          dialog.showModal();
        });
      });
      dialog.addEventListener("click", function (e) { if (e.target === dialog || e.target.closest("[data-dk-close]")) dialog.close(); });
    }
  }
})();
