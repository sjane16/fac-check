/*
 * Fac-Check theme switcher (Light / Dark / System).
 * Loaded synchronously in <head> BEFORE {% tailwind_css %} so the correct
 * theme is applied before first paint (no white flash).
 */
(function () {
  var KEY = "fc-theme";
  var root = document.documentElement;
  var mq = window.matchMedia("(prefers-color-scheme: dark)");

  function choice() {
    try {
      return localStorage.getItem(KEY) || "light";
    } catch (e) {
      return "light";
    }
  }

  function apply() {
    var c = choice();
    var dark = c === "dark" || (c === "system" && mq.matches);
    root.classList.toggle("dark", dark);
    root.dataset.theme = dark ? "dark" : "light"; // for daisyUI
    root.style.colorScheme = dark ? "dark" : "light";
    root.dataset.themeChoice = c; // for the active-button highlight
    document.dispatchEvent(new CustomEvent("themechange", { detail: { dark: dark } }));
  }

  apply();

  // Follow the OS setting while "System" is selected
  if (mq.addEventListener) {
    mq.addEventListener("change", apply);
  }

  // Event delegation: <script> tags inside AJAX-loaded partials never run,
  // so the Appearance buttons are handled from here instead.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-theme-choice]");
    if (!btn) return;
    try {
      localStorage.setItem(KEY, btn.dataset.themeChoice);
    } catch (err) {}
    apply();
  });
})();
