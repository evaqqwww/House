import { MathUtils, NoToneMapping, SRGBColorSpace, Vector2, WebGLRenderer } from 'three';
import { loadAssets } from './assets.js';
import { createLighting } from './lighting.js';
import { buildWorld } from './world.js';
import { createPost } from './post.js';
import { createCameraRig, MODE_ALIASES } from './cameras.js';
import { createResolutionController, detectDevice, QUALITY_ALIASES, TIERS } from './quality.js';
import { hourDelta } from './timeofday.js';
import { createGpuTimer } from './gputimer.js';
import { loadViewLut } from './viewlut.js';
import * as ui from './ui.js';

const params = new URLSearchParams(location.search);
const ASSET_BASE = `${import.meta.env.BASE_URL}assets/`;
const TOD_ALIASES = { morning: 'morning', 清晨: 'morning', day: 'day', 白天: 'day', dusk: 'dusk', 黄昏: 'dusk', night: 'night', 夜晚: 'night' };

ui.installErrorHandlers();

/** ?preset= takes camera and / or time presets (e.g. ?preset=closeup,night). */
function readPresets() {
  const tokens = (params.get('preset') || '')
    .split(/[,，+ ]/)
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  let camera = null;
  let tod = null;
  for (const t of tokens) {
    if (MODE_ALIASES[t]) camera = MODE_ALIASES[t];
    else if (TOD_ALIASES[t]) tod = TOD_ALIASES[t];
  }
  return { camera, tod };
}

