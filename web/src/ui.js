// DOM UI (touch-first, Chinese): loading screen, error overlay, the time slider with 白天 / 黄昏 / 夜晚 presets,
// camera presets 总览 / 特写 / 自由, the quality selector, the free-camera hint, the FPS badge and the debug panel.
const $ = (id) => document.getElementById(id);

const errors = [];
const PRESET_LABEL = { overview: '总览', closeup: '特写', free: '自由' };
const QUALITY_LABEL = { auto: '自动', low: '低', medium: '标准', high: '高' };
const DAY_PARTS = [
  [0, '深夜'], [4.6, '黎明'], [6.5, '清晨'], [8.5, '上午'], [11, '正午'], [14, '下午'], [16.5, '黄昏'], [19, '夜晚'], [23.5, '深夜'],
];

export function showError(err, context = '') {
  const msg = err instanceof Error ? `${err.message}\n${err.stack || ''}` : String(err);
  errors.push(`${context ? `[${context}] ` : ''}${msg}`.trim());
  $('error-text').textContent = errors.join('\n\n');
  $('error').hidden = false;
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
 *           onQuality:(q:string)=>void, trackGradient:string, touch:boolean }} opts
 */
export function initControls({ onHour, onTimePreset, onPreset, onQuality, trackGradient, touch }) {
  $('panel').hidden = false;
  $('top').hidden = false;
  $('hint').textContent = touch ? '单指拖动旋转 · 双指缩放平移' : '拖动旋转 · 滚轮缩放 · 右键平移';
  $('slider-track').style.background = trackGradient;

  // keep the hint above the panel, whatever its height
  const panel = $('panel');
  const setPanelHeight = () => document.documentElement.style.setProperty('--panel-h', `${panel.offsetHeight}px`);
  if (window.ResizeObserver) new ResizeObserver(setPanelHeight).observe(panel);
  setPanelHeight();

  const slider = $('time-slider');
  let dragging = null;
  const hourAt = (e) => {
    const r = slider.getBoundingClientRect();
    return Math.min(Math.max((e.clientX - r.left) / Math.max(r.width, 1), 0), 1) * 24;
  };
  slider.addEventListener('pointerdown', (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    dragging = e.pointerId;
    slider.setPointerCapture(e.pointerId);
    slider.classList.add('dragging');
    onHour(hourAt(e));
    e.preventDefault();
  });
  slider.addEventListener('pointermove', (e) => {
    if (dragging !== e.pointerId) return;
    onHour(hourAt(e));
  });
  const end = (e) => {
    if (dragging !== e.pointerId) return;
    dragging = null;
    slider.classList.remove('dragging');
  };
  slider.addEventListener('pointerup', end);
  slider.addEventListener('pointercancel', end);
  slider.addEventListener('lostpointercapture', end);
  slider.addEventListener('keydown', (e) => {
    const h = Number(slider.getAttribute('aria-valuenow')) || 0;
    const step = { ArrowLeft: -0.25, ArrowDown: -0.25, ArrowRight: 0.25, ArrowUp: 0.25, PageDown: -1, PageUp: 1 }[e.key];
    if (step !== undefined) onHour(Math.min(Math.max(h + step, 0), 24));
    else if (e.key === 'Home') onHour(0);
    else if (e.key === 'End') onHour(24);
    else return;
    e.preventDefault();
  });

  for (const b of document.querySelectorAll('[data-tod]')) b.addEventListener('click', () => onTimePreset(b.dataset.tod));
  for (const b of document.querySelectorAll('[data-preset]')) b.addEventListener('click', () => onPreset(b.dataset.preset));
  for (const b of document.querySelectorAll('[data-quality]')) b.addEventListener('click', () => onQuality(b.dataset.quality));
}

let hintTimer = 0;
/** sliderHour: the slider position (0 - 24, 24 kept at the right end) */
export function reflectState({ preset, quality, resolvedQuality, sliderHour, todPreset }) {
  if (preset) {
    for (const b of document.querySelectorAll('[data-preset]')) b.classList.toggle('active', b.dataset.preset === preset);
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
    $('slider-thumb').style.left = `${(h / 24) * 100}%`;
    const text = formatHour(h);
    $('time-clock').textContent = text;
    $('time-part').textContent = dayPart(((h % 24) + 24) % 24);
    const slider = $('time-slider');
    slider.setAttribute('aria-valuenow', h.toFixed(2));
    slider.setAttribute('aria-valuetext', text);
  }
  if (todPreset !== undefined) {
    for (const b of document.querySelectorAll('[data-tod]')) b.classList.toggle('active', b.dataset.tod === todPreset);
  }
}

/**
 * The always-visible FPS badge (frame rate, current tier, render pixel ratio); tapping it calls onToggle. The
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
      `叶卡      ${s.cards.toLocaleString()} 张`,
      `时间 ${formatHour(s.hour)}  画质 ${QUALITY_LABEL[s.quality] || s.quality}${s.quality === 'auto' ? `·${QUALITY_LABEL[s.tier]}` : ''}  镜头 ${PRESET_LABEL[s.preset] || s.preset}`,
    ].join('\n');
  }
}
