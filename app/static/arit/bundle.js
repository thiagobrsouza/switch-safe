/* @ds-bundle: {"format":4,"namespace":"ARIT","components":[{"name":"Navbar"},{"name":"Sidebar"},{"name":"RadialMenu"},{"name":"Button"},{"name":"FormField"},{"name":"Card"},{"name":"Chip"},{"name":"DashboardShell"}]} */
(function () {
  "use strict";
  var SYM = '<path fill="#00df82" d="M28.96,17.33v14.31l-17.38,11.58H0v-25.64C0,7.92,7.92,0,17.59,0h17.16v11.58h-17.16c-3.28,0-6.01,2.72-6.01,6.01v11.78l17.38-12.04Z"/><rect fill="#00df82" x="34.76" y="11.58" width="11.58" height="31.65"/>';
  function logo(opts) {
    opts = opts || {};
    var c = opts.onDark === false ? "#042222" : "#ffffff";
    if (opts.symbolOnly) return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 46.34 43.23" role="img" aria-label="AR IT">' + SYM + '</svg>';
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 184.88 43.23" role="img" aria-label="AR IT">' + '<path fill="'+c+'" d="M71.22,25.09h19.42v-5.13c0-2.64-1.38-3.74-4.56-3.74h-10.3c-3.19,0-4.56,1.1-4.56,3.74v5.13ZM95.69,19.96v23.27h-5.04v-13.51h-19.42v13.51h-5.04v-23.27c0-5.35,4.27-8.38,8.38-8.38h12.75c4.11,0,8.38,3.02,8.38,8.38"/><path fill="'+c+'" d="M120.36,28.07c3.35,0,4.41-3.17,4.41-5.81,0-3.75-2.09-6.04-4.34-6.04h-13.59v11.86h13.51s0,0,0,0ZM130.62,36.45v6.79h-5.04v-4.42c0-4.05-1.33-6.1-5.27-6.1-.02,0-9.32,0-13.45,0v10.52h-5.04V11.58h19.75c4.96,0,8.38,3.43,8.38,10.45,0,3.52-.96,6.06-2.5,7.78,2.06,1.63,3.19,4.17,3.19,6.63"/><rect fill="'+c+'" x="144.55" y="11.58" width="5.04" height="31.65"/><polygon fill="'+c+'" points="184.88 16.22 171.58 16.22 171.58 43.23 166.54 43.23 166.54 16.22 153.23 16.22 153.23 11.58 184.88 11.58 184.88 16.22"/>' + SYM + '</svg>';
  }

  /* Menu lateral: expande submenus (aria-expanded) e recolhe a barra. */
  function sidebar(root, opts) {
    opts = opts || {};
    root.querySelectorAll("[aria-expanded]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.setAttribute("aria-expanded", btn.getAttribute("aria-expanded") === "true" ? "false" : "true");
      });
    });
    root.querySelectorAll(".ar-nav-item[href], .ar-nav-sub .ar-nav-item").forEach(function (a) {
      a.addEventListener("click", function (e) {
        if (opts.preventNav) e.preventDefault();
        root.querySelectorAll(".ar-nav-item").forEach(function (x) { x.classList.remove("is-active"); x.removeAttribute("aria-current"); });
        a.classList.add("is-active"); a.setAttribute("aria-current", "page");
      });
    });
    return {
      collapse: function (v) { root.classList.toggle("ar-sidebar--collapsed", v === undefined ? !root.classList.contains("ar-sidebar--collapsed") : !!v); }
    };
  }

  /* Menu pizza: items = [{icon, label, onSelect}] (3 a 8 fatias). */
  function radialMenu(root, items, opts) {
    opts = opts || {};
    var size = opts.size || 280, R = size / 2, r0 = opts.inner || 46, n = items.length;
    var NS = "http://www.w3.org/2000/svg";
    root.classList.add("ar-radial");
    root.style.width = size + "px"; root.style.height = size + "px";
    var ring = document.createElement("div"); ring.className = "ar-radial__ring";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + size + " " + size); svg.setAttribute("class", "ar-radial__svg"); svg.setAttribute("role", "menu");
    function pt(rad, a) { return [R + rad * Math.cos(a), R + rad * Math.sin(a)]; }
    var step = (Math.PI * 2) / n, start = -Math.PI / 2 - step / 2, gap = 0.012;
    items.forEach(function (it, i) {
      var a0 = start + i * step + gap, a1 = start + (i + 1) * step - gap, ro = R - 2, large = a1 - a0 > Math.PI ? 1 : 0;
      var p1 = pt(ro, a0), p2 = pt(ro, a1), p3 = pt(r0, a1), p4 = pt(r0, a0);
      var g = document.createElementNS(NS, "g");
      g.setAttribute("class", "ar-radial__slice" + (it.active ? " is-active" : ""));
      g.setAttribute("tabindex", "0"); g.setAttribute("role", "menuitem"); g.setAttribute("aria-label", it.label);
      var path = document.createElementNS(NS, "path");
      path.setAttribute("d", "M" + p1 + " A" + ro + " " + ro + " 0 " + large + " 1 " + p2 + " L" + p3 + " A" + r0 + " " + r0 + " 0 " + large + " 0 " + p4 + " Z");
      var mid = (a0 + a1) / 2, c = pt((ro + r0) / 2 + 4, mid);
      var ic = document.createElementNS(NS, "text"); ic.setAttribute("class", "ar-radial__glyph");
      ic.setAttribute("x", c[0]); ic.setAttribute("y", c[1] - 2); ic.setAttribute("text-anchor", "middle"); ic.textContent = it.icon;
      var tx = document.createElementNS(NS, "text"); tx.setAttribute("x", c[0]); tx.setAttribute("y", c[1] + 16); tx.textContent = it.label;
      g.appendChild(path); g.appendChild(ic); g.appendChild(tx);
      function pick() {
        svg.querySelectorAll(".ar-radial__slice").forEach(function (s) { s.classList.remove("is-active"); });
        g.classList.add("is-active");
        if (label) label.textContent = it.label;
        if (it.onSelect) it.onSelect(it);
        if (opts.closeOnSelect) setOpen(false);
      }
      g.addEventListener("click", pick);
      g.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      svg.appendChild(g);
    });
    ring.appendChild(svg);
    var btn = document.createElement("button");
    btn.className = "ar-radial__trigger"; btn.type = "button"; btn.setAttribute("aria-label", opts.triggerLabel || "Abrir menu");
    btn.innerHTML = '<span class="ar-icon">' + (opts.triggerIcon || "add") + "</span>";
    var label = null;
    if (opts.showLabel) { label = document.createElement("div"); label.className = "ar-radial__label"; label.textContent = opts.hint || "Escolha uma ação"; }
    root.innerHTML = ""; root.appendChild(ring); root.appendChild(btn); if (label) root.appendChild(label);
    function setOpen(v) { root.classList.toggle("is-open", v); btn.setAttribute("aria-expanded", v ? "true" : "false"); }
    btn.addEventListener("click", function () { setOpen(!root.classList.contains("is-open")); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    if (opts.open) setOpen(true);
    return { open: function () { setOpen(true); }, close: function () { setOpen(false); }, toggle: function () { setOpen(!root.classList.contains("is-open")); } };
  }

  /* Tema: "light" | "dark" no <html data-theme>. */
  function setTheme(t) { document.documentElement.setAttribute("data-theme", t); }

  window.ARIT = { version: "1.0.0", logo: logo, sidebar: sidebar, radialMenu: radialMenu, setTheme: setTheme };
})();
