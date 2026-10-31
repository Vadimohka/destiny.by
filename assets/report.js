'use strict';

(() => {
  const links = [...document.querySelectorAll('.report-sidebar .page-toc a[href^="#"]')];
  const sections = links.map(link => document.querySelector(link.getAttribute('href')));
  if (!links.length || !sections.every(Boolean)) return;

  let scheduled = false;
  const update = () => {
    const threshold = Math.min(window.innerHeight * .25, 180);
    let active = -1;
    sections.forEach((section, index) => {
      if (section.getBoundingClientRect().top <= threshold) active = index;
    });
    links.forEach((link, index) => {
      if (index === active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
    scheduled = false;
  };
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(update);
  };
  window.addEventListener('scroll', schedule, {passive: true});
  window.addEventListener('resize', schedule, {passive: true});
  window.addEventListener('pageshow', schedule);
  update();
})();
