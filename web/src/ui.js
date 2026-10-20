// Touch-first controls: icon shortcuts and a compact time window, camera modes, quality and diagnostics.
import { createTimeGesture } from './time-gesture.js';
import { createIdleControls } from './idle-controls.js';

const $ = (id) => document.getElementById(id);

const errors = [];
const PRESET_LABEL = { overview: '总览', closeup: '特写', free: '自由' };
const QUALITY_LABEL = { auto: '自动', low: '低', medium: '标准', high: '高' };
const DAY_PARTS = [
  [0, '深夜'], [4.6, '黎明'], [6.5, '清晨'], [8.5, '上午'], [11, '正午'], [14, '下午'], [16.5, '黄昏'], [19, '夜晚'], [23.5, '深夜'],
];
let resetIdleClock = () => {};
let wakeControls = () => {};

export function showError(err, context = '') {
  const msg = err instanceof Error ? `${err.message}\n${err.stack || ''}` : String(err);
  errors.push(`${context ? `[${context}] ` : ''}${msg}`.trim());
  $('error-text').textContent = errors.join('\n\n');
  $('error').hidden = false;
  wakeControls();
}

export function installErrorHandlers() {
  window.addEventListener('error', (e) => showError(e.error || e.message, 'error'));
  window.addEventListener('unhandledrejection', (e) => showError(e.reason, 'promise'));
  $('error-close').addEventListener('click', () => {
    $('error').hidden = true;
  });
  // no page zoom from pinches or double taps outside the canvas (iOS ignores user-scalable=no)
  for (const type of ['gesturestart', 'gesturechange', 'dblclick']) document.addEventListener(type, (e) => e.preventDefault(), { passive: false });
}

export function setLoading(fraction, text) {
  $('loading-fill').style.width = `${Math.round(Math.min(Math.max(fraction, 0), 1) * 100)}%`;
  if (text) $('loading-text').textContent = `${Math.round(fraction * 100)}%  ${text}`;
}

export function hideLoading() {
  const el = $('loading');
  el.classList.add('done');
  setTimeout(() => {
    el.hidden = true;
    resetIdleClock();
  }, 700);
}

function formatHour(h) {
  const m = Math.round((((h % 24) + 24) % 24) * 60) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

function dayPart(h) {
  let name = DAY_PARTS[0][1];
  for (const [start, n] of DAY_PARTS) if (h >= start) name = n;
  return name;
}

/**
 * @param {{ onHour:(h:number)=>void, onTimePreset:(name:string)=>void, onPreset:(name:string)=>void,
 *           onQuality:(q:string)=>void, touch:boolean }} opts
 */
export function initControls({ onHour, onTimePreset, onPreset, onQuality, touch }) {
  $('panel').hidden = false;
  $('top').hidden = false;
  $('hint').textContent = touch ? '单指拖动旋转 · 双指缩放平移' : '拖动旋转 · 滚轮缩放 · 右键平移';
  const panel = $('panel');
  const timeWindow = $('time-window');
  const slider = $('time-slider');
  const presets = $('time-presets');
  let returnFocus = null;
  let closeTimer;
  const setPanelHeight = () => document.documentElement.style.setProperty('--panel-h', `${panel.offsetHeight + (timeWindow.hidden ? 0 : timeWindow.offsetHeight + 10)}px`);
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(setPanelHeight);
    observer.observe(panel);
    observer.observe(timeWindow);
  }
  setPanelHeight();

  function setOpen(open) {
    clearTimeout(closeTimer);
    if (!open && slider.contains(document.activeElement)) {
      (returnFocus || presets.querySelector('.active') || presets.querySelector('button')).focus({ preventScroll: true });
    }
    timeWindow.hidden = !open;
    setPanelHeight();
  }

  const selectTime = (choice) => {
    setOpen(false);
    onTimePreset(choice);
  };
  const gesture = createTimeGesture({
    readHour: () => Number(slider.getAttribute('aria-valuenow')) || 0,
    onHour,
    onOpen: () => setOpen(true),
    onTap: selectTime,
    onDragging: (dragging) => {
      panel.classList.toggle('dragging', dragging);
      if (!dragging) closeTimer = setTimeout(() => setOpen(false), 900);
    },
  });

  for (const surface of [presets, slider]) {
    surface.addEventListener('pointerdown', (e) => {
      if (e.isPrimary === false || e.button !== 0) return;
      clearTimeout(closeTimer);
      const button = e.target.closest('[data-tod]');
      const choice = button?.dataset.tod;
      if (!gesture.start(e, choice, surface === slider)) return;
      if (button) returnFocus = button;
      surface.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    surface.addEventListener('pointermove', (e) => gesture.move(e));
    surface.addEventListener('pointerup', (e) => gesture.end(e));
    surface.addEventListener('pointercancel', (e) => gesture.end(e, true));
    surface.addEventListener('lostpointercapture', (e) => gesture.end(e, true));
    surface.addEventListener('contextmenu', (e) => e.preventDefault());
  }
  document.addEventListener('pointerdown', (e) => {
    if (e.isPrimary !== false && !e.target.closest('.time-control')) setOpen(false);
  });
  const cancel = () => { gesture.cancel(); setOpen(false); };
  window.addEventListener('blur', cancel);
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancel(); });
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { cancel(); e.preventDefault(); }
  });
  const timeKeys = { ArrowLeft: -0.25, ArrowDown: -0.25, ArrowRight: 0.25, ArrowUp: 0.25, PageDown: -1, PageUp: 1 };
  function adjustTime(e) {
    clearTimeout(closeTimer);
    const h = Number(slider.getAttribute('aria-valuenow')) || 0;
    const step = timeKeys[e.key];
    if (step !== undefined) onHour(Math.min(Math.max(h + step, 0), 24));
    else if (e.key === 'Home') onHour(0);
    else if (e.key === 'End') onHour(24);
    else return;
    e.preventDefault();
  }
  slider.addEventListener('keydown', adjustTime);
  presets.addEventListener('keydown', (e) => {
    if (!(e.key in timeKeys) && e.key !== 'Home' && e.key !== 'End') return;
    returnFocus = e.target.closest('[data-tod]');
    setOpen(true);
    slider.focus({ preventScroll: true });
    adjustTime(e);
  });

  // Pointer taps are resolved by the gesture; native keyboard/assistive clicks still work.
  for (const b of document.querySelectorAll('[data-tod]')) b.addEventListener('click', (e) => {
    if (e.detail === 0) selectTime(b.dataset.tod);
  });
  for (const b of document.querySelectorAll('[data-preset]')) b.addEventListener('click', () => onPreset(b.dataset.preset));
  for (const b of document.querySelectorAll('[data-quality]')) b.addEventListener('click', () => onQuality(b.dataset.quality));
  installIdleVisibility(cancel);
}

