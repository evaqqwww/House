// "smoke": the chimney plume, Blender's M_House_Smoke (albedo 0.92, emission 0.25): lit by the current sun/moon,
// sky and ground light. The smoke flows up the tube at `rise` m/s: the bends, the puffs and the breakup of the
// density are functions of the height minus rise * time, so they drift upward with it. The bends grow with height,
// gusts lean the plume downwind (+x, the way the modelled plume drifts), the density thins out into wisps toward
// the top, and the edges of the tube fade so it reads as a soft volume.
import { DoubleSide, ShaderMaterial } from 'three';
import { LIGHT_PARS, NOISE } from '../shaderlib.js';

const SMOKE = {
  albedo: 0.92,
  emission: 0.25,
  direct: 0.85,
  opacity: 0.85,
  rise: 0.7, // m/s
  sway: 0.45, // m, sideways bends at the top
  lean: 0.55, // m, downwind lean at the top in a gust
  puff: 0.09, // m, swelling of the puffs
};

export function createSmokeMaterial(geometry, lighting) {
  geometry.computeBoundingBox();
  const bb = geometry.boundingBox;
  return new ShaderMaterial({
    name: 'Smoke',
    transparent: true,
    depthWrite: false,
    side: DoubleSide,
    uniforms: {
      ...lighting.U,
      uYRange: { value: [bb.min.y, Math.max(bb.max.y, bb.min.y + 1e-3)] },
      uSmoke: { value: [SMOKE.albedo, SMOKE.emission, SMOKE.direct, SMOKE.opacity] },
      uFlow: { value: [SMOKE.rise, SMOKE.sway, SMOKE.lean, SMOKE.puff] },
    },
    vertexShader: /* glsl */ `
      ${LIGHT_PARS}
      uniform float uTime;
      uniform vec2 uYRange;
      uniform vec4 uSmoke;
      uniform vec4 uFlow; // rise speed, sway, lean, puff
      varying float vH;
      varying vec2 vFlow;
      varying vec3 vRad;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        float h = clamp( ( position.y - uYRange.x ) / ( uYRange.y - uYRange.x ), 0.0, 1.0 );
        float t = uTime;
        float s = position.y - uFlow.x * t; // height in the rising smoke
        vec3 p = position;
        float a = uFlow.y * pow( h, 1.3 );
        p.x += a * ( 0.6 * sin( 2.6 * s + 0.3 * t ) + 0.4 * sin( 5.3 * s - 0.4 * t + 1.7 ) );
        p.z += a * ( 0.45 * cos( 2.1 * s + 0.2 * t + 0.8 ) + 0.3 * sin( 4.4 * s + 0.5 * t ) );
        float gust = 0.5 + 0.5 * sin( 0.31 * t ) * sin( 0.17 * t + 1.3 );
        p.x += uFlow.z * h * h * gust;
        float bump = 0.5 + 0.5 * sin( 4.6 * s + 1.1 * sin( 1.7 * s ) );
        p += normal * uFlow.w * smoothstep( 0.0, 0.2, h ) * ( 0.4 + h ) * bump * bump;
        vH = h;
        vFlow = vec2( dot( position.xz, vec2( 4.0, 3.0 ) ), s );
        vec3 L = ( keyRad() + fillRad() ) * uSmoke.z + uSkyUp * 0.8 + uGroundRad * 0.5 + uLampRad * 0.05;
        vRad = uSmoke.x * L + uSmoke.y;
        vec4 mv = modelViewMatrix * vec4( p, 1.0 );
        vN = normalMatrix * normal;
        vV = -mv.xyz;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      ${NOISE}
      uniform vec4 uSmoke;
      varying float vH;
      varying vec2 vFlow;
      varying vec3 vRad;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        float n = 0.65 * vnoise( vec2( 0.9 * vFlow.x, 2.2 * vFlow.y ) )
                + 0.35 * vnoise( vec2( 2.1 * vFlow.x, 5.0 * vFlow.y ) + 7.3 );
        float thr = mix( -0.2, 0.62, vH ); // solid at the flue, wisps at the top
        float density = smoothstep( thr - 0.12, thr + 0.18, n );
        float fade = ( 1.0 - smoothstep( 0.55, 1.0, vH ) ) * smoothstep( 0.0, 0.04, vH + 0.02 );
        float edge = smoothstep( 0.0, 0.6, abs( dot( normalize( vN ), normalize( vV ) ) ) );
        gl_FragColor = vec4( vRad, uSmoke.w * density * fade * mix( 0.3, 1.0, edge ) );
        #include <colorspace_fragment>
      }`,
  });
}
