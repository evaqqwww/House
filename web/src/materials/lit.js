// "lit": house, rocks, flowers, lily pads, reeds. Albedo from COLOR_0, baked light layers from the vertex
// attributes aSky = (sqrt(S / skyScale), sqrt(Lp / lampScale)) and aBounce = (sqrt(B / bounceScale), Z):
//   radiance = (albedo + ks) * (key * shadow + fill + skyRad(Z) * S + bounce * B + lamp * Lp) + emission
// ks is the rough specular of Blender's Principled BSDF (specular 0.5, roughness 0.8-0.9): the Cycles glossy passes
// of the house and rocks come to 1-3 % of the diffuse light, white. Direct light and the baked sum are per vertex
// (flat-shaded parts have per-face normals); the shadow lookup is per fragment. M_House_Glass and M_House_Lantern
// add the time-of-day lamp emission.
import { Color, DoubleSide, FrontSide, ShaderMaterial } from 'three';
import { LIGHT_PARS, SHADOW_PARS } from '../shaderlib.js';

const SPECULAR = 0.018; // ks: Cycles glossy / diffuse light of the house surfaces (rocks ~0.012)

const vertexShader = /* glsl */ `
${LIGHT_PARS}
${SHADOW_PARS}
attribute vec4 aSky;
attribute vec4 aBounce; // sqrt(B / bounceScale) rgb, zenith fraction Z (linear) a
varying vec3 vAlbedo;
varying vec3 vKey;
varying vec3 vFill;
varying vec3 vShadow;
void main() {
  vec4 wp = modelMatrix * vec4( position, 1.0 );
  vec3 n = normalize( mat3( modelMatrix ) * normal );
  vKey = keyRad() * max( dot( n, keyDir() ), 0.0 );
  vec3 S = decodeSky( aSky.rgb );
  vec3 B = decodeBounce( aBounce.rgb );
  float Lp = decodeLamp( aSky.a );
  vFill = fillRad() * max( dot( n, fillDir() ), 0.0 ) + bakedLight( S, B, Lp, aBounce.a );
  vAlbedo = color.rgb;
  vShadow = shadowCoord( wp.xyz, n );
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const fragmentShader = /* glsl */ `
${SHADOW_PARS}
uniform vec3 uEmission;
uniform float uSpecular;
varying vec3 vAlbedo;
varying vec3 vKey;
varying vec3 vFill;
varying vec3 vShadow;
void main() {
  float sh = dot( vKey, vKey ) > 0.0 ? shadowLookup( vShadow ) : 1.0;
  gl_FragColor = vec4( ( vAlbedo + uSpecular ) * ( vKey * sh + vFill ) + uEmission, 1.0 );
  #include <colorspace_fragment>
}`;

/**
 * @param {ReturnType<import('../lighting.js').createLighting>} lighting
 * @param {{value: Color}} [emission]  shared emission uniform (glass / lantern), black when omitted
 */
export function createLitMaterial(lighting, emission = null, name = 'Lit') {
  return new ShaderMaterial({
    name,
    vertexColors: true,
    // the emitters are tiny and seen through the lantern frame from any side
    side: emission ? DoubleSide : FrontSide,
    uniforms: { ...lighting.U, uEmission: emission || { value: new Color(0, 0, 0) }, uSpecular: { value: SPECULAR } },
    vertexShader,
    fragmentShader,
  });
}
