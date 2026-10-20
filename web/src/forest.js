// Trees and bushes: per-instance level of detail. The GLB carries every variant as <Variant>_L0/_L1/_L2 meshes with
// the primitives M_Bark, M_LeafCore (opaque inner core) and M_LeafCards (leaf-cluster cards, see
// materials/trees.js), and <Variant>_Shadow, the opaque hull that casts the variant's shadow. Bark and core are
// merged here into one geometry with an aLeaf flag; the cards keep their own geometry (plus aLeaf). manifest.trees
// .instances places the trees. Per variant x LOD one InstancedMesh draws the bark + core and one the cards, sharing
// the instance matrices, holding the instances currently drawn at that level; the lists are rebuilt when the camera
// has moved enough (frustum culling per instance, LOD by distance scaled to the screen's pixel density, with
// hysteresis, nearest instances first). Per tier the visible trees stay within a triangle budget and a card budget
// (cards are cheap in triangles but cost fill rate): over either, the farthest trees are demoted first. The colour
// pass draws the trees before the rest of the opaque scene, cores before cards, so the cores hide the cards behind
// them and the canopies hide the ground. Shadow casters: one InstancedMesh of the _Shadow hull per variant in the
// shadow scene. Hiding: the close-up corridor (manifest list, colour + shadow) and, in the free camera, trees whose
// canopy crosses the camera-target line (colour pass only).
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
const CARDS = /leafcards/i;
const BARK = /bark/i;

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

/** aLeaf = (leaf flag, height in the canopy between y0 and y1), normalized bytes */
function leafAttribute(position, isLeaf, y0, y1) {
  const n = position.count;
  const leaf = new Uint8Array(n * 2);
  for (let i = 0; i < n; i++) {
    leaf[i * 2] = isLeaf ? 255 : 0; // normalized: 255 -> 1.0
    leaf[i * 2 + 1] = isLeaf && y1 > y0 ? Math.round(255 * MathUtils.clamp((position.getY(i) - y0) / (y1 - y0), 0, 1)) : 0;
  }
  return new BufferAttribute(leaf, 2, true);
}

function yRange(geometries) {
  let y0 = Infinity;
  let y1 = -Infinity;
  for (const g of geometries) {
    const pa = g.attributes.position;
    for (let i = 0; i < pa.count; i++) {
      y0 = Math.min(y0, pa.getY(i));
      y1 = Math.max(y1, pa.getY(i));
    }
  }
  return [y0, y1];
}

