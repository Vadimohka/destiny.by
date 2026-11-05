'use strict';

// GitHub Pages serves the root 404 document even for a missing /be/ URL.
// Localize that response in place so its URL and HTTP 404 status are preserved.
(() => {
  if (!window.location.pathname.startsWith('/be/')) return;
  document.documentElement.lang = 'be';
  document.title = 'Старонка не знойдзена — DESTINY';
  document.querySelectorAll('[data-be]').forEach(element => {
    // Values are authored HTML in this document, never URL or visitor input.
    element.innerHTML = element.dataset.be;
  });
  document.querySelectorAll('[data-be-label]').forEach(element => {
    element.setAttribute('aria-label', element.dataset.beLabel);
  });
  document.querySelectorAll('[data-be-href]').forEach(element => {
    element.setAttribute('href', element.dataset.beHref);
  });
  document.querySelectorAll('.language-switcher a').forEach(link => {
    if (link.hreflang === 'be') link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
})();
