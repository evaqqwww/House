// Loads the asset set (manifest.json version 2) with byte progress: scene.glb (meshopt), the terrain light layers
// (terrain_sky / terrain_bounce / terrain_lamp / terrain_zenith, data), ground_albedo (sRGB) and grass.png (density +
// height, read on the CPU and uploaded as a density texture). ground_cover.png keeps surface coverage separate
// from canopy-thinned blade density. PNGs are decoded by src/png.js, so data stays bit exact.
import {
  DataTexture,
  LinearFilter,
  LinearMipmapLinearFilter,
  NoColorSpace,
  RedFormat,
  RGBAFormat,
  SRGBColorSpace,
  UnsignedByteType,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { decodePNG, toRed, toRGBA } from './png.js';

const FILES = ['scene.glb', 'terrain_sky.png', 'terrain_bounce.png', 'terrain_lamp.png', 'terrain_zenith.png', 'ground_albedo.png', 'grass.png'];

async function fetchWithProgress(url, onBytes) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  if (!res.body) {
    const buf = await res.arrayBuffer();
    onBytes(buf.byteLength);
    return buf;
  }
  const reader = res.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.byteLength;
    onBytes(value.byteLength);
  }
  const out = new Uint8Array(size);
  let off = 0;
  for (const c of chunks) {
    out.set(c, off);
    off += c.byteLength;
  }
  return out.buffer;
}

function dataTexture(img, name, { srgb = false, red = false, anisotropy = 1 } = {}) {
  const data = red ? toRed(img) : toRGBA(img);
  const tex = new DataTexture(data, img.width, img.height, red ? RedFormat : RGBAFormat, UnsignedByteType);
  tex.name = name;
  tex.flipY = false; // row 0 of the PNG is v = 0 (glTF convention), i.e. three.js z = -half
  tex.colorSpace = srgb ? SRGBColorSpace : NoColorSpace;
  tex.generateMipmaps = true;
  tex.minFilter = LinearMipmapLinearFilter;
  tex.magFilter = LinearFilter;
  tex.anisotropy = anisotropy;
  tex.unpackAlignment = 1;
  tex.needsUpdate = true;
  return tex;
}

function parseGLB(buffer, path) {
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  return new Promise((resolve, reject) => {
    loader.parse(buffer, path, resolve, (err) => reject(err instanceof Error ? err : new Error(String(err?.message || err))));
  });
}

/**
 * @param {string} base  URL prefix of the asset folder, ending with '/'
 * @param {(fraction:number, text:string) => void} onProgress
 * @param {number} maxAnisotropy
 */
export async function loadAssets(base, onProgress, maxAnisotropy = 4) {
  onProgress(0, '读取清单');
  const mres = await fetch(base + 'manifest.json', { cache: 'no-cache' });
  if (!mres.ok) throw new Error(`manifest.json: HTTP ${mres.status}`);
  const manifest = await mres.json();
  if (manifest.version !== 2) throw new Error(`manifest.json version ${manifest.version}: this viewer needs asset contract version 2`);
  // The surface map is additive to v2; older exports retain their original density-based response.
  const files = manifest.files['ground_cover.png'] ? [...FILES, 'ground_cover.png'] : FILES;

  const expected = {};
  for (const f of files) expected[f] = Number(manifest.files[f]) || 1;
  const total = Object.values(expected).reduce((a, b) => a + b, 0);
  const got = Object.fromEntries(files.map((f) => [f, 0]));
  const report = () => {
    const sum = files.reduce((a, f) => a + Math.min(got[f], expected[f]), 0);
    onProgress(0.85 * (sum / total), `下载 ${(sum / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);
  };
  const bump = (file) => (n) => {
    got[file] += n;
    report();
  };
  const buffers = await Promise.all(files.map((f) => fetchWithProgress(base + f, bump(f))));
  const byName = Object.fromEntries(files.map((f, i) => [f, buffers[i]]));

  onProgress(0.88, '解析场景');
  await MeshoptDecoder.ready;
  const [gltf, skyImg, bounceImg, lampImg, zenithImg, albedoImg, grassImg] = await Promise.all([
    parseGLB(byName['scene.glb'], base),
    decodePNG(byName['terrain_sky.png']),
    decodePNG(byName['terrain_bounce.png']),
    decodePNG(byName['terrain_lamp.png']),
    decodePNG(byName['terrain_zenith.png']),
    decodePNG(byName['ground_albedo.png']),
    decodePNG(byName['grass.png']),
  ]);
  if (grassImg.channels < 3) throw new Error('grass.png must be RGB (R density, G/B height)');
  const coverImg = byName['ground_cover.png'] ? await decodePNG(byName['ground_cover.png']) : grassImg;

  onProgress(0.94, '准备贴图');
  const aniso = Math.min(maxAnisotropy, 4);
  const textures = {
    sky: dataTexture(skyImg, 'terrain_sky', { anisotropy: aniso }),
    bounce: dataTexture(bounceImg, 'terrain_bounce'),
    lamp: dataTexture(lampImg, 'terrain_lamp', { red: true }),
    albedo: dataTexture(albedoImg, 'ground_albedo', { srgb: true, anisotropy: aniso }),
    density: dataTexture(grassImg, 'grass_density', { red: true, anisotropy: aniso }),
    cover: dataTexture(coverImg, 'ground_cover', { red: true, anisotropy: aniso }),
    zenith: dataTexture(zenithImg, 'terrain_zenith', { red: true }),
  };
  // CPU copies for the grass generator: density (R) and the 16-bit height (G * 256 + B)
  const n = grassImg.width * grassImg.height;
  const density = new Uint8Array(n);
  const height = new Uint16Array(n);
  for (let i = 0, j = 0; i < n; i++, j += grassImg.channels) {
    density[i] = grassImg.data[j];
    height[i] = grassImg.data[j + 1] * 256 + grassImg.data[j + 2];
  }
  const grassMap = { width: grassImg.width, height: grassImg.height, density, heightCode: height };
  return { manifest, gltf, textures, grassMap };
}
