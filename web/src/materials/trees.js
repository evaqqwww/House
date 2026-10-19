// Trees and bushes (treeLOD variants, InstancedMesh per variant x LOD; bark and leaves merged into one geometry
// with aLeaf = (leaf flag, height in the canopy)). Lit entirely per vertex: real-time Lambert from the canopy
// (sphere-transferred) normals with the shadow map, a hemisphere of zenith-coloured sky / ground light, Blender's
// M_Leaves translucency (0.32 of the light passes through, tinted warm) with a forward-scattering glow when looking
// toward the light (strong at golden hour), the lower canopy shaded by the leaves above it, and the lantern's point
// light at night. Wind sway is optional (USE_WIND).
// The L2 proxies that cast the tree shadows only roughly match the canopy, so tree receivers look up the shadow map
// a little toward the light (uShadowReach metres): a canopy is shaded by its neighbours, not by its own proxy.
import { Color, DoubleSide, ShaderMaterial, Vector2, Vector4 } from 'three';
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
};

const vertexShader = /* glsl */ `
#define SHADOW_VERTEX
${LIGHT_PARS}
${SHADOW_PARS}
attribute vec2 aLeaf; // leaf flag, height in the canopy (0 lowest leaf, 1 top)
uniform float uTime;
uniform vec4 uLeaf;     // translucency, glow, glow power, scatter
uniform vec3 uWarm;
uniform vec4 uTreeLight; // direct scale, leaf AO, bark AO, wind
uniform float uWrap;
uniform float uShadowReach; // metres toward the light for the shadow lookup
uniform vec2 uTreeSky; // zenith fraction of the sky light, lantern gain
uniform float uCanopyBottom; // light reaching the lowest leaves, relative to the top of the canopy
varying vec3 vColor;

void main() {
  vec3 pos = position;
  mat4 inst = instanceMatrix;
  vec3 base = inst[ 3 ].xyz;
  #ifdef USE_WIND
    float wPhase = dot( base.xz, vec2( 0.13, 0.19 ) );
    float wAmt = uTreeLight.w * smoothstep( 1.5, 7.0, pos.y );
    pos.x += wAmt * sin( uTime * 1.1 + wPhase + pos.y * 0.35 );
    pos.z += wAmt * 0.7 * cos( uTime * 0.87 + wPhase * 1.3 + pos.x * 0.4 );
  #endif
  mat4 world = modelMatrix * inst;
  vec4 wp = world * vec4( pos, 1.0 );
  vec3 n = normalize( mat3( world ) * normal );
  vec3 V = normalize( cameraPosition - wp.xyz );
  float leaf = aLeaf.x;
  // the lower canopy is shaded by the leaves above it (sun and sky alike)
  float hk = mix( 1.0, mix( uCanopyBottom, 1.0, smoothstep( 0.0, 1.0, aLeaf.y ) ), leaf );

  vec3 albedo = color.rgb;
  albedo = mix( albedo, albedo / max( vec3( 1.0 ) - uLeaf.w * albedo, vec3( 0.05 ) ), leaf );
  float trans = uLeaf.x * leaf;

  vec3 kd = keyDir();
  float sh = shadowLookup( shadowCoord( wp.xyz + kd * uShadowReach, n ) );

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

  vColor = albedo * ( direct + indirect + uTreeSky.y * lampPointLight( wp.xyz, n ) );
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const fragmentShader = /* glsl */ `
varying vec3 vColor;
void main() {
  gl_FragColor = vec4( vColor, 1.0 );
  #include <colorspace_fragment>
}`;

export function createTreeMaterial(lighting) {
  return new ShaderMaterial({
    name: 'Trees',
    vertexColors: true,
    side: DoubleSide,
    defines: { USE_WIND: '' },
    uniforms: {
      ...lighting.U,
      uLeaf: { value: new Vector4(DEFAULTS.translucency, DEFAULTS.glow, DEFAULTS.glowPower, DEFAULTS.scatter) },
      uWarm: { value: new Color().fromArray(DEFAULTS.warm) },
      uTreeLight: { value: new Vector4(DEFAULTS.directScale, DEFAULTS.ambientOcclusion, DEFAULTS.barkOcclusion, DEFAULTS.wind) },
      uShadowReach: { value: DEFAULTS.shadowReach },
      uWrap: { value: DEFAULTS.wrap },
      uTreeSky: { value: new Vector2(DEFAULTS.skyZenith, DEFAULTS.lamp) },
      uCanopyBottom: { value: DEFAULTS.canopyBottom },
    },
    vertexShader,
    fragmentShader,
  });
}
