// Camera presets from the glTF cameras (pose + extras), the free orbit mode (one finger / mouse drag orbits, pinch /
// wheel zooms, two fingers / right drag pans) and smooth transitions. Close-up corridor: the tree instances listed in
// manifest.cameras.CAM_Closeup.corridor.hide are hidden while that preset is shown.
// Field of view: Blender's AUTO sensor fit in landscape (the sensor spans the width); portrait keeps most of the
// preset's horizontal field of view (see applyFov).
import { MathUtils, PerspectiveCamera, Quaternion, Vector3 } from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const PRESET_CAMERAS = { overview: 'CAM_Overview', closeup: 'CAM_Closeup' };
export const MODE_ALIASES = {
  overview: 'overview', 总览: 'overview',
  closeup: 'closeup', 特写: 'closeup',
  free: 'free', 自由: 'free',
};

const HOUSE = new Vector3(0, 1.5, 0);
const PIVOT_RADIUS = 14;

/** Preset from the glTF camera (pose, extras lens_mm / sensor_mm / target / fstop) and manifest.cameras[name]. */
function readPreset(gltf, manifest, name) {
  const cam = gltf.cameras.find((c) => c.userData.name === name); // the glTF node name (three.js may suffix .name)
  if (!cam) throw new Error(`camera ${name} not found in scene.glb`);
  const info = manifest.cameras[name];
  const extras = cam.userData;
  const position = new Vector3();
  const quaternion = new Quaternion();
  cam.updateWorldMatrix(true, false);
  cam.matrixWorld.decompose(position, quaternion, new Vector3());
  const forward = new Vector3(0, 0, -1).applyQuaternion(quaternion);

  const lens = Number(extras.lens_mm);
  const hfov = 2 * Math.atan(Number(extras.sensor_mm) / 2 / lens);
  const lensM = lens / 1000;
  const target = new Vector3().fromArray(extras.target);
  // orbit pivot: on the view axis, at the look-at distance
  const lookAt = position.clone().addScaledVector(forward, position.distanceTo(new Vector3().fromArray(info.lookAt)));
  // tree instances (indices into manifest.trees.instances) hidden while the preset is shown
  const corridor = info.corridor.hide;

  return {
    position, quaternion, hfov, lensM, aperture: lensM / Number(extras.fstop), target, lookAt, corridor,
    near: cam.near, far: cam.far,
  };
}

