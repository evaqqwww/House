// Trees and bushes (treeLOD variants, instanced per variant x LOD by forest.js). Two materials share one set of
// light uniforms and one per-vertex light model:
//   Trees      the opaque parts: bark and the dark inner leaf core (merged, aLeaf = (leaf flag, height in the
//              canopy)); the core keeps the sky out of the gaps between the leaf cards
//   TreeCards  the leaf-cluster cards: camera-facing quads expanded in the vertex shader around the card centre
//              (POSITION), textured from the leaf atlas (manifest.trees.leafAtlas)
// Light model (per vertex): real-time Lambert from the canopy (sphere-transferred) normals with the shadow map, a
// hemisphere of zenith-coloured sky / ground light, Blender's M_Leaves translucency (0.32 of the light passes
// through, tinted warm) with a forward-scattering glow when looking toward the light (strong at golden hour), the
// lower canopy shaded by the leaves above it, and the lantern's point light at night. Wind sway is optional
// (USE_WIND) and moves a card as a whole (it acts on the card centre).
// The shadow casters (<Variant>_Shadow hulls) only roughly match the canopy, so tree receivers look up the shadow
// map a little toward the light (uShadowReach metres): a canopy is shaded by its neighbours, not by its own hull.
//
// Cards: the atlas "up" (from the cluster's base at the bottom of its cell toward the leaf tips) is rolled toward
// the screen-space direction of the canopy normal at the card centre, so clusters fan outward along the silhouette
// and point up on the face of the canopy (uCard.x), plus the card's own roll jitter (_card.w). Each corner is lit
// with the canopy normal bent toward that corner (uCard.y), so a card reads as a small dome; the cluster's base is a
// little darker than its tips (uCard.z). Cards whose canopy normal faces away from the camera lie behind the core:
// they shrink to nothing between two cosines (uCardFade), so a card turning away never pops on the silhouette, and
// are dropped in the vertex shader beyond. The atlas grey divided by its mean multiplies the
// vertex-colour albedo, so the mean albedo is the vertex colour's. Alpha: with MSAA, alpha to coverage on an alpha
// sharpened to about one pixel of edge (Golus, "Anti-aliased Alpha Test"); without, an alpha test at the cutoff. Both
// scale the alpha up with the mip level (uLeafAtlas.z per level), so distant canopies keep their coverage instead of
// thinning out.
import { Color, DoubleSide, ShaderMaterial, Vector2, Vector3, Vector4 } from 'three';
import { LIGHT_PARS, SHADOW_PARS } from '../shaderlib.js';

// Calibrated against the Cycles passes of the preset shots (diffuse light of the canopies, per hour): the canopy
// takes the sun like a Lambert surface with its canopy normals and a lot of zenith-coloured sky light.
const DEFAULTS = {
  translucency: 0.32,
  warm: [1.5, 1.45, 0.8],
  glow: 1.0,
  glowPower: 4.0,
  wind: 0.035,
  ambientOcclusion: 0.65, // sky light reaching the visible leaves
  barkOcclusion: 0.35,
  scatter: 0.3, // light scattered between leaves: albedo / (1 - scatter * albedo)
  directScale: 0.95,
  shadowReach: 0.9,
  wrap: 0.1, // leaf cards face every way: part of the direct light reaches the canopy like a wrapped diffuse
  skyZenith: 0.9, // zenith fraction of the sky light the canopy sees
  lamp: 0.12, // lantern point light on the canopies
  canopyBottom: 0.4, // light reaching the lowest leaves relative to the top of the canopy
  // cards
  cardUp: 0.3, // upward lean of the cluster direction (a face-on card points up, a silhouette card outward)
  cardBulge: 0.45, // normal bend toward a corner
  cardBase: 0.85, // light at the cluster's base relative to its tips
  cardFade: [-0.4, -0.1], // cards shrink from full size (cosine of canopy normal and view -0.1) to nothing (-0.4)
  coverageBoost: 0.25, // alpha gain per mip level
};

