'use strict';

(() => {
  const hero = document.querySelector('.hero');
  if (!hero) return;

  const root = document.documentElement;
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const connection = navigator.connection;
  let visible = !('IntersectionObserver' in window);
  let burstTimer = 0;
  let endTimer = 0;
  let started = false;
  let revealObserver;

  const allowed = () => !preference.matches && !connection?.saveData && visible && !document.hidden;
  const stopBurst = () => {
    window.clearTimeout(burstTimer);
    window.clearTimeout(endTimer);
    hero.classList.remove('is-interference');
  };
  const scheduleBurst = delay => {
    window.clearTimeout(burstTimer);
    if (!allowed()) return;
    burstTimer = window.setTimeout(() => {
      if (!allowed()) return;
      hero.classList.add('is-interference');
      endTimer = window.setTimeout(() => {
        hero.classList.remove('is-interference');
        scheduleBurst(8500 + Math.random() * 4500);
      }, 520);
    }, delay);
  };
  const revealAll = () => {
    revealObserver?.disconnect();
    document.querySelectorAll('.reveal-pending').forEach(element => element.classList.remove('reveal-pending'));
  };
  const update = () => {
    const reduced = preference.matches || Boolean(connection?.saveData);
    root.classList.toggle('motion-off', reduced);
    hero.classList.toggle('is-active', allowed());
    stopBurst();
    if (reduced) revealAll();
    if (allowed()) {
      scheduleBurst(started ? 6500 : 1600);
      started = true;
    }
  };

  preference.addEventListener('change', update);
  connection?.addEventListener?.('change', update);
  document.addEventListener('visibilitychange', update);

  if ('IntersectionObserver' in window) {
    const heroObserver = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      update();
    }, {threshold: .12});
    heroObserver.observe(hero);

    if (!preference.matches && !connection?.saveData) {
      revealObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          entry.target.classList.remove('reveal-pending');
          entry.target.classList.add('reveal-enter');
          entry.target.addEventListener('animationend', () => entry.target.classList.remove('reveal-enter'), {once: true});
          revealObserver.unobserve(entry.target);
        });
      }, {threshold: .08, rootMargin: '0px 0px -24px 0px'});
      document.querySelectorAll('.section-heading, .service-row, .result-copy, .report-preview, .approach-list li, .contact-section').forEach(element => {
        if (element.getBoundingClientRect().top < window.innerHeight - 24) return;
        element.classList.add('reveal-pending');
        revealObserver.observe(element);
      });
    }
  }
  update();
})();
