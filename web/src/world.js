// Builds the scene from the loaded assets: classifies glTF nodes by extras.shade (lit / terrain / treeLOD / water /
// smoke), assigns the time-of-day materials, and sets up the shadow casters, the forest LOD, the grass, the sky dome
// and the fireflies. setTier() applies a quality tier to all of them.
import { Box3, Scene, Vector3 } from 'three';
import { createLitMaterial } from './materials/lit.js';
import { createTerrainMaterial, grassColorUniforms } from './materials/terrain.js';
import { createTreeMaterials, setCardAlphaMode } from './materials/trees.js';
import { createGrass, GRASS_DEFAULTS } from './materials/grass.js';
import { createWaterMaterial } from './materials/water.js';
import { createSmokeMaterial } from './materials/smoke.js';
import { createSkyDome } from './materials/sky.js';
import { createFireflies } from './materials/fireflies.js';
import { createShadows, createTreeDepthMaterial } from './shadows.js';
import { collectTreeTemplates, createForest } from './forest.js';

// lit meshes left out of the shadow map
const NON_CASTERS = /^(LilyPads|Reeds|House_Glass|House_LanternGlow)/;

function namesUp(obj) {
  const out = [];
  for (let o = obj; o; o = o.parent) {
    if (o.name) out.push(o.name);
    if (o.userData?.name) out.push(o.userData.name);
  }
  return out;
}

/** extras.shade of the mesh or of the glTF node above it (multi-primitive meshes) */
function classify(mesh) {
  for (let o = mesh; o; o = o.parent) if (o.userData?.shade) return o.userData.shade;
}

function findCamera(root, name) {
  let found = null;
  root.traverse((o) => {
    if (!found && o.isCamera && (o.name === name || o.userData?.name === name)) found = o;
  });
  return found;
}

/** Ground area the preset cameras can see (portrait or landscape), for the shadow map and the grass weighting. */
function presetViews(root) {
  const views = [];
  for (const name of ['CAM_Overview', 'CAM_Closeup']) {
    const cam = findCamera(root, name);
    if (!cam) continue;
    cam.updateWorldMatrix(true, false);
    const position = new Vector3().setFromMatrixPosition(cam.matrixWorld);
    const forward = new Vector3(0, 0, -1).transformDirection(cam.matrixWorld);
    const hfov = 2 * Math.atan(Number(cam.userData.sensor_mm) / 2 / Number(cam.userData.lens_mm));
    // portrait screens widen the vertical field of view (cameras.js); cover both orientations
    const half = Math.min(0.5 * hfov * 1.7, 0.75);
    views.push({ name, position, forward, half, cosHalf: Math.cos(half) });
  }
  return views;
}

function groundFootprint(views, half) {
  const box = new Box3();
  const dir = new Vector3();
  for (const v of views) {
    // rays on the cone around the view axis (and the axis itself) hitting the ground plane y = 0
    const right = new Vector3().crossVectors(v.forward, new Vector3(0, 1, 0)).normalize();
    const up = new Vector3().crossVectors(right, v.forward).normalize();
    for (let i = 0; i <= 16; i++) {
      const a = (i / 16) * Math.PI * 2;
      for (const r of [0, 0.5, 1]) {
        dir.copy(v.forward)
          .addScaledVector(right, Math.cos(a) * Math.tan(v.half) * r)
          .addScaledVector(up, Math.sin(a) * Math.tan(v.half) * r)
          .normalize();
        let t = dir.y < -1e-3 ? -v.position.y / dir.y : 140;
        t = Math.min(t, 140);
        box.expandByPoint(new Vector3().copy(v.position).addScaledVector(dir, t));
      }
    }
  }
  box.min.x = Math.max(box.min.x - 4, -half);
  box.min.z = Math.max(box.min.z - 4, -half);
  box.max.x = Math.min(box.max.x + 4, half);
  box.max.z = Math.min(box.max.z + 4, half);
  return box;
}

