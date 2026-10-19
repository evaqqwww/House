// Trees and bushes: per-instance level of detail. The GLB carries every variant as <Variant>_L0/_L1/_L2 meshes
// (bark + leaves primitives, merged here into one geometry with an aLeaf flag); manifest.trees.instances places
// them. One InstancedMesh per variant x LOD holds the instances currently drawn at that level; the lists are rebuilt
// when the camera has moved enough (frustum culling per instance, LOD by distance scaled to the screen's pixel
// density, with hysteresis). The L2 meshes double as the shadow casters (one InstancedMesh per variant in the
// shadow scene). Hiding: the close-up corridor (manifest list, colour + shadow) and, in the free camera, trees
// whose canopy crosses the camera-target line (colour pass only).
import {
  BufferAttribute,
  BufferGeometry,
  Frustum,
  InstancedMesh,
  MathUtils,
  Matrix4,
  Quaternion,
  Sphere,
  Vector3,
  DynamicDrawUsage,
} from 'three';

const LOD_HYSTERESIS = 0.06;
const REF_VFOV = 22.9; // overview camera, 16:9
const REF_HEIGHT = 1080;

function toFloat(attr) {
  const n = attr.count;
  const k = attr.itemSize;
  const out = new Float32Array(n * k);
  for (let i = 0; i < n; i++) {
    out[i * k] = attr.getX(i);
    if (k > 1) out[i * k + 1] = attr.getY(i);
    if (k > 2) out[i * k + 2] = attr.getZ(i);
    if (k > 3) out[i * k + 3] = attr.getW(i);
  }
  return out;
}

/**
 * Merges bark + leaves primitives into one indexed geometry: position, normal, color and aLeaf = (leaf flag, height
 * in the canopy: 0 at the lowest leaf, 1 at the top; bark 0).
 */
function mergeParts(parts) {
  let vtx = 0;
  let idx = 0;
  for (const { geometry } of parts) {
    vtx += geometry.attributes.position.count;
    idx += geometry.index ? geometry.index.count : geometry.attributes.position.count;
  }
  const pos = new Float32Array(vtx * 3);
  const nrm = new Float32Array(vtx * 3);
  const col = new Float32Array(vtx * 3);
  const leaf = new Uint8Array(vtx * 2);
  let y0 = Infinity;
  let y1 = -Infinity;
  for (const { geometry, isLeaf } of parts) {
    if (!isLeaf) continue;
    const pa = geometry.attributes.position;
    for (let i = 0; i < pa.count; i++) {
      y0 = Math.min(y0, pa.getY(i));
      y1 = Math.max(y1, pa.getY(i));
    }
  }
  const index = vtx > 65535 ? new Uint32Array(idx) : new Uint16Array(idx);
  let vo = 0;
  let io = 0;
  for (const { geometry, isLeaf } of parts) {
    const g = geometry;
    const n = g.attributes.position.count;
    pos.set(toFloat(g.attributes.position), vo * 3);
    nrm.set(toFloat(g.attributes.normal), vo * 3);
    const c = toFloat(g.attributes.color);
    const k = g.attributes.color.itemSize;
    for (let i = 0; i < n; i++) {
      col[(vo + i) * 3] = c[i * k];
      col[(vo + i) * 3 + 1] = c[i * k + 1];
      col[(vo + i) * 3 + 2] = c[i * k + 2];
    }
    for (let i = 0; i < n; i++) {
      leaf[(vo + i) * 2] = isLeaf ? 255 : 0; // normalized: 255 -> 1.0
      leaf[(vo + i) * 2 + 1] = isLeaf && y1 > y0 ? Math.round(255 * Math.min(Math.max((g.attributes.position.getY(i) - y0) / (y1 - y0), 0), 1)) : 0;
    }
    if (g.index) {
      for (let i = 0; i < g.index.count; i++) index[io + i] = g.index.getX(i) + vo;
      io += g.index.count;
    } else {
      for (let i = 0; i < n; i++) index[io + i] = vo + i;
      io += n;
    }
    vo += n;
  }
  const out = new BufferGeometry();
  out.setAttribute('position', new BufferAttribute(pos, 3));
  out.setAttribute('normal', new BufferAttribute(nrm, 3));
  out.setAttribute('color', new BufferAttribute(col, 3));
  out.setAttribute('aLeaf', new BufferAttribute(leaf, 2, true));
  out.setIndex(new BufferAttribute(index, 1));
  out.computeBoundingBox();
  out.computeBoundingSphere();
  return out;
}

