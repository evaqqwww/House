// Recompresses public/assets/scene.glb after the Blender export: renames the light-layer attributes the
// exporter writes (_SKY, _BOUNCE) to COLOR_1 / COLOR_2, drops the card attributes (_CARD, TEXCOORD_0) the exporter
// also writes on the other primitives of a tree LOD mesh (zeros there), quantizes normals (8 bit), every vertex colour
// (8 bit: COLOR_0 albedo, COLOR_1 / COLOR_2 sqrt-encoded light) and UVs (16 bit), and meshopt-compresses every stream.
// Positions stay float (the viewer merges the tree LOD primitives in object space: leaf heights, bounds, wind, card
// centres), and so does _CARD (corner, half size in metres, roll in radians).
//   node scripts/optimize-glb.mjs [glb]     (default: public/assets/scene.glb; rewritten in place)
import fs from 'node:fs';
import path from 'node:path';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression, KHRMeshQuantization } from '@gltf-transform/extensions';
import { quantize } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder } from 'meshoptimizer';

const file = process.argv[2] || 'public/assets/scene.glb';
const RENAME = { _SKY: 'COLOR_1', _BOUNCE: 'COLOR_2' };
const CARD_MATERIAL = 'M_LeafCards';
const CARD_ATTRIBUTES = ['_CARD', 'TEXCOORD_0'];

await MeshoptDecoder.ready;
await MeshoptEncoder.ready;
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.decoder': MeshoptDecoder, 'meshopt.encoder': MeshoptEncoder });

const before = fs.statSync(file).size;
const doc = await io.read(file);
let renamed = 0;
let dropped = 0;
for (const mesh of doc.getRoot().listMeshes()) {
  const cards = mesh.listPrimitives().some((prim) => prim.getMaterial()?.getName() === CARD_MATERIAL);
  for (const prim of mesh.listPrimitives()) {
    for (const [from, to] of Object.entries(RENAME)) {
      const attr = prim.getAttribute(from);
      if (!attr) continue;
      prim.setAttribute(to, attr);
      prim.setAttribute(from, null);
      renamed++;
    }
    if (cards && prim.getMaterial()?.getName() !== CARD_MATERIAL) {
      for (const name of CARD_ATTRIBUTES) {
        if (!prim.getAttribute(name)) continue;
        prim.setAttribute(name, null);
        dropped++;
      }
    }
  }
}
await doc.transform(
  quantize({
    pattern: /^(NORMAL|COLOR_\d+|TEXCOORD_0)$/,
    quantizeNormal: 8,
    quantizeColor: 8, // albedo and sqrt-encoded light: 8 bits are enough
    quantizeTexcoord: 16,
  }),
);
// 8-bit normals need KHR_mesh_quantization; quantize() decides by the POSITION accessor alone and leaves it out
// while positions stay float, so it is declared here whenever a normal or tangent is not float
const quantized = doc.getRoot().listMeshes().some((mesh) => mesh.listPrimitives().some((prim) =>
  ['NORMAL', 'TANGENT'].some((s) => prim.getAttribute(s) && prim.getAttribute(s).getComponentSize() < 4)));
if (quantized) doc.createExtension(KHRMeshQuantization).setRequired(true);
doc
  .createExtension(EXTMeshoptCompression)
  .setRequired(true)
  .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.QUANTIZE });
await io.write(file, doc);
const after = fs.statSync(file).size;
console.log(`${file} ${(before / 1e6).toFixed(2)} MB -> ${(after / 1e6).toFixed(2)} MB, ` +
  `${renamed} light-layer attributes renamed, ${dropped} unused card attributes dropped`);

// keep manifest.files in step when the GLB sits next to a manifest
const manifestPath = path.join(path.dirname(file), 'manifest.json');
if (fs.existsSync(manifestPath)) {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (manifest.files && path.basename(file) in manifest.files) {
    manifest.files[path.basename(file)] = after;
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 1));
  }
}
