// Quality tiers (低 / 标准 / 高, plus 自动 = detected), device detection and the dynamic resolution controller.

export const TIERS = {
  low: {
    key: 'low',
    grass: 25000, fireflies: 120, lodScale: 1.2, treeBudget: 70000, wind: false,
    shadow: 1024, shadowTaps: 1,
    dof: false, dofScale: 0.5, dofSamples: 12,
    bloomScale: 0.25,
    msaa: 0, maxPixelRatio: 1, minPixelRatio: 0.5,
  },
  medium: {
    key: 'medium',
    grass: 60000, fireflies: 250, lodScale: 2, treeBudget: 170000, wind: true,
    shadow: 2048, shadowTaps: 4,
    dof: true, dofScale: 0.5, dofSamples: 20,
    bloomScale: 0.5,
    msaa: 2, maxPixelRatio: 1.5, minPixelRatio: 0.6,
    maxPixels: 830000, // phones: keeps textures + render targets within ~64 MB on the largest screens
  },
  high: {
    key: 'high',
    grass: 200000, fireflies: 400, lodScale: 2.5, treeBudget: 450000, wind: true,
    shadow: 4096, shadowTaps: 16,
    dof: true, dofScale: 0.5, dofSamples: 40,
    bloomScale: 0.5,
    msaa: 4, maxPixelRatio: 2, minPixelRatio: 0.75,
  },
};

export const QUALITY_ALIASES = {
  auto: 'auto', 自动: 'auto',
  low: 'low', 低: 'low',
  medium: 'medium', 标准: 'medium',
  high: 'high', 高: 'high',
};

const WEAK_GPU = /Adreno \(TM\) ([3-5]\d\d|6[01]\d)\b|Mali-(4\d\d|T\d+|G3\d|G5[0-2])\b|PowerVR|SwiftShader|llvmpipe|Software|Microsoft Basic/i;
const INTEGRATED_GPU = /Intel(?!.*Arc)|Mali|Adreno|Apple M1\b/i;

/** Phone / tablet / desktop guess from UA, touch, screen size and the GPU string. */
export function detectDevice(gl) {
  const ua = navigator.userAgent || '';
  const uaMobile = navigator.userAgentData?.mobile === true || /Android|iPhone|iPod|Mobile|HarmonyOS|OpenHarmony/i.test(ua);
  const iPadOS = /Macintosh/.test(ua) && navigator.maxTouchPoints > 1;
  const coarse = window.matchMedia?.('(pointer: coarse)').matches ?? false;
  const shortSide = Math.min(window.screen?.width || 1e4, window.screen?.height || 1e4);
  const mobile = uaMobile || iPadOS || (coarse && shortSide <= 820);
  let gpu = '';
  try {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    gpu = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
  } catch {
    gpu = '';
  }
  const weak = WEAK_GPU.test(gpu);
  const lowMemory = (navigator.deviceMemory && navigator.deviceMemory <= 3) || (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4);
  let tier;
  if (mobile) tier = weak || lowMemory ? 'low' : 'medium';
  else tier = weak ? 'low' : INTEGRATED_GPU.test(gpu) ? 'medium' : 'high';
  return { mobile, tier, targetFps: mobile ? 30 : 60 };
}

/**
 * Adapts the render pixel ratio to the measured frame time: down when frames are slower than the target, up again
 * (probing, with a cooldown so it does not oscillate) when there is headroom.
 */
export function createResolutionController({ targetFps, onChange }) {
  const st = {
    targetMs: 1000 / targetFps,
    scale: 1,
    min: 0.5,
    max: 1,
    samples: [],
    windowStart: 0,
    cooldownUntil: 0,
    upStreak: 0,
    blockUpUntil: 0,
    lastUp: -1e9,
    failures: 0,
  };

  function setRange(min, max, reset = false) {
    st.min = Math.min(min, max);
    st.max = max;
    const next = reset ? max : Math.min(Math.max(st.scale, st.min), st.max);
    if (next !== st.scale || reset) {
      st.scale = next;
      onChange(st.scale);
    }
    st.samples.length = 0;
    st.cooldownUntil = performance.now() + 1500;
  }

  function apply(next, now) {
    next = Math.round(Math.min(Math.max(next, st.min), st.max) * 20) / 20;
    if (Math.abs(next - st.scale) < 0.025) return;
    st.scale = next;
    st.cooldownUntil = now + 1500; // the resize itself costs a frame or two
    onChange(next);
  }

  /** call once per frame with the frame time in ms */
  function sample(ms, now) {
    if (now < st.cooldownUntil) {
      st.samples.length = 0;
      st.windowStart = now;
      return;
    }
    if (ms > 0 && ms < 250) st.samples.push(ms);
    if (now - st.windowStart < 1000 || st.samples.length < 12) return;
    const sorted = [...st.samples].sort((a, b) => a - b);
    const med = sorted[Math.floor(sorted.length * 0.5)];
    const p80 = sorted[Math.floor(sorted.length * 0.8)];
    st.samples.length = 0;
    st.windowStart = now;
    const t = st.targetMs;
    if (p80 > t * 1.15) {
      // pixels scale with the square of the pixel ratio
      apply(st.scale * Math.min(Math.max(Math.sqrt(t / p80), 0.75), 0.95), now);
      st.upStreak = 0;
      // a probe that did not hold backs off longer each time
      if (now - st.lastUp < 5000) st.failures = Math.min(st.failures + 1, 4);
      st.blockUpUntil = now + 8000 * 2 ** st.failures;
    } else if (st.scale < st.max && now > st.blockUpUntil) {
      // clear headroom, or holding the target (a vsync-limited frame rate hides any headroom): probe upward
      const plenty = p80 < t * 0.6;
      const headroom = med < t * 0.8;
      const holding = med <= t * 1.03 && p80 <= t * 1.08;
      st.upStreak = plenty ? st.upStreak + 4 : headroom ? st.upStreak + 2 : holding ? st.upStreak + 1 : 0;
      if (st.upStreak >= 4) {
        apply(st.scale * (plenty ? 1.2 : 1.1), now);
        st.lastUp = now;
        st.upStreak = 0;
      }
    } else {
      st.upStreak = 0;
    }
  }

  return {
    state: st,
    sample,
    setRange,
  };
}