/** Collects the treeLOD templates from the glTF scene (and detaches them). */
export function collectTreeTemplates(root) {
  const nodes = [];
  root.traverse((o) => {
    if (o.userData?.shade === 'treeLOD') nodes.push(o);
  });
  const variants = new Map();
  for (const node of nodes) {
    const { variant } = node.userData;
    const lod = Number(node.userData.lod);
    const meshes = [];
    node.traverse((m) => {
      if (m.isMesh) meshes.push(m);
    });
    const parts = meshes.map((m) => ({ geometry: m.geometry, isLeaf: /leaves/i.test(m.material?.name || '') }));
    if (!variants.has(variant)) variants.set(variant, { name: variant, lods: [] });
    const v = variants.get(variant);
    v.lods[lod] = mergeParts(parts);
    v.isBush = /bush/i.test(variant);
    if (lod === 0) {
      const leaves = parts.find((p) => p.isLeaf) || parts[0];
      leaves.geometry.computeBoundingBox();
      v.canopy = leaves.geometry.boundingBox.clone();
    }
  }
  for (const node of nodes) node.removeFromParent();
  for (const v of variants.values()) v.tris = v.lods.map((g) => (g.index ? g.index.count : g.attributes.position.count) / 3);
  return variants;
}

/**
 * @param {Map} variants  from collectTreeTemplates
 * @param {object} manifest
 * @param {import('three').Material} material  the tree material (materials/trees.js)
 * @param {import('three').Material} depthMaterial  instanced depth material of the shadow scene
 */
