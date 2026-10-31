'use strict';

(() => {
  const heroes = document.querySelectorAll('[data-page-hero]');
  if (!heroes.length) return;
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const connection = navigator.connection;

  heroes.forEach(hero => {
    let visible = !('IntersectionObserver' in window);
    let nextBreak = 0;
    let breakEnd = 0;
    const allowed = () => !preference.matches && !connection?.saveData && visible && !document.hidden;
    const stopBreak = () => {
      window.clearTimeout(nextBreak);
      window.clearTimeout(breakEnd);
      hero.classList.remove('is-signal-break');
    };
    const scheduleBreak = () => {
      if (!allowed()) return;
      nextBreak = window.setTimeout(() => {
        if (!allowed()) return;
        hero.classList.add('is-signal-break');
        breakEnd = window.setTimeout(() => {
          hero.classList.remove('is-signal-break');
          scheduleBreak();
        }, 380);
      }, 11000 + Math.random() * 5000);
    };
    const update = () => {
      stopBreak();
      hero.classList.toggle('is-motion-active', allowed());
      if (allowed()) scheduleBreak();
    };

    preference.addEventListener('change', update);
    connection?.addEventListener?.('change', update);
    document.addEventListener('visibilitychange', update);
    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver(entries => {
        visible = entries[0].isIntersecting;
        update();
      }, {threshold: .08});
      observer.observe(hero);
    }
    update();
  });
})();