function easeInOut(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

export function createCameraRig({ gltf, manifest, forest, domElement, interactionRoot, onModeChange }) {
  const presets = {};
  for (const [key, name] of Object.entries(PRESET_CAMERAS)) presets[key] = readPreset(gltf, manifest, name);

  const camera = new PerspectiveCamera(30, 16 / 9, presets.overview.near, presets.overview.far);
  camera.name = 'ViewCamera';
  const controls = new OrbitControls(camera, domElement);
  controls.enabled = false;
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 6;
  controls.maxDistance = 70;
  controls.minPolarAngle = 0.12;
  controls.maxPolarAngle = MathUtils.degToRad(80);
  controls.screenSpacePanning = false;
  controls.rotateSpeed = 0.6;
  controls.zoomSpeed = 0.8;
  controls.panSpeed = 0.6;
  controls.addEventListener('change', () => {
    const t = controls.target;
    const d = new Vector3(t.x - HOUSE.x, 0, t.z - HOUSE.z);
    if (d.length() > PIVOT_RADIUS) d.setLength(PIVOT_RADIUS);
    t.set(HOUSE.x + d.x, MathUtils.clamp(t.y, 0, 6), HOUSE.z + d.z);
  });

  const state = {
    mode: 'overview',
    hfov: presets.overview.hfov,
    focusTarget: presets.overview.target.clone(),
    aperture: presets.overview.aperture,
    lensM: presets.overview.lensM,
    ref: presets.overview, // preset whose aperture/distance ratio the free mode keeps
    anim: null,
    aspect: 16 / 9,
  };

  // Landscape: Blender's AUTO sensor fit (the sensor spans the width). Portrait: the sensor would span the height and
  // crop the scene to a narrow strip, so the width keeps most of the preset's horizontal field of view instead.
  function applyFov() {
    const a = state.aspect;
    const tanH = Math.tan(state.hfov / 2);
    const vfov = a >= 1 ? 2 * Math.atan(tanH / a) : 2 * Math.atan((tanH * (0.45 + 0.55 * a)) / a);
    camera.fov = MathUtils.radToDeg(vfov);
    camera.aspect = a;
    camera.updateProjectionMatrix();
  }

  function setAspect(aspect) {
    state.aspect = aspect;
    applyFov();
  }

  function setMode(name, { instant = false } = {}) {
    const mode = MODE_ALIASES[String(name).toLowerCase()];
    if (!mode) throw new Error(`unknown camera preset "${name}"`);
    const prev = state.mode;
    state.mode = mode;
    if (mode === 'free') {
      if (state.anim) finishAnim();
      if (prev !== 'free') controls.target.copy(presets[prev].lookAt);
      controls.enabled = true;
      controls.update();
      if (prev === 'closeup') forest.setCorridor([]);
    } else {
      const p = presets[mode];
      controls.enabled = false;
      forest.clearOccluders();
      state.ref = p;
      forest.setCorridor(p.corridor);
      const from = {
        position: camera.position.clone(), quaternion: camera.quaternion.clone(), hfov: state.hfov,
        focusTarget: state.focusTarget.clone(), aperture: state.aperture, lensM: state.lensM,
      };
      if (instant) {
        state.anim = null;
        camera.position.copy(p.position);
        camera.quaternion.copy(p.quaternion);
        state.hfov = p.hfov;
        state.focusTarget.copy(p.target);
        state.aperture = p.aperture;
        state.lensM = p.lensM;
        applyFov();
      } else {
        state.anim = { from, to: p, t: 0, duration: 1.4 };
      }
    }
    camera.updateMatrixWorld();
    onModeChange?.(mode);
  }

  // dragging or zooming while a preset is shown switches to the free mode (capture phase: before OrbitControls)
  const takeOver = () => {
    if (state.mode !== 'free') {
      if (state.anim) finishAnim();
      setMode('free');
    }
  };
  interactionRoot.addEventListener('pointerdown', takeOver, { capture: true });
  interactionRoot.addEventListener('wheel', takeOver, { capture: true, passive: true });

  function finishAnim() {
    const p = state.anim.to;
    camera.position.copy(p.position);
    camera.quaternion.copy(p.quaternion);
    state.hfov = p.hfov;
    state.focusTarget.copy(p.target);
    state.aperture = p.aperture;
    state.lensM = p.lensM;
    state.anim = null;
    applyFov();
  }

  function update(dt) {
    if (state.anim) {
      const a = state.anim;
      a.t = Math.min(1, a.t + dt / a.duration);
      const k = easeInOut(a.t);
      camera.position.lerpVectors(a.from.position, a.to.position, k);
      camera.quaternion.slerpQuaternions(a.from.quaternion, a.to.quaternion, k);
      state.hfov = MathUtils.lerp(a.from.hfov, a.to.hfov, k);
      state.focusTarget.lerpVectors(a.from.focusTarget, a.to.target, k);
      state.aperture = MathUtils.lerp(a.from.aperture, a.to.aperture, k);
      state.lensM = MathUtils.lerp(a.from.lensM, a.to.lensM, k);
      applyFov();
      if (a.t >= 1) finishAnim();
    } else if (state.mode === 'free') {
      controls.update(dt);
      forest.updateOccluders(camera.position, controls.target);
      state.focusTarget.copy(controls.target);
      const ref = state.ref;
      const s = camera.position.distanceTo(controls.target);
      state.aperture = ref.aperture * (s / ref.position.distanceTo(ref.target));
    }
    camera.updateMatrixWorld();
    return {
      distance: camera.position.distanceTo(state.focusTarget),
      aperture: state.aperture,
      lensM: state.lensM,
    };
  }

  return {
    camera,
    setMode,
    setAspect,
    update,
    get mode() {
      return state.mode;
    },
  };
}
