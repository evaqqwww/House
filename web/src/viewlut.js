// Blender's view transform as a 3D LUT: AgX with the look 'AgX - Base Contrast' on the sRGB display, baked from
// Blender's colour management config by scripts/make-agx-lut.py (src/agx-lut.png). The Cycles references are formed
// with exactly this transform; three.js' AgXToneMapping differs from it by up to ~10 display levels in the mid-tones.
// Lookup as in OpenColorIO: scene-linear Rec.709 -> linear E-Gamut -> log2 allocation -> tetrahedral interpolation
// of the LUT nodes (the nodes are Blender's own grid, so the result matches Blender to under a display level for
// scene colours). The LUT holds display-encoded sRGB; viewTransform() returns it decoded to linear, ready for the
// pass's sRGB output encoding.
import { Data3DTexture, NearestFilter, NoColorSpace, RGBAFormat, UnsignedByteType } from 'three';
import { decodePNG, toRGBA } from './png.js';

const LUT_URL = new URL('./agx-lut.png', import.meta.url);
export const LUT_LOG_RANGE = [-12.47393, 6.722499]; // log2 of the first and last node (make-agx-lut.py)

export async function loadViewLut() {
  const res = await fetch(LUT_URL);
  if (!res.ok) throw new Error(`agx-lut.png: HTTP ${res.status}`);
  const img = await decodePNG(await res.arrayBuffer());
  const n = img.width;
  if (img.height !== n * n) throw new Error(`agx-lut.png: expected ${n} x ${n * n} pixels, got ${img.width} x ${img.height}`);
  const tex = new Data3DTexture(toRGBA(img), n, n, n);
  tex.format = RGBAFormat;
  tex.type = UnsignedByteType;
  tex.colorSpace = NoColorSpace;
  tex.minFilter = NearestFilter;
  tex.magFilter = NearestFilter;
  tex.generateMipmaps = false;
  tex.unpackAlignment = 1;
  tex.name = 'AgX_Base_sRGB';
  tex.needsUpdate = true;
  return tex;
}

export const VIEW_GLSL = /* glsl */ `
uniform highp sampler3D viewLut;
uniform vec2 viewLutRange; // log2 of the first and last node
// scene-linear Rec.709 -> linear FilmLight E-Gamut (OpenColorIO, Blender's config)
const mat3 REC709_TO_EGAMUT = mat3(
  vec3( 0.5593711, 0.0762207, 0.0655267 ), vec3( 0.3047833, 0.7879718, 0.1645468 ), vec3( 0.1358456, 0.1358075, 0.7699265 ) );
vec3 viewNode( ivec3 i ) { return texelFetch( viewLut, i, 0 ).rgb; }
vec3 viewTransform( vec3 c ) {
  float n1 = float( textureSize( viewLut, 0 ).x - 1 );
  vec3 e = REC709_TO_EGAMUT * max( c, vec3( 0.0 ) );
  vec3 t = clamp( ( log2( max( e, vec3( 1e-10 ) ) ) - viewLutRange.x ) / ( viewLutRange.y - viewLutRange.x ), 0.0, 1.0 ) * n1;
  vec3 i0 = min( floor( t ), vec3( n1 - 1.0 ) );
  vec3 f = t - i0;
  ivec3 b = ivec3( i0 );
  vec3 c000 = viewNode( b );
  vec3 c111 = viewNode( b + ivec3( 1 ) );
  vec3 d;
  // tetrahedral interpolation (OpenColorIO)
  if ( f.r > f.g ) {
    if ( f.g > f.b ) d = ( 1.0 - f.r ) * c000 + ( f.r - f.g ) * viewNode( b + ivec3( 1, 0, 0 ) ) + ( f.g - f.b ) * viewNode( b + ivec3( 1, 1, 0 ) ) + f.b * c111;
    else if ( f.r > f.b ) d = ( 1.0 - f.r ) * c000 + ( f.r - f.b ) * viewNode( b + ivec3( 1, 0, 0 ) ) + ( f.b - f.g ) * viewNode( b + ivec3( 1, 0, 1 ) ) + f.g * c111;
    else d = ( 1.0 - f.b ) * c000 + ( f.b - f.r ) * viewNode( b + ivec3( 0, 0, 1 ) ) + ( f.r - f.g ) * viewNode( b + ivec3( 1, 0, 1 ) ) + f.g * c111;
  } else {
    if ( f.b > f.g ) d = ( 1.0 - f.b ) * c000 + ( f.b - f.g ) * viewNode( b + ivec3( 0, 0, 1 ) ) + ( f.g - f.r ) * viewNode( b + ivec3( 0, 1, 1 ) ) + f.r * c111;
    else if ( f.b > f.r ) d = ( 1.0 - f.g ) * c000 + ( f.g - f.b ) * viewNode( b + ivec3( 0, 1, 0 ) ) + ( f.b - f.r ) * viewNode( b + ivec3( 0, 1, 1 ) ) + f.r * c111;
    else d = ( 1.0 - f.g ) * c000 + ( f.g - f.r ) * viewNode( b + ivec3( 0, 1, 0 ) ) + ( f.r - f.b ) * viewNode( b + ivec3( 1, 1, 0 ) ) + f.b * c111;
  }
  // display sRGB -> linear (the pass encodes its output to sRGB)
  return mix( d / 12.92, pow( ( d + 0.055 ) / 1.055, vec3( 2.4 ) ), step( 0.04045, d ) );
}
`;
