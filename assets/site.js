'use strict';

(() => {
  document.documentElement.classList.add('js-ready');

  const year = document.querySelector('#copyright-year');
  if (year) year.textContent = String(new Date().getFullYear());

  const menuToggle = document.querySelector('.menu-toggle');
  const nav = document.querySelector('#site-nav');
  const menuLabels = document.documentElement.lang === 'be'
    ? { open: 'Адкрыць меню', close: 'Закрыць меню' }
    : { open: 'Открыть меню', close: 'Закрыть меню' };

  // Static links work without JavaScript; preserve a section when switching languages.
  const languageLinks = [...document.querySelectorAll('.language-switcher a')];
  const updateLanguageLinks = () => languageLinks.forEach(link => {
    const url = new URL(link.href, window.location.href);
    url.hash = window.location.hash;
    link.href = url.pathname + url.search + url.hash;
  });
  updateLanguageLinks();
  window.addEventListener('hashchange', updateLanguageLinks);

  const closeMenu = (restoreFocus = false) => {
    if (!menuToggle || !nav) return;
    menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.setAttribute('aria-label', menuLabels.open);
    nav.classList.remove('is-open');
    if (restoreFocus) menuToggle.focus();
  };
  if (menuToggle && nav) menuToggle.addEventListener('click', event => {
    const open = menuToggle.getAttribute('aria-expanded') !== 'true';
    menuToggle.setAttribute('aria-expanded', String(open));
    menuToggle.setAttribute('aria-label', open ? menuLabels.close : menuLabels.open);
    nav.classList.toggle('is-open', open);
    if (open && event.detail === 0) nav.querySelector('a')?.focus();
  });
  nav?.addEventListener('click', event => {
    if (event.target.closest('a')) closeMenu();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menuToggle?.getAttribute('aria-expanded') === 'true') closeMenu(true);
  });
  document.addEventListener('click', event => {
    if (nav && menuToggle && !nav.contains(event.target) && !menuToggle.contains(event.target)) closeMenu();
  });
  window.matchMedia('(min-width: 1101px)').addEventListener('change', () => closeMenu());

  const initTabs = (listSelector, buttonSelector, datasetKey, isService = false) => {
    const tablist = document.querySelector(listSelector);
    if (!tablist) return;
    const buttons = [...tablist.querySelectorAll(buttonSelector)];
    const panels = buttons.map(button => document.getElementById(button.dataset[datasetKey]));
    if (!buttons.length || !panels.every(Boolean)) return;
    const activate = (index, focus = false) => {
      buttons.forEach((button, position) => {
        const selected = index === position;
        button.setAttribute('aria-selected', String(selected));
        button.tabIndex = selected ? 0 : -1;
        panels[position].hidden = !selected;
      });
      if (focus) buttons[index].focus();
    };
    const revealHash = hash => {
      if (!hash) return false;
      let id;
      try { id = decodeURIComponent(hash.slice(1)); } catch { return false; }
      const target = document.getElementById(id);
      const index = panels.findIndex(panel => panel === target || (target && panel.contains(target)));
      if (index < 0) return false;
      activate(index);
      return true;
    };
    tablist.setAttribute('role', 'tablist');
    const orientation = window.matchMedia('(min-width: 801px)');
    const updateOrientation = () => tablist.setAttribute('aria-orientation', isService && orientation.matches ? 'vertical' : 'horizontal');
    updateOrientation();
    orientation.addEventListener('change', updateOrientation);
    buttons.forEach((button, index) => {
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', panels[index].id);
      panels[index].setAttribute('role', 'tabpanel');
      panels[index].setAttribute('aria-labelledby', button.id);
      panels[index].tabIndex = 0;
      button.addEventListener('click', () => activate(index));
      button.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % buttons.length;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = buttons.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        activate(next, true);
      });
    });
    if (!revealHash(window.location.hash)) activate(0);
    window.addEventListener('hashchange', () => revealHash(window.location.hash));
    document.addEventListener('click', event => {
      const link = event.target.closest('a[href]');
      if (!link) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin === window.location.origin && url.pathname === window.location.pathname) revealHash(url.hash);
    });
    if (isService) tablist.closest('.service-explorer')?.classList.add('has-tabs');
    tablist.hidden = false;
  };
  initTabs('.report-tabs', '[data-report-tab]', 'reportTab');

})();