function installIdleVisibility(closeTimeWindow) {
  const chrome = ['panel', 'top', 'fps', 'debug', 'hint'].map($);
  const wake = $('wake-controls');
  const heldInputs = new Set();
  let lastFocus = null;
  const idle = createIdleControls({
    canHide: () => $('loading').hidden && $('error').hidden && !document.hidden,
    onHide: () => {
      closeTimeWindow();
      lastFocus = document.activeElement;
      const hadFocus = chrome.some((el) => el.contains(lastFocus));
      for (const el of chrome) el.inert = true;
      document.body.classList.add('ui-idle');
      wake.hidden = false;
      if (hadFocus) wake.focus({ preventScroll: true });
    },
    onShow: () => {
      document.body.classList.remove('ui-idle');
      for (const el of chrome) el.inert = false;
      wake.hidden = true;
    },
  });
  resetIdleClock = () => idle.activity();
  wakeControls = () => idle.wake();

  // A transparent wake surface consumes the complete first click before normal controls return.
  wake.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    idle.wake();
    if (e.detail === 0 && lastFocus?.isConnected) lastFocus.focus({ preventScroll: true });
  });
  wake.addEventListener('contextmenu', (e) => e.preventDefault());

  const hold = (token) => { heldInputs.add(token); idle.hold(token); };
  const release = (token) => { if (heldInputs.delete(token)) idle.release(token); };
  document.addEventListener('pointerdown', (e) => hold(`pointer:${e.pointerId}`), { capture: true, passive: true });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    document.addEventListener(type, (e) => release(`pointer:${e.pointerId}`), { capture: true, passive: true });
  }
  document.addEventListener('keydown', (e) => hold(`key:${e.code || e.key}`), { capture: true });
  document.addEventListener('keyup', (e) => release(`key:${e.code || e.key}`), { capture: true });
  document.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'mouse' && e.buttons === 0) release(`pointer:${e.pointerId}`);
    resetIdleClock();
  }, { capture: true, passive: true });
  for (const type of ['wheel', 'click', 'focusin']) {
    document.addEventListener(type, resetIdleClock, { capture: true, passive: true });
  }
  const releaseAll = () => { for (const token of heldInputs) release(token); };
  window.addEventListener('blur', releaseAll);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) releaseAll();
    else resetIdleClock();
  });
}

