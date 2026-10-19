// "smoke": the chimney wisp, Blender's M_House_Smoke (albedo 0.92, emission 0.25): lit by the current sun/moon,
// sky and ground light, alpha fading out with height, a slight sway that grows with height.
import { DoubleSide, ShaderMaterial } from 'three';
import { LIGHT_PARS } from '../shaderlib.js';

const SMOKE = { albedo: 0.92, emission: 0.25, direct: 0.85, opacity: 0.9, sway: 0.18 };

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
      uSway: { value: SMOKE.sway },
    },
    vertexShader: /* glsl */ `
      ${LIGHT_PARS}
      uniform float uTime;
      uniform vec2 uYRange;
      uniform vec4 uSmoke;
      uniform float uSway;
      varying float vH;
      varying vec3 vRad;
      void main() {
        float h = clamp( ( position.y - uYRange.x ) / ( uYRange.y - uYRange.x ), 0.0, 1.0 );
        vH = h;
        vec3 p = position;
        float k = uSway * h * h;
        p.x += k * sin( uTime * 0.9 + h * 4.0 );
        p.z += k * 0.6 * cos( uTime * 0.7 + h * 3.1 );
        vec3 L = ( keyRad() + fillRad() ) * uSmoke.z + uSkyUp * 0.8 + uGroundRad * 0.5 + uLampRad * 0.05;
        vRad = uSmoke.x * L + uSmoke.y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4( p, 1.0 );
      }`,
    fragmentShader: /* glsl */ `
      uniform vec4 uSmoke;
      varying float vH;
      varying vec3 vRad;
      void main() {
        float a = uSmoke.w * ( 1.0 - smoothstep( 0.15, 1.0, vH ) ) * smoothstep( 0.0, 0.04, vH + 0.02 );
        gl_FragColor = vec4( vRad, a );
        #include <colorspace_fragment>
      }`,
  });
}
