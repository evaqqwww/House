// One directional shadow map for the key light (sun or moon), rendered on demand: when the light direction changes
// (time of day) or the set of casters changes (close-up corridor). The casters live in their own scene, so they can
// be cheaper than what the camera sees: house + rocks + terrain (back faces) and the L2 tree proxies.
// Depth only: a 16-bit depth texture with hardware comparison (sampler2DShadow, bilinear PCF), sampled by every
// material through the shared uniforms uShadowMap / uShadowMatrix / uShadowParams (see shaderlib.js).
import {
  DepthTexture,
  LessEqualCompare,
  LinearFilter,
  Matrix4,
  Mesh,
  NearestFilter,
  OrthographicCamera,
  RedFormat,
  Scene,
  ShaderMaterial,
  UnsignedByteType,
  UnsignedShortType,
  Vector3,
  WebGLRenderTarget,
  BackSide,
  DoubleSide,
} from 'three';

const DEPTH_VERTEX = /* glsl */ `
void main() {
  vec4 p = vec4( position, 1.0 );
  #ifdef USE_INSTANCING
    p = instanceMatrix * p;
  #endif
  gl_Position = projectionMatrix * modelViewMatrix * p;
}`;
const DEPTH_FRAGMENT = 'void main() { gl_FragColor = vec4( 1.0 ); }';

// tree proxies: leaves pushed out along the canopy normal, so the coarse L2 canopy casts a shadow as full as L0's
const TREE_DEPTH_VERTEX = /* glsl */ `
attribute float aLeaf;
uniform float uInflate;
void main() {
  vec4 p = vec4( position + normal * aLeaf * uInflate, 1.0 );
  #ifdef USE_INSTANCING
    p = instanceMatrix * p;
  #endif
  gl_Position = projectionMatrix * modelViewMatrix * p;
}`;

const TREE_INFLATE = 0.2; // metres along the canopy normal

export function createTreeDepthMaterial() {
  return new ShaderMaterial({
    name: 'ShadowDepthTrees',
    uniforms: { uInflate: { value: TREE_INFLATE } },
    vertexShader: TREE_DEPTH_VERTEX,
    fragmentShader: DEPTH_FRAGMENT,
    side: DoubleSide,
    colorWrite: false,
  });
}

const PENUMBRA = 0.38; // metres covered by the PCF tap grid
const BIAS = new Matrix4().set(0.5, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0, 1);

/**
 * @param {import('three').WebGLRenderer} renderer
 * @param {ReturnType<import('./lighting.js').createLighting>} lighting
 * @param {{min: Vector3, max: Vector3}} region  world box that receives shadows (casters may lie outside it)
 */