async function main() {
  const container = document.getElementById('app');
  const renderer = new WebGLRenderer({ antialias: false, alpha: false, powerPreference: 'high-performance', stencil: false });
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = NoToneMapping; // Blender's view transform is applied once, in the post Look pass
  renderer.shadowMap.enabled = false; // the key-light shadow map is rendered by shadows.js
  renderer.info.autoReset = false;
  renderer.debug.onShaderError = (gl, program, vs, fs) => {
    const log = [gl.getProgramInfoLog(program), gl.getShaderInfoLog(vs), gl.getShaderInfoLog(fs)].filter(Boolean).join('\n');
    console.error('Shader error', log);
    ui.showError(log || 'shader compile/link failed', 'shader');
  };
  const device = detectDevice(renderer.getContext());
  let quality = QUALITY_ALIASES[params.get('quality')] || 'auto';
  let tier = TIERS[quality === 'auto' ? device.tier : quality];
  const dpr = window.devicePixelRatio || 1;
  renderer.setPixelRatio(Math.min(dpr, tier.maxPixelRatio));
  renderer.setSize(window.innerWidth, window.innerHeight);
  container.appendChild(renderer.domElement);
  renderer.domElement.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    ui.showError('WebGL context lost', 'webgl');
  });

  const [assets, viewLut] = await Promise.all([
    loadAssets(ASSET_BASE, ui.setLoading, renderer.capabilities.getMaxAnisotropy()),
    loadViewLut(),
  ]);
  const { manifest } = assets;
  const lighting = createLighting(manifest);
  const world = buildWorld(assets, lighting, renderer);
  const { forest, shadows, grass, fireflies } = world;

  const rig = createCameraRig({
    gltf: assets.gltf,
    manifest,
    forest,
    domElement: renderer.domElement,
    interactionRoot: container,
    onModeChange: (mode) => ui.reflectState({ preset: mode }),
  });
  const camera = rig.camera;
  world.scene.add(camera);

  const post = createPost(renderer, world.scene, camera, lighting, manifest, tier, viewLut);
  const { composer } = post;

  // ---------------------------------------------------------------- resolution
  const resolution = createResolutionController({
    targetFps: device.targetFps,
    onChange: (pr) => {
      renderer.setPixelRatio(pr);
      composer.setSize(window.innerWidth, window.innerHeight);
    },
  });

  // highest pixel ratio of the tier; on phones also within the tier's pixel budget (render targets scale with the
  // pixel count, and the memory budget is a phone's)
  function maxPixelRatio() {
    const css = Math.max(window.innerWidth * window.innerHeight, 1);
    const pixels = device.mobile ? tier.maxPixels || Infinity : Infinity;
    return Math.min(dpr, tier.maxPixelRatio, Math.sqrt(pixels / css));
  }

  function resize() {
    const max = maxPixelRatio();
    if (Math.abs(max - resolution.state.max) > 1e-3) resolution.setRange(Math.min(tier.minPixelRatio, max), max);
    composer.setSize(window.innerWidth, window.innerHeight);
    rig.setAspect(window.innerWidth / Math.max(window.innerHeight, 1));
  }

  function applyTier() {
    world.setTier(tier);
    post.setTier(tier);
    const max = maxPixelRatio();
    resolution.setRange(Math.min(tier.minPixelRatio, max), max, true);
    resize();
  }

  function setQuality(name) {
    const key = QUALITY_ALIASES[name];
    if (!key) throw new Error(`unknown quality "${name}"`);
    quality = key;
    tier = TIERS[key === 'auto' ? device.tier : key];
    applyTier();
    ui.reflectState({ quality, resolvedQuality: tier.key });
  }

  // ---------------------------------------------------------------- time of day
  const tod = lighting.tod;
  let sliderHour = tod.defaultHour;
  let todAnim = null;
  function setHour(h, { fromSlider = false } = {}) {
    if (!fromSlider) todAnim = null;
    sliderHour = Math.min(Math.max(h, 0), 24);
    if (!Number.isFinite(sliderHour)) sliderHour = 0;
    lighting.setHour(sliderHour);
    const preset = Object.entries(tod.presets).find(([, ph]) => Math.abs(hourDelta(ph, sliderHour)) < 0.01)?.[0] ?? '';
    ui.reflectState({ sliderHour, todPreset: preset });
  }
  function animateToHour(target, duration = 1.4) {
    const from = lighting.hour;
    const delta = hourDelta(from, target);
    todAnim = { from, delta, t: 0, duration };
  }
  const setTimePreset = (name) => {
    const key = TOD_ALIASES[name] || name;
    if (!(key in tod.presets)) throw new Error(`unknown time preset "${name}"`);
    animateToHour(tod.presets[key]);
  };

  const setPreset = (name, opts) => rig.setMode(name, opts);

  const presets = readPresets();
  const hourParam = params.get('hour');
  setHour(hourParam !== null && hourParam !== '' && Number.isFinite(Number(hourParam)) ? Number(hourParam) : presets.tod ? tod.presets[presets.tod] : tod.defaultHour);
  applyTier();
  const initialCam = presets.camera || 'overview';
  setPreset(initialCam === 'free' ? 'overview' : initialCam, { instant: true });
  if (initialCam === 'free') setPreset('free');
  window.addEventListener('resize', resize);
  window.visualViewport?.addEventListener('resize', resize);

  ui.initControls({
    onHour: (h) => setHour(h, { fromSlider: false }),
    onTimePreset: (p) => setTimePreset(p),
    onPreset: (p) => setPreset(p),
    onQuality: (q) => setQuality(q),
    touch: device.mobile || (window.matchMedia?.('(pointer: coarse)').matches ?? false),
  });
  ui.reflectState({ quality, resolvedQuality: tier.key, sliderHour });

  ui.setLoading(0.97, '编译着色器');
  const size0 = renderer.getDrawingBufferSize(new Vector2());
  forest.update(camera, size0.y);
  // compile everything up front, including what only shows later (fireflies at night, every tree LOD)
  const hiddenNow = [];
  world.scene.traverse((o) => {
    if (!o.visible && (o.isMesh || o.isPoints)) {
      hiddenNow.push(o);
      o.visible = true;
    }
  });
  await renderer.compileAsync(world.scene, camera);
  for (const o of hiddenNow) o.visible = false;
  fireflies.update();
  forest.markDirty();

  // ---------------------------------------------------------------- frame loop, FPS badge, debug panel
  const hud = ui.createDebugHud(params.has('debug'));
  const gpuTimer = createGpuTimer(renderer.getContext());
  gpuTimer.enabled = hud.open;
  const fpsBadge = ui.createFpsBadge(() => {
    hud.toggle();
    gpuTimer.enabled = hud.open;
  });
  const tmpSize = new Vector2();
  const frameTimes = [];
  let last = performance.now();
  let frames = 0;
  const totals = { calls: 0, triangles: 0 };

  function textureBytes() {
    let b = 0;
    for (const t of Object.values(assets.textures)) {
      const img = t.image;
      const bpp = t.format === 1028 ? 1 : 4; // RedFormat : RGBA
      b += img.width * img.height * bpp * (t.generateMipmaps ? 4 / 3 : 1);
    }
    const lut = viewLut.image;
    return b + lut.width * lut.height * lut.depth * 4;
  }
  const assetTextureBytes = textureBytes();

  /** mean frame time (ms) over the last 120 frames */
  function averageFrameMs() {
    const n = frameTimes.length;
    return n ? frameTimes.reduce((a, b) => a + b, 0) / n : 0;
  }

  /** what the FPS badge shows (ui.createFpsBadge) */
  function badge() {
    const avg = averageFrameMs();
    return { fps: avg ? 1000 / avg : 0, tier: tier.key, pixelRatio: renderer.getPixelRatio() };
  }

  /** what the debug panel prints (ui.createDebugHud) */
  function stats() {
    const avg = averageFrameMs();
    const size = renderer.getDrawingBufferSize(tmpSize);
    const targetBytes = post.bytes(size.x, size.y) + shadows.bytes;
    return {
      fps: avg ? 1000 / avg : 0,
      frameMs: avg,
      calls: totals.calls,
      triangles: totals.triangles,
      sceneCalls: post.sceneStats.calls,
      sceneTriangles: post.sceneStats.triangles,
      grass: grass.count,
      quality,
      tier: tier.key,
      preset: rig.mode,
      hour: lighting.hour,
      pixelRatio: renderer.getPixelRatio(),
      minPixelRatio: resolution.state.min,
      maxPixelRatio: resolution.state.max,
      targetFps: Math.round(1000 / resolution.state.targetMs),
      width: size.x,
      height: size.y,
      shadowMap: shadows.state.size,
      shadowRenders: shadows.state.renders,
      shadowCalls: shadows.state.info.calls,
      shadowTriangles: shadows.state.info.triangles,
      shadowCpuMs: shadows.state.lastCpuMs,
      treeLods: forest.state.counts,
      textureBytes: assetTextureBytes + targetBytes,
      targetBytes,
      gpuMs: gpuTimer.ms,
    };
  }

  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  function frame() {
    const now = performance.now();
    const ms = now - last;
    const dt = Math.min(ms / 1000, 0.1);
    frameTimes.push(ms);
    if (frameTimes.length > 120) frameTimes.shift();
    last = now;
    lighting.U.uTime.value += dt;

    if (todAnim) {
      const a = todAnim;
      a.t = Math.min(1, a.t + dt / a.duration);
      const h = (((a.from + a.delta * easeInOut(a.t)) % 24) + 24) % 24;
      setHour(h, { fromSlider: true });
      if (a.t >= 1) todAnim = null;
    }

    const focus = rig.update(dt);
    const size = renderer.getDrawingBufferSize(tmpSize);
    forest.update(camera, size.y);
    const tanHalf = Math.tan(MathUtils.degToRad(camera.fov) / 2);
    grass.setPixelAngle((2 * tanHalf) / size.y);
    fireflies.setPixelsPerMeter(size.y / (2 * tanHalf), size.y);
    fireflies.update();
    post.update(camera, focus, size.x, size.y);

    if (forest.state.shadowDirty) {
      shadows.markDirty();
      forest.state.shadowDirty = false;
    }
    gpuTimer.begin();
    renderer.info.reset();
    shadows.update();
    renderer.info.reset();
    composer.render(dt);
    gpuTimer.end();
    totals.calls = renderer.info.render.calls;
    totals.triangles = renderer.info.render.triangles;
    frames++;
    resolution.sample(ms, now);
    if (frames === 1) ui.hideLoading();
    fpsBadge(badge);
    hud.update(stats);
  }

  ui.setLoading(1, '完成');
  renderer.setAnimationLoop(frame);
}

main().catch((err) => {
  console.error(err);
  ui.showError(err, 'load');
});