/** Merges bark + leaf (core) primitives into one indexed geometry: position, normal, color and aLeaf. */
function mergeParts(parts, y0, y1) {
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
  const index = vtx > 65535 ? new Uint32Array(idx) : new Uint16Array(idx);
  let vo = 0;
  let io = 0;
  for (const { geometry: g, isLeaf } of parts) {
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
    leaf.set(leafAttribute(g.attributes.position, isLeaf, y0, y1).array, vo * 2);
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

/** The card primitive as loaded (position = card centre, normal, color, uv, _card) plus aLeaf, the card count and
 * the largest half size. */
function cardGeometry(source, y0, y1) {
  const g = new BufferGeometry();
  for (const name of ['position', 'normal', 'color', 'uv', '_card']) {
    const a = source.getAttribute(name);
    if (!a) throw new Error(`leaf cards without ${name === '_card' ? '_CARD' : name}`);
    g.setAttribute(name, a);
  }
  g.setIndex(source.index);
  g.setAttribute('aLeaf', leafAttribute(source.attributes.position, true, y0, y1));
  const card = g.attributes._card;
  let maxHalf = 0;
  for (let i = 0; i < card.count; i += 4) maxHalf = Math.max(maxHalf, card.getZ(i));
  g.computeBoundingBox();
  g.computeBoundingSphere();
  return { geometry: g, count: card.count / 4, maxHalf };
}

function triangles(g) {
  return (g.index ? g.index.count : g.attributes.position.count) / 3;
}

function meshesOf(node) {
  const meshes = [];
  node.traverse((m) => {
    if (m.isMesh) meshes.push(m);
  });
  return meshes;
}

/** Collects the tree templates (<Variant>_L0/_L1/_L2 and <Variant>_Shadow) from the glTF scene and detaches them. */
export function collectTreeTemplates(root) {
  const lodNodes = [];
  const shadowNodes = [];
  const walk = (o) => {
    const shade = o.userData?.shade;
    if (/_Shadow$/.test(o.name) && (shade === undefined || /^tree/.test(shade))) shadowNodes.push(o);
    else if (shade === 'treeLOD') lodNodes.push(o);
    else o.children.forEach(walk);
  };
  walk(root);

  const variants = new Map();
  const variantOf = (name) => {
    if (!variants.has(name)) variants.set(name, { name, lods: [], isBush: /bush/i.test(name) });
    return variants.get(name);
  };
  for (const node of lodNodes) {
    const v = variantOf(node.userData.variant);
    const parts = meshesOf(node).map((m) => {
      const mat = m.material?.name || '';
      return { geometry: m.geometry, isCards: CARDS.test(mat), isLeaf: !BARK.test(mat) };
    });
    const cardPart = parts.find((p) => p.isCards);
    const solidParts = parts.filter((p) => !p.isCards);
    // height in the canopy: over the card centres (or the leaf geometry without cards)
    const [y0, y1] = yRange(cardPart ? [cardPart.geometry] : solidParts.filter((p) => p.isLeaf).map((p) => p.geometry));
    const solid = mergeParts(solidParts, y0, y1);
    const cards = cardPart ? cardGeometry(cardPart.geometry, y0, y1) : null;
    v.lods[Number(node.userData.lod)] = {
      solid,
      cards: cards?.geometry ?? null,
      cardCount: cards?.count ?? 0,
      maxHalf: cards?.maxHalf ?? 0,
      tris: triangles(solid) + (cards ? triangles(cards.geometry) : 0),
    };
  }
  for (const node of shadowNodes) {
    const v = variantOf(node.userData?.variant || node.name.replace(/_Shadow$/, ''));
    const parts = meshesOf(node).map((m) => ({ geometry: m.geometry, isLeaf: !BARK.test(m.material?.name || '') }));
    v.shadow = mergeParts(parts, ...yRange(parts.filter((p) => p.isLeaf).map((p) => p.geometry)));
  }
  for (const node of [...lodNodes, ...shadowNodes]) node.removeFromParent();

  for (const [name, v] of variants) {
    if (!v.lods.length) {
      variants.delete(name);
      continue;
    }
    const l0 = v.lods[0];
    // bounds of the whole tree (cards reach a half size beyond their centres) and of the canopy
    v.sphere = l0.solid.boundingSphere.clone();
    if (l0.cards) {
      const cs = l0.cards.boundingSphere;
      v.sphere.union(new Sphere(cs.center.clone(), cs.radius + l0.maxHalf));
    }
    v.canopy = l0.cards ? l0.cards.boundingBox.clone().expandByScalar(0.5 * l0.maxHalf) : l0.solid.boundingBox.clone();
    v.shadow = v.shadow || v.lods[v.lods.length - 1].solid;
  }
  return variants;
}

/**
 * @param {Map} variants  from collectTreeTemplates
 * @param {object} manifest
 * @param {import('three').Material} material  bark + core material (materials/trees.js)
 * @param {import('three').Material|null} cardMaterial  leaf card material (materials/trees.js)
 * @param {import('three').Material} depthMaterial  instanced depth material of the shadow scene
 */
export function createForest(variants, manifest, material, cardMaterial, depthMaterial) {
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
    const sphere = v.sphere.clone().applyMatrix4(m);
    const cb = v.canopy;
    const center = cb.getCenter(new Vector3()).applyMatrix4(m);
    const radius = 0.5 * Math.max(cb.max.x - cb.min.x, cb.max.z - cb.min.z) * sc;
    instances.push({ index: i, variant: v, matrix: m.clone(), sphere, center, radius, lod: -1, isBush: v.isBush });
  }

  // colour pass meshes: variant x LOD, bark + core and cards
  const meshes = new Map();
  const group = [];
  const instanced = (geometry, mat, n, name, order) => {
    const im = new InstancedMesh(geometry, mat, n);
    im.name = name;
    im.count = 0;
    im.frustumCulled = false;
    im.matrixAutoUpdate = false;
    im.renderOrder = order;
    group.push(im);
    return im;
  };
  for (const v of variants.values()) {
    const n = instances.filter((it) => it.variant === v).length;
    if (!n) continue;
    const byLod = v.lods.map((lod, l) => {
      const solid = instanced(lod.solid, material, n, `${v.name}_L${l}`, -2);
      solid.instanceMatrix.setUsage(DynamicDrawUsage);
      let cards = null;
      if (lod.cards && cardMaterial) {
        cards = instanced(lod.cards, cardMaterial, n, `${v.name}_L${l}_Cards`, -1);
        cards.instanceMatrix = solid.instanceMatrix;
      }
      return { solid, cards };
    });
    meshes.set(v, byLod);
  }

  // shadow casters: the _Shadow hull of every instance outside the corridor
  const casters = new Map();
  for (const v of variants.values()) {
    const n = instances.filter((it) => it.variant === v).length;
    if (!n) continue;
    const im = new InstancedMesh(v.shadow, depthMaterial, n);
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
    cardBudget: Infinity, // leaf cards drawn at most (tier)
    dirty: true,
    shadowDirty: true,
    camPos: new Vector3(Infinity, 0, 0),
    camQuat: new Quaternion(),
    camFov: 0,
    pixelScale: 1,
    counts: [0, 0, 0],
    cards: 0, // leaf cards drawn
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
    const cam = camera.position;
    const visible = [];
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
    }
    // nearest first: within a mesh, near canopies hide the cards behind them
    visible.sort((a, b) => a.d - b.d);
    let tris = 0;
    let cards = 0;
    for (const { it } of visible) {
      tris += it.variant.lods[it.lod].tris;
      cards += it.variant.lods[it.lod].cardCount;
    }
    // budgets of the tier: demote the farthest trees first (L0 -> L1, then L1 -> L2)
    const over = () => tris > state.budget || cards > state.cardBudget;
    for (const from of [0, 1]) {
      for (let i = visible.length - 1; i >= 0 && over(); i--) {
        const it = visible[i].it;
        if (it.lod !== from) continue;
        const a = it.variant.lods[from];
        const b = it.variant.lods[from + 1];
        tris += b.tris - a.tris;
        cards += b.cardCount - a.cardCount;
        it.lod = from + 1;
      }
    }
    for (const ims of meshes.values()) for (const { solid } of ims) solid.count = 0;
    const counts = [0, 0, 0];
    for (const { it } of visible) {
      const { solid } = meshes.get(it.variant)[it.lod];
      solid.setMatrixAt(solid.count++, it.matrix);
      counts[it.lod]++;
    }
    for (const ims of meshes.values()) {
      for (const { solid, cards: c } of ims) {
        solid.visible = solid.count > 0;
        if (solid.count) solid.instanceMatrix.needsUpdate = true;
        if (c) {
          c.count = solid.count;
          c.visible = solid.visible;
        }
      }
    }
    state.counts = counts;
    state.cards = cards;
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
    /** tier: LOD distance scale, triangle and card budgets */
    setLodScale(scale, budget = Infinity, cardBudget = Infinity) {
      state.lodScale = scale;
      state.budget = budget;
      state.cardBudget = cardBudget;
      state.dirty = true;
    },
    markDirty() {
      state.dirty = true;
    },
  };
}