export function createShadows(renderer, lighting, region) {
  const scene = new Scene();
  scene.name = 'ShadowCasters';
  scene.matrixWorldAutoUpdate = false;
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.1, 500);
  // static casters are closed meshes and the terrain: their back faces
  const depthMaterial = new ShaderMaterial({ name: 'ShadowDepth', vertexShader: DEPTH_VERTEX, fragmentShader: DEPTH_FRAGMENT, side: BackSide, colorWrite: false });
  const { U } = lighting;
  const state = {
    size: 0,
    target: null,
    renderedDir: new Vector3(0, 0, 0),
    dirty: true,
    renders: 0,
    lastCpuMs: 0,
    texelWorld: 0.05,
    taps: 4,
    info: { calls: 0, triangles: 0 },
  };

  /** PCF taps of the tier's shaders (1, 4 or 16): sets the tap spacing on the next render */
  function setTaps(taps) {
    if (state.taps === taps) return;
    state.taps = taps;
    state.dirty = true;
  }

  function setSize(size) {
    if (state.size === size) return;
    state.target?.depthTexture?.dispose();
    state.target?.dispose();
    const depthTexture = new DepthTexture(size, size, UnsignedShortType);
    depthTexture.compareFunction = LessEqualCompare;
    depthTexture.minFilter = LinearFilter;
    depthTexture.magFilter = LinearFilter;
    depthTexture.name = 'ShadowMap';
    // the colour attachment is never written (colorWrite = false); R8 keeps it small
    state.target = new WebGLRenderTarget(size, size, {
      format: RedFormat,
      type: UnsignedByteType,
      minFilter: NearestFilter,
      magFilter: NearestFilter,
      generateMipmaps: false,
      depthBuffer: true,
      depthTexture,
    });
    state.target.texture.name = 'ShadowMap.Color';
    state.size = size;
    U.uShadowMap.value = depthTexture;
    state.dirty = true;
  }

  /** A static caster sharing the geometry of a scene mesh. */
  function addMesh(geometry, matrixWorld) {
    const m = new Mesh(geometry, depthMaterial);
    m.matrixAutoUpdate = false;
    m.matrix.copy(matrixWorld);
    m.matrixWorld.copy(matrixWorld);
    m.frustumCulled = false;
    scene.add(m);
    state.dirty = true;
  }

  /** An InstancedMesh owned by the caller (the tree proxies, with createTreeDepthMaterial). */
  function addObject(object) {
    object.matrixAutoUpdate = false;
    object.updateMatrix();
    object.matrixWorld.copy(object.matrix);
    scene.add(object);
    state.dirty = true;
  }

  const corner = new Vector3();
  const up = new Vector3();
  function fit(dir) {
    const c = new Vector3().addVectors(region.min, region.max).multiplyScalar(0.5);
    camera.position.copy(c).addScaledVector(dir, 200);
    up.set(0, 1, 0);
    if (Math.abs(dir.y) > 0.99) up.set(0, 0, 1);
    camera.up.copy(up);
    camera.lookAt(c);
    camera.updateMatrixWorld();
    const view = camera.matrixWorldInverse;
    let l = Infinity, r = -Infinity, b = Infinity, t = -Infinity, zn = -Infinity, zf = Infinity;
    for (let i = 0; i < 8; i++) {
      corner.set(i & 1 ? region.max.x : region.min.x, i & 2 ? region.max.y : region.min.y, i & 4 ? region.max.z : region.min.z);
      corner.applyMatrix4(view);
      l = Math.min(l, corner.x);
      r = Math.max(r, corner.x);
      b = Math.min(b, corner.y);
      t = Math.max(t, corner.y);
      zn = Math.max(zn, corner.z);
      zf = Math.min(zf, corner.z);
    }
    camera.left = l;
    camera.right = r;
    camera.bottom = b;
    camera.top = t;
    // casters outside the region (toward the light) still shade it: extend the near plane toward the light
    camera.near = Math.max(0.1, -zn - 80);
    camera.far = -zf + 2;
    camera.updateProjectionMatrix();
    U.uShadowMatrix.value.copy(BIAS).multiply(camera.projectionMatrix).multiply(view);
    state.texelWorld = Math.max(r - l, t - b) / state.size;
    const depthRange = camera.far - camera.near;
    const p = U.uShadowParams.value;
    p.x = 1 / state.size;
    // tap spacing (shaderlib.js): the tap grid spans about PENUMBRA metres on every tier (Cycles' 5 degree sun
    // blurs canopy shadows by ~0.4 m); a single tap is the hardware 2 x 2 PCF only
    const span = state.taps >= 16 ? 3 : state.taps >= 4 ? 1 : 0;
    p.y = span ? Math.max(2, PENUMBRA / (span * state.texelWorld)) : 1;
    p.z = 0.03 / depthRange; // constant bias: 3 cm along the light
    p.w = 1.5 * state.texelWorld; // normal offset of the receivers
  }

  /** true when the next update() will render (key light moved or casters changed) */
  function pending() {
    return state.dirty || lighting.state.keyDir.dot(state.renderedDir) <= 0.9999995;
  }

  /** Renders when the key light moved or the casters changed. */
  function update() {
    if (!pending()) return;
    const dir = lighting.state.keyDir;
    fit(dir);
    const t0 = performance.now();
    const info = renderer.info.render;
    const c0 = info.calls;
    const t0tri = info.triangles;
    const prev = renderer.getRenderTarget();
    renderer.setRenderTarget(state.target);
    renderer.clear(true, true, false);
    renderer.render(scene, camera);
    renderer.setRenderTarget(prev);
    state.info.calls = info.calls - c0;
    state.info.triangles = info.triangles - t0tri;
    state.lastCpuMs = performance.now() - t0;
    state.renderedDir.copy(dir);
    state.dirty = false;
    state.renders++;
  }

  function markDirty() {
    state.dirty = true;
  }

  return {
    state,
    setSize,
    setTaps,
    addMesh,
    addObject,
    update,
    markDirty,
    /** bytes of GPU memory (16-bit depth + R8 colour) */
    get bytes() {
      return state.size * state.size * 3;
    },
  };
}
