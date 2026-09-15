(() => {
  'use strict';
  const root = document.documentElement;
  const theme = document.getElementById('theme');
  const reduced = document.getElementById('reduced');
  const mark = document.getElementById('boot-mark');
  const play = document.getElementById('play');
  const status = document.getElementById('motion-status');
  const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let timer;
  const motion = window.diamaneLift.motion;
  // The same generated source powers the motion symbol and standalone assets.
  mark.replaceChildren(...window.diamaneLift.planes.map(d => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    return path;
  }));
  function setTheme() {
    const value = theme.value === 'system' ? (darkQuery.matches ? 'dark' : 'light') : theme.value;
    root.dataset.theme = value;
    document.querySelectorAll('[data-asset]').forEach(image => {image.src = `assets/${image.dataset.asset}-${value}.svg`;});
    document.querySelectorAll('[data-download]').forEach(link => {link.href = `assets/${link.dataset.download}-${value}.${link.dataset.format || 'svg'}`;});
  }
  function stopMotion(message) {
    clearTimeout(timer);
    mark.classList.remove('play');
    status.textContent = message;
  }
  function setMotionPreference() {
    root.dataset.reduced = String(reduced.checked || motionQuery.matches);
    if (root.dataset.reduced === 'true') stopMotion('Reduced motion · static mark');
    else if (!mark.classList.contains('play')) status.textContent = 'Static resting mark';
  }
  play.addEventListener('click', () => {
    stopMotion('Static resting mark');
    if (root.dataset.reduced === 'true') {status.textContent = 'Reduced motion · static mark';return;}
    void mark.getBoundingClientRect();
    mark.classList.add('play');
    status.textContent = 'Planes settling into place';
    timer = setTimeout(() => stopMotion('Complete · resting mark'), motion.completionMs);
  });
  theme.addEventListener('change', setTheme);
  darkQuery.addEventListener('change', setTheme);
  reduced.addEventListener('change', setMotionPreference);
  motionQuery.addEventListener('change', setMotionPreference);
  setTheme();
  setMotionPreference();
})();