const TREE_LIGHT = /* glsl */ `
uniform float uTime;
uniform vec4 uLeaf;     // translucency, glow, glow power, scatter
uniform vec3 uWarm;
uniform vec4 uTreeLight; // direct scale, leaf AO, bark AO, wind
uniform float uWrap;
uniform float uShadowReach; // metres toward the light for the shadow lookup
uniform vec2 uTreeSky; // zenith fraction of the sky light, lantern gain
uniform float uCanopyBottom; // light reaching the lowest leaves, relative to the top of the canopy

// wind sway of an object-space point of the instance whose base is at world position base
vec3 treeWind( vec3 pos, vec3 base ) {
  #ifdef USE_WIND
    float wPhase = dot( base.xz, vec2( 0.13, 0.19 ) );
    float wAmt = uTreeLight.w * smoothstep( 1.5, 7.0, pos.y );
    pos.x += wAmt * sin( uTime * 1.1 + wPhase + pos.y * 0.35 );
    pos.z += wAmt * 0.7 * cos( uTime * 0.87 + wPhase * 1.3 + pos.x * 0.4 );
  #endif
  return pos;
}

// radiance of a tree surface point: world position wp, world normal n, albedo, leaf flag (0 bark, 1 leaves),
// height in the canopy h (0 lowest leaf, 1 top)
vec3 treeLight( vec3 wp, vec3 n, vec3 albedo, float leaf, float h ) {
  vec3 V = normalize( cameraPosition - wp );
  // the lower canopy is shaded by the leaves above it (sun and sky alike)
  float hk = mix( 1.0, mix( uCanopyBottom, 1.0, smoothstep( 0.0, 1.0, h ) ), leaf );

  albedo = mix( albedo, albedo / max( vec3( 1.0 ) - uLeaf.w * albedo, vec3( 0.05 ) ), leaf );
  float trans = uLeaf.x * leaf;

  vec3 kd = keyDir();
  float sh = shadowLookup( shadowCoord( wp + kd * uShadowReach, n ) );

  // direct: key (shadowed) + fill
  float ndl = dot( n, kd );
  float toward = pow( max( dot( -V, kd ), 0.0 ), uLeaf.z );
  float rim = 1.0 - abs( dot( n, V ) );
  float back = max( -ndl, 0.0 ) + uLeaf.y * toward * rim;
  float wrap = uWrap * leaf;
  float diffuse = mix( max( ndl, 0.0 ), 0.3 * ( 1.0 + ndl ), wrap );
  vec3 direct = ( ( 1.0 - trans ) * diffuse + trans * back * uWarm ) * keyRad() * sh;
  vec3 fd = fillDir();
  float ndf = dot( n, fd );
  direct += ( ( 1.0 - trans ) * max( ndf, 0.0 ) + trans * max( -ndf, 0.0 ) * uWarm ) * fillRad();
  direct *= uTreeLight.x * hk;

  // hemisphere: open-sky light from above, meadow light from below; through the leaf from the other side
  vec3 sky = skyLight( uTreeSky.x );
  vec3 irr = mix( uGroundRad, sky, 0.5 + 0.5 * n.y );
  vec3 behind = 0.25 * max( uGroundRad + sky - irr, vec3( 0.0 ) );
  float ao = mix( uTreeLight.z, uTreeLight.y, leaf );
  vec3 indirect = ao * hk * ( ( 1.0 - trans ) * irr + trans * uWarm * behind );

  return albedo * ( direct + indirect + uTreeSky.y * lampPointLight( wp, n ) );
}
`;

const solidVertex = /* glsl */ `
#define SHADOW_VERTEX
${LIGHT_PARS}
${SHADOW_PARS}
${TREE_LIGHT}
attribute vec2 aLeaf; // leaf flag, height in the canopy (0 lowest leaf, 1 top)
varying vec3 vColor;

void main() {
  mat4 world = modelMatrix * instanceMatrix;
  vec4 wp = world * vec4( treeWind( position, instanceMatrix[ 3 ].xyz ), 1.0 );
  vec3 n = normalize( mat3( world ) * normal );
  vColor = treeLight( wp.xyz, n, color.rgb, aLeaf.x, aLeaf.y );
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const solidFragment = /* glsl */ `
varying vec3 vColor;
void main() {
  gl_FragColor = vec4( vColor, 1.0 );
  #include <colorspace_fragment>
}`;

const cardVertex = /* glsl */ `
#define SHADOW_VERTEX
${LIGHT_PARS}
${SHADOW_PARS}
${TREE_LIGHT}
attribute vec2 aLeaf; // leaf flag (1), height of the card centre in the canopy
attribute vec4 _card; // corner x, corner y (+1 toward the leaf tips), half size (m), roll jitter (rad)
uniform vec3 uCard;   // upward lean, normal bend toward the corner, light at the cluster base
uniform vec2 uCardFade; // cosines of canopy normal and view where a card has shrunk to nothing / is at full size
varying vec3 vColor;
varying vec2 vUv;

