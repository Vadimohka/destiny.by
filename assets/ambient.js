'use strict';

(() => {
  const banners = [...document.querySelectorAll('[data-ambient-banner]')];
  const reveals = [...document.querySelectorAll('[data-reveal]')];
  if ((!banners.length && !reveals.length) || !('IntersectionObserver' in window)) return;

  const root = document.documentElement;
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const connection = navigator.connection;
  const visibleBanners = new Set();
  const arriving = new Set();
  let nextBurst = 0;
  let endBurst = 0;
  let activeBanner = null;
  let bannerCursor = -1;

  const enabled = () => !preference.matches && !connection?.saveData;
  const allowed = () => enabled() && !document.hidden;
  const finishReveal = element => {
    element.classList.remove('ambient-enter');
    element.style.removeProperty('--ambient-reveal-delay');
    arriving.delete(element);
  };
  const stopBurst = () => {
    window.clearTimeout(nextBurst);
    window.clearTimeout(endBurst);
    activeBanner?.classList.remove('ambient-break');
    activeBanner = null;
  };
  const scheduleBurst = () => {
    if (!allowed() || !visibleBanners.size) return;
    nextBurst = window.setTimeout(() => {
      if (!allowed() || !visibleBanners.size) return;
      const candidates = [...visibleBanners];
      bannerCursor = (bannerCursor + 1) % candidates.length;
      activeBanner = candidates[bannerCursor];
      activeBanner.classList.add('ambient-break');
      endBurst = window.setTimeout(() => {
        activeBanner?.classList.remove('ambient-break');
        activeBanner = null;
        scheduleBurst();
      }, 380);
    }, 12000 + Math.random() * 6000);
  };
  const update = () => {
    root.classList.toggle('ambient-enabled', enabled());
    root.classList.toggle('ambient-suspended', document.hidden);
    banners.forEach(banner => banner.classList.toggle('ambient-active', allowed() && visibleBanners.has(banner)));
    if (!allowed()) [...arriving].forEach(finishReveal);
    stopBurst();
    scheduleBurst();
  };

  const bannerObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting && entry.intersectionRatio >= .12) visibleBanners.add(entry.target);
      else visibleBanners.delete(entry.target);
    });
    update();
  }, {threshold: [0, .12]});
  banners.forEach((banner, index) => {
    banner.style.setProperty('--ambient-scan-duration', `${12 + (index % 4) * .55}s`);
    banner.style.setProperty('--ambient-drift-duration', `${24 + (index % 4) * 2}s`);
    banner.style.setProperty('--ambient-phase', `${(index % 4) * -2.4}s`);
    bannerObserver.observe(banner);
  });

  const revealObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      revealObserver.unobserve(entry.target);
      if (!allowed() || entry.target.contains(document.activeElement)) return;
      const delay = Math.max(0, Math.min(240, Number(entry.target.dataset.revealDelay) || 0));
      entry.target.style.setProperty('--ambient-reveal-delay', `${delay}ms`);
      arriving.add(entry.target);
      entry.target.classList.add('ambient-enter');
    });
  }, {threshold: .05, rootMargin: '0px 0px -20px 0px'});
  reveals.forEach(element => revealObserver.observe(element));

  root.addEventListener('animationend', event => {
    if (event.animationName === 'ambient-arrive' && arriving.has(event.target)) finishReveal(event.target);
  });
  root.addEventListener('focusin', event => {
    [...arriving].forEach(element => {
      if (element.contains(event.target)) finishReveal(element);
    });
  });
  preference.addEventListener('change', update);
  connection?.addEventListener?.('change', update);
  document.addEventListener('visibilitychange', update);
  window.addEventListener('pageshow', update);
  update();
})();