export function buildWorld({ manifest, gltf, textures, grassMap }, lighting, renderer) {
  const scene = new Scene();
  scene.name = 'Diorama';
  const half = manifest.terrainUV.half;

  const root = gltf.scene;
  scene.add(root);
  root.updateMatrixWorld(true);
  const variants = collectTreeTemplates(root);
  const views = presetViews(root);

  const colorUniforms = grassColorUniforms(manifest, GRASS_DEFAULTS);
  const lit = createLitMaterial(lighting);
  const litGlass = createLitMaterial(lighting, lighting.emission.M_House_Glass, 'Lit_Glass');
  const litLantern = createLitMaterial(lighting, lighting.emission.M_House_Lantern, 'Lit_Lantern');
  const terrain = createTerrainMaterial(lighting, textures, colorUniforms);
  const water = createWaterMaterial(manifest, lighting, textures);
  const { solid: trees, cards: leafCards } = createTreeMaterials(lighting, textures.leaves, manifest.trees.leafAtlas);
  const custom = [lit, litGlass, litLantern, terrain, water, trees];
  if (leafCards) custom.push(leafCards);

  const meshes = [];
  root.traverse((o) => {
    if (o.isMesh) meshes.push(o);
  });
  const casterMeshes = [];
  let terrainMesh = null;
  for (const mesh of meshes) {
    const shade = classify(mesh);
    const oldMat = mesh.material;
    const matName = oldMat?.name || '';
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.matrixAutoUpdate = false;
    switch (shade) {
      case 'terrain':
        mesh.material = terrain;
        terrainMesh = mesh;
        break;
      case 'water':
        mesh.material = water;
        mesh.renderOrder = 10;
        break;
      case 'smoke': {
        const m = createSmokeMaterial(mesh.geometry, lighting);
        custom.push(m);
        mesh.material = m;
        mesh.renderOrder = 20;
        break;
      }
      default: {
        // baked layers: COLOR_1 / COLOR_2 after scripts/optimize-glb.mjs, _SKY / _BOUNCE straight from the exporter
        const g = mesh.geometry;
        const sky = g.getAttribute('color_1') || g.getAttribute('_sky');
        const bounce = g.getAttribute('color_2') || g.getAttribute('_bounce');
        if (sky) g.setAttribute('aSky', sky);
        else console.warn(`${mesh.name}: no sky/lamp layer (COLOR_1 / _SKY)`);
        if (bounce) g.setAttribute('aBounce', bounce);
        mesh.material = matName === 'M_House_Glass' ? litGlass : matName === 'M_House_Lantern' ? litLantern : lit;
        const names = namesUp(mesh);
        if (!names.some((n) => NON_CASTERS.test(n))) casterMeshes.push(mesh);
      }
    }
    if (oldMat && oldMat !== mesh.material) oldMat.dispose?.();
  }

  // ---------------------------------------------------------------- lantern (point light for the trees)
  lighting.setLampPosition(new Vector3().fromArray(manifest.lamps.pointLightPosition));

  // ---------------------------------------------------------------- shadow casters and region
  const footprint = groundFootprint(views, half);
  const forest = createForest(variants, manifest, trees, leafCards, createTreeDepthMaterial());
  let treeTop = 12;
  for (const it of forest.instances) treeTop = Math.max(treeTop, it.sphere.center.y + it.sphere.radius);
  const region = {
    min: new Vector3(footprint.min.x, manifest.grass.heightMin - 0.5, footprint.min.z),
    max: new Vector3(footprint.max.x, Math.min(treeTop, 30), footprint.max.z),
  };
  const shadows = createShadows(renderer, lighting, region);
  for (const m of casterMeshes) shadows.addMesh(m.geometry, m.matrixWorld);
  shadows.addMesh(terrainMesh.geometry, terrainMesh.matrixWorld);
  for (const c of forest.casters) shadows.addObject(c);
  scene.add(...forest.group);

  // ---------------------------------------------------------------- grass, sky, fireflies
  const grass = createGrass(manifest, grassMap, textures, lighting, colorUniforms, views);
  scene.add(grass.mesh);
  custom.push(grass.material);
  scene.add(createSkyDome(lighting));
  const house = new Vector3().fromArray(manifest.cameras.CAM_Overview.target);
  const fireflies = createFireflies(grassMap, manifest, lighting, house, 400);
  scene.add(fireflies.points);

  function setTier(tier) {
    for (const m of custom) {
      m.defines = m.defines || {};
      const taps = String(tier.shadowTaps);
      const wind = tier.wind;
      let changed = m.defines.SHADOW_TAPS !== taps;
      m.defines.SHADOW_TAPS = taps;
      if (('USE_WIND' in m.defines) !== wind && (m === trees || m === leafCards || m === grass.material)) {
        if (wind) m.defines.USE_WIND = '';
        else delete m.defines.USE_WIND;
        changed = true;
      }
      if (changed) m.needsUpdate = true;
    }
    if (leafCards) setCardAlphaMode(leafCards, tier.msaa);
    shadows.setSize(tier.shadow);
    shadows.setTaps(tier.shadowTaps);
    grass.setCount(tier.grass);
    forest.setLodScale(tier.lodScale, tier.treeBudget, tier.cardBudget);
    fireflies.setCount(tier.fireflies);
  }

  return { scene, shadows, forest, grass, fireflies, setTier };
}