void main() {
  mat4 world = modelMatrix * instanceMatrix;
  vec4 wc = world * vec4( treeWind( position, instanceMatrix[ 3 ].xyz ), 1.0 );
  vec3 n = normalize( mat3( world ) * normal );
  // cards behind the canopy centre are hidden by the core: they shrink away, and beyond that there is no quad at all
  float keep = smoothstep( uCardFade.x, uCardFade.y, dot( n, normalize( cameraPosition - wc.xyz ) ) );
  if ( keep <= 0.0 ) {
    gl_Position = vec4( 0.0, 0.0, 2.0, 1.0 );
    return;
  }
  vec3 vc = ( viewMatrix * wc ).xyz;
  vec3 nv = mat3( viewMatrix ) * n;
  // screen-space direction of the canopy normal at the card centre (derivative of the perspective projection
  // along n, scaled by the depth), leaning up; rolled by the card's jitter
  vec2 up = nv.xy - vc.xy * ( nv.z / min( vc.z, -1e-3 ) );
  up.y += uCard.x;
  up = dot( up, up ) > 1e-8 ? normalize( up ) : vec2( 0.0, 1.0 );
  float cr = cos( _card.w );
  float sr = sin( _card.w );
  up = vec2( cr * up.x - sr * up.y, sr * up.x + cr * up.y );
  vec2 right = vec2( up.y, -up.x );
  float hs = _card.z * length( world[ 0 ].xyz ) * keep; // half size (m)
  vec2 off = ( _card.x * right + _card.y * up ) * hs;
  vc.xy += off;
  gl_Position = projectionMatrix * vec4( vc, 1.0 );

  // light the corner: world position of the corner, canopy normal bent toward it
  vec3 offW = vec3( viewMatrix[ 0 ][ 0 ], viewMatrix[ 1 ][ 0 ], viewMatrix[ 2 ][ 0 ] ) * off.x
            + vec3( viewMatrix[ 0 ][ 1 ], viewMatrix[ 1 ][ 1 ], viewMatrix[ 2 ][ 1 ] ) * off.y;
  vec3 nb = normalize( n + uCard.y * offW / max( hs, 1e-4 ) );
  float base = mix( uCard.z, 1.0, 0.5 + 0.5 * _card.y );
  vColor = base * treeLight( wc.xyz + offW, nb, color.rgb, 1.0, aLeaf.y );
  vUv = uv;
}`;

const cardFragment = /* glsl */ `
uniform sampler2D uLeafMap;
uniform vec4 uLeafAtlas; // 1 / mean grey, alpha cutoff, alpha gain per mip level, atlas size (texels)
varying vec3 vColor;
varying vec2 vUv;
void main() {
  vec4 t = texture2D( uLeafMap, vUv );
  vec2 tx = vUv * uLeafAtlas.w;
  vec2 dx = dFdx( tx );
  vec2 dy = dFdy( tx );
  float mip = max( 0.5 * log2( max( dot( dx, dx ), dot( dy, dy ) ) ), 0.0 );
  float a = t.a * ( 1.0 + mip * uLeafAtlas.z );
  #ifdef LEAF_A2C
    a = ( a - uLeafAtlas.y ) / max( fwidth( a ), 1e-4 ) + 0.5;
  #else
    if ( a < uLeafAtlas.y ) discard;
    a = 1.0;
  #endif
  gl_FragColor = vec4( vColor * ( t.r * uLeafAtlas.x ), clamp( a, 0.0, 1.0 ) );
  #include <colorspace_fragment>
}`;

/**
 * @param {ReturnType<import('../lighting.js').createLighting>} lighting
 * @param {import('three').Texture|null} leafMap  the leaf atlas (sRGB, mipmapped)
 * @param {{meanGrey:number, alphaCutoff:number}|undefined} atlas  manifest.trees.leafAtlas
 * @returns {{ solid: ShaderMaterial, cards: ShaderMaterial|null }}
 */
export function createTreeMaterials(lighting, leafMap, atlas) {
  const shared = {
    ...lighting.U,
    uLeaf: { value: new Vector4(DEFAULTS.translucency, DEFAULTS.glow, DEFAULTS.glowPower, DEFAULTS.scatter) },
    uWarm: { value: new Color().fromArray(DEFAULTS.warm) },
    uTreeLight: { value: new Vector4(DEFAULTS.directScale, DEFAULTS.ambientOcclusion, DEFAULTS.barkOcclusion, DEFAULTS.wind) },
    uShadowReach: { value: DEFAULTS.shadowReach },
    uWrap: { value: DEFAULTS.wrap },
    uTreeSky: { value: new Vector2(DEFAULTS.skyZenith, DEFAULTS.lamp) },
    uCanopyBottom: { value: DEFAULTS.canopyBottom },
  };
  const solid = new ShaderMaterial({
    name: 'Trees',
    vertexColors: true,
    side: DoubleSide,
    defines: { USE_WIND: '' },
    uniforms: shared,
    vertexShader: solidVertex,
    fragmentShader: solidFragment,
  });
  if (!leafMap || !atlas) return { solid, cards: null };
  const size = leafMap.image?.width || 1024;
  const cards = new ShaderMaterial({
    name: 'TreeCards',
    vertexColors: true,
    side: DoubleSide,
    defines: { USE_WIND: '', LEAF_A2C: '' },
    alphaToCoverage: true,
    uniforms: {
      ...shared,
      uCard: { value: new Vector3(DEFAULTS.cardUp, DEFAULTS.cardBulge, DEFAULTS.cardBase) },
      uCardFade: { value: new Vector2(...DEFAULTS.cardFade) },
      uLeafMap: { value: leafMap },
      uLeafAtlas: { value: new Vector4(1 / Math.max(atlas.meanGrey, 1e-3), atlas.alphaCutoff ?? 0.5, DEFAULTS.coverageBoost, size) },
    },
    vertexShader: cardVertex,
    fragmentShader: cardFragment,
  });
  return { solid, cards };
}

/** Alpha to coverage with MSAA, alpha test without (the tier's sample count). */
export function setCardAlphaMode(cards, msaa) {
  const a2c = msaa > 0;
  if (cards.alphaToCoverage === a2c) return;
  cards.alphaToCoverage = a2c;
  if (a2c) cards.defines.LEAF_A2C = '';
  else delete cards.defines.LEAF_A2C;
  cards.needsUpdate = true;
}