let hintTimer = 0;
/** sliderHour: the time adjustment position (0–24, with both midnight endpoints available). */
export function reflectState({ preset, quality, resolvedQuality, sliderHour, todPreset }) {
  if (preset) {
    for (const b of document.querySelectorAll('[data-preset]')) {
      const active = b.dataset.preset === preset;
      b.classList.toggle('active', active);
      b.setAttribute('aria-pressed', String(active));
    }
    const hint = $('hint');
    clearTimeout(hintTimer);
    hint.hidden = preset !== 'free';
    if (preset === 'free') hintTimer = setTimeout(() => (hint.hidden = true), 5000);
  }
  if (quality) {
    for (const b of document.querySelectorAll('[data-quality]')) {
      b.classList.toggle('active', b.dataset.quality === quality);
      b.setAttribute('aria-checked', b.dataset.quality === quality ? 'true' : 'false');
      b.classList.toggle('implied', quality === 'auto' && b.dataset.quality === resolvedQuality);
    }
  }
  if (sliderHour !== undefined) {
    const h = Math.min(Math.max(sliderHour, 0), 24);
    const text = formatHour(h);
    $('time-value').textContent = text;
    const slider = $('time-slider');
    slider.setAttribute('aria-valuenow', h.toFixed(2));
    slider.setAttribute('aria-valuetext', `${text} ${dayPart(h % 24)}`);
  }
  if (todPreset !== undefined) {
    const h = Number($('time-slider').getAttribute('aria-valuenow')) % 24;
    const period = todPreset || (h < 4.6 || h >= 19 ? 'night' : h < 11 ? 'morning' : h < 16.5 ? 'day' : 'dusk');
    for (const b of document.querySelectorAll('[data-tod]')) {
      const active = b.dataset.tod === period;
      b.classList.toggle('active', active);
      b.setAttribute('aria-pressed', String(active));
    }
  }
}

/**
 * The FPS badge (frame rate, current tier, render pixel ratio); tapping it calls onToggle. The
 * returned function takes a getter of { fps, tier, pixelRatio } and refreshes the badge twice a second.
 */
export function createFpsBadge(onToggle) {
  const el = $('fps');
  const rate = $('fps-rate');
  const extra = $('fps-extra');
  el.hidden = false;
  el.addEventListener('click', onToggle);
  let last = 0;
  return (read) => {
    const now = performance.now();
    if (now - last < 500) return;
    last = now;
    const { fps, tier, pixelRatio } = read();
    el.dataset.level = fps >= 50 ? 'good' : fps >= 28 ? 'ok' : 'low';
    rate.textContent = `${Math.round(fps)} FPS`;
    extra.textContent = `· ${QUALITY_LABEL[tier]} ${pixelRatio.toFixed(1)}×`;
  };
}

/**
 * The detailed debug panel, open from the start with ?debug and toggled by the FPS badge. update() takes the stats
 * getter and refreshes the text 4 times a second while the panel is open.
 */
export function createDebugHud(open) {
  const el = $('debug');
  el.hidden = !open;
  $('fps').setAttribute('aria-expanded', String(open));
  let last = 0;
  return {
    get open() {
      return !el.hidden;
    },
    toggle() {
      el.hidden = !el.hidden;
      $('fps').setAttribute('aria-expanded', String(!el.hidden));
      last = 0;
    },
    update(stats) {
      if (el.hidden) return;
      const now = performance.now();
      if (now - last < 250) return;
      last = now;
      render(stats());
    },
  };

  function render(s) {
    el.textContent = [
      `FPS      ${s.fps.toFixed(1)}  (${s.frameMs.toFixed(1)} ms)  目标 ${s.targetFps}${s.gpuMs ? `  GPU ${s.gpuMs.toFixed(2)} ms` : ''}`,
      `渲染比例  ${s.pixelRatio.toFixed(2)}  (${s.minPixelRatio.toFixed(2)}–${s.maxPixelRatio.toFixed(2)})  ${s.width}x${s.height}`,
      `场景绘制  ${s.sceneCalls} 次  ${(s.sceneTriangles / 1e3).toFixed(0)} k 三角形`,
      `总绘制    ${s.calls} 次  ${(s.triangles / 1e3).toFixed(0)} k`,
      `贴图显存  ${(s.textureBytes / 1048576).toFixed(1)} MB  (渲染目标 ${(s.targetBytes / 1048576).toFixed(1)} MB)`,
      `阴影      ${s.shadowMap}²  重绘 ${s.shadowRenders} 次  ${s.shadowCalls} 次 / ${(s.shadowTriangles / 1e3).toFixed(0)} k  ${s.shadowCpuMs.toFixed(2)} ms`,
      `树 LOD    ${s.treeLods.join(' / ')}  草 ${s.grass.toLocaleString()}`,
      `时间 ${formatHour(s.hour)}  画质 ${QUALITY_LABEL[s.quality] || s.quality}${s.quality === 'auto' ? `·${QUALITY_LABEL[s.tier]}` : ''}  镜头 ${PRESET_LABEL[s.preset] || s.preset}`,
    ].join('\n');
  }
}