export function createForest(variants, manifest, material, depthMaterial) {
  const list = manifest.trees.instances;
  const [d1, d2] = manifest.trees.lodDistances; // L0 -> L1 and L1 -> L2 switch distances

  const instances = [];
  const m = new Matrix4();
  const q = new Quaternion();
  const p = new Vector3();
  const s = new Vector3();
  for (const [i, it] of list.entries()) {
    const v = variants.get(it.variant);
    if (!v) continue;
    p.fromArray(it.p);
    q.fromArray(it.q);
    const sc = it.s;
    s.setScalar(sc);
    m.compose(p, q, s);
    const sphere = v.lods[0].boundingSphere.clone().applyMatrix4(m);
    const cb = v.canopy;
    const center = cb.getCenter(new Vector3()).applyMatrix4(m);
    const radius = 0.5 * Math.max(cb.max.x - cb.min.x, cb.max.z - cb.min.z) * sc;
    instances.push({ index: i, variant: v, matrix: m.clone(), sphere, center, radius, lod: -1, isBush: v.isBush });
  }

  // colour pass meshes: variant x LOD
  const meshes = new Map();
  const group = [];
  for (const v of variants.values()) {
    const n = instances.filter((it) => it.variant === v).length;
    if (!n) continue;
    const byLod = v.lods.map((geom, lod) => {
      const im = new InstancedMesh(geom, material, n);
      im.name = `${v.name}_L${lod}`;
      im.count = 0;
      im.frustumCulled = false;
      im.instanceMatrix.setUsage(DynamicDrawUsage);
      im.matrixAutoUpdate = false;
      group.push(im);
      return im;
    });
    meshes.set(v, byLod);
  }

  // shadow casters: L2 of every instance outside the corridor
  const casters = new Map();
  for (const v of variants.values()) {
    const n = instances.filter((it) => it.variant === v).length;
    if (!n) continue;
    const im = new InstancedMesh(v.lods[2], depthMaterial, n);
    im.name = `${v.name}_Shadow`;
    im.count = 0;
    im.frustumCulled = false;
    casters.set(v, im);
  }

  const state = {
    corridor: new Set(),
    occluders: new Set(),
    lodScale: 1,
    budget: Infinity, // tree triangles drawn at most (tier)
    dirty: true,
    shadowDirty: true,
    camPos: new Vector3(Infinity, 0, 0),
    camQuat: new Quaternion(),
    camFov: 0,
    pixelScale: 1,
    counts: [0, 0, 0],
  };

  function rebuildCasters() {
    for (const im of casters.values()) im.count = 0;
    for (const it of instances) {
      if (state.corridor.has(it.index)) continue;
      const im = casters.get(it.variant);
      im.setMatrixAt(im.count++, it.matrix);
    }
    for (const im of casters.values()) im.instanceMatrix.needsUpdate = true;
    state.shadowDirty = true;
  }
  rebuildCasters();

  const frustum = new Frustum();
  const proj = new Matrix4();
  const sph = new Sphere();

  function rebuild(camera, drawingHeight) {
    proj.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    frustum.setFromProjectionMatrix(proj);
    // distances are for a 1080p overview frame; scale by the current pixels per radian and the tier
    const tanHalf = Math.tan(MathUtils.degToRad(camera.fov) / 2);
    const ppu = drawingHeight / (2 * tanHalf);
    const refPpu = REF_HEIGHT / (2 * Math.tan(MathUtils.degToRad(REF_VFOV) / 2));
    const k = (refPpu / Math.max(ppu, 1)) / state.lodScale;
    for (const ims of meshes.values()) for (const im of ims) im.count = 0;
    const cam = camera.position;
    const visible = [];
    let tris = 0;
    for (const it of instances) {
      if (state.corridor.has(it.index) || state.occluders.has(it.index)) {
        it.lod = -1;
        continue;
      }
      sph.copy(it.sphere);
      sph.radius = sph.radius * 1.1 + 1.5;
      if (!frustum.intersectsSphere(sph)) {
        it.lod = -1;
        continue;
      }
      const d = Math.max(cam.distanceTo(it.sphere.center) - it.sphere.radius * 0.5, 0) * k;
      // hysteresis: a tree keeps its level until it is clearly past the switch distance
      const prev = it.lod;
      const h = LOD_HYSTERESIS;
      const t1 = prev === 0 ? d1 * (1 + h) : prev > 0 ? d1 * (1 - h) : d1;
      const t2 = prev === 2 ? d2 * (1 - h) : prev >= 0 ? d2 * (1 + h) : d2;
      it.lod = d < t1 ? 0 : d < t2 ? 1 : 2;
      visible.push({ it, d });
      tris += it.variant.tris[it.lod];
    }
    // triangle budget of the tier: demote the farthest trees first (L0 -> L1, then L1 -> L2)
    if (tris > state.budget) {
      visible.sort((a, b) => b.d - a.d);
      for (const from of [0, 1]) {
        for (const v of visible) {
          if (tris <= state.budget) break;
          if (v.it.lod !== from) continue;
          tris += v.it.variant.tris[from + 1] - v.it.variant.tris[from];
          v.it.lod = from + 1;
        }
      }
    }
    const counts = [0, 0, 0];
    for (const { it } of visible) {
      const im = meshes.get(it.variant)[it.lod];
      im.setMatrixAt(im.count++, it.matrix);
      counts[it.lod]++;
    }
    for (const ims of meshes.values()) {
      for (const im of ims) {
        im.visible = im.count > 0;
        if (im.count) im.instanceMatrix.needsUpdate = true;
      }
    }
    state.counts = counts;
  }

  /** Rebuilds the instance lists when the camera moved / turned / zoomed enough. */
  function update(camera, drawingHeight) {
    const moved = camera.position.distanceToSquared(state.camPos) > 0.25 * 0.25;
    const turned = 1 - Math.abs(camera.quaternion.dot(state.camQuat)) > 2e-5; // ~0.7 degrees
    const zoomed = Math.abs(camera.fov - state.camFov) > 0.2;
    const res = Math.abs(drawingHeight / state.pixelScale - 1) > 0.15;
    if (!(state.dirty || moved || turned || zoomed || res)) return;
    rebuild(camera, drawingHeight);
    state.camPos.copy(camera.position);
    state.camQuat.copy(camera.quaternion);
    state.camFov = camera.fov;
    state.pixelScale = drawingHeight;
    state.dirty = false;
  }

  function setCorridor(indices) {
    const next = new Set(indices);
    if (next.size === state.corridor.size && [...next].every((i) => state.corridor.has(i))) return;
    state.corridor = next;
    state.dirty = true;
    rebuildCasters();
  }

  /** free camera: trees (not bushes) whose canopy sphere the camera-target segment crosses (colour pass only) */
  const a = new Vector3();
  const dvec = new Vector3();
  const w = new Vector3();
  function updateOccluders(from, to, margin = 0.4) {
    a.copy(from);
    dvec.copy(to).sub(from);
    const len2 = Math.max(dvec.lengthSq(), 1e-9);
    const next = new Set();
    for (const it of instances) {
      if (it.isBush) continue;
      w.copy(it.center).sub(a);
      const t = MathUtils.clamp(w.dot(dvec) / len2, 0, 1);
      if (t >= 0.98) continue;
      if (w.addScaledVector(dvec, -t).length() < it.radius + margin) next.add(it.index);
    }
    if (next.size === state.occluders.size && [...next].every((i) => state.occluders.has(i))) return;
    state.occluders = next;
    state.dirty = true;
  }

  function clearOccluders() {
    if (!state.occluders.size) return;
    state.occluders = new Set();
    state.dirty = true;
  }

  return {
    group,
    casters: [...casters.values()],
    instances,
    state,
    update,
    setCorridor,
    updateOccluders,
    clearOccluders,
    setLodScale(s, budget = Infinity) {
      state.lodScale = s;
      state.budget = budget;
      state.dirty = true;
    },
    markDirty() {
      state.dirty = true;
    },
  };
}
