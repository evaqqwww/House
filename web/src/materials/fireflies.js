// Fireflies: a few hundred additive points drifting over the meadow around the house, blinking, scaled by the
// time-of-day fireflies(t) value (hidden while it is zero). Bright enough in the HDR buffer to catch the bloom.
import { AdditiveBlending, BufferGeometry, InterleavedBuffer, InterleavedBufferAttribute, Points, ShaderMaterial, Sphere, Vector3 } from 'three';
import { mulberry32 } from './grass.js';

const RADIUS = 26; // metres around the house
const RADIANCE = 9; // HDR brightness of a firefly's core

/** Meadow spots (grass density > 0.35) within `radius` of `center`, at 0.3 - 1.8 m above the ground. */
function positions(grassMap, manifest, count, center, radius, seed = 11) {
  const { width: W, height: H, density, heightCode } = grassMap;
  const half = manifest.terrainUV.half;
  const { heightMin: hMin, heightMax: hMax } = manifest.grass;
  const rng = mulberry32(seed);
  const out = new Float32Array(count * 4);
  let n = 0;
  for (let tries = 0; n < count && tries < count * 200; tries++) {
    const a = rng() * Math.PI * 2;
    const r = radius * Math.sqrt(rng());
    const x = center.x + Math.cos(a) * r;
    const z = center.z + Math.sin(a) * r;
    const tx = Math.floor(((x + half) / (2 * half)) * W);
    const ty = Math.floor(((z + half) / (2 * half)) * H);
    if (tx < 0 || ty < 0 || tx >= W || ty >= H) continue;
    const i = ty * W + tx;
    if (density[i] < 90) continue;
    const y = hMin + (heightCode[i] / 65535) * (hMax - hMin);
    out[n * 4] = x;
    out[n * 4 + 1] = y + 0.3 + 1.5 * rng() * rng();
    out[n * 4 + 2] = z;
    out[n * 4 + 3] = rng();
    n++;
  }
  return { data: out, count: n };
}

export function createFireflies(grassMap, manifest, lighting, center, maxCount = 400) {
  const { data, count } = positions(grassMap, manifest, maxCount, center, RADIUS);
  const geometry = new BufferGeometry();
  const inter = new InterleavedBuffer(data, 4);
  geometry.setAttribute('position', new InterleavedBufferAttribute(inter, 3, 0));
  geometry.setAttribute('aPhase', new InterleavedBufferAttribute(inter, 1, 3));
  geometry.setDrawRange(0, count);
  geometry.boundingSphere = new Sphere(new Vector3(center.x, center.y + 1, center.z), 40);
  const material = new ShaderMaterial({
    name: 'Fireflies',
    transparent: true,
    depthWrite: false,
    blending: AdditiveBlending,
    uniforms: {
      uTime: lighting.U.uTime,
      uFireflies: lighting.U.uFireflies,
      uPixelsPerMeter: { value: 1000 },
      uMaxSize: { value: 6 },
      uColor: { value: new Vector3(1.0, 0.8, 0.32).multiplyScalar(RADIANCE) },
    },
    vertexShader: /* glsl */ `
      attribute float aPhase;
      uniform float uTime;
      uniform float uFireflies;
      uniform float uPixelsPerMeter;
      uniform float uMaxSize;
      varying float vGlow;
      void main() {
        float ph = aPhase * 6.2831853;
        vec3 p = position.xyz + vec3( sin( uTime * 0.31 + ph ) * 0.9 + sin( uTime * 0.83 + ph * 3.0 ) * 0.25,
                                      sin( uTime * 0.47 + ph * 2.0 ) * 0.3,
                                      cos( uTime * 0.27 + ph * 1.7 ) * 0.9 + cos( uTime * 0.71 + ph * 5.0 ) * 0.25 );
        float blink = smoothstep( 0.15, 0.9, sin( uTime * ( 0.9 + aPhase ) + ph * 7.0 ) * 0.5 + 0.5 );
        vGlow = blink * uFireflies;
        vec4 mv = modelViewMatrix * vec4( p, 1.0 );
        gl_PointSize = clamp( 0.07 * uPixelsPerMeter / max( -mv.z, 0.1 ), 1.5, uMaxSize ) * step( 0.01, vGlow );
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      varying float vGlow;
      void main() {
        vec2 q = gl_PointCoord * 2.0 - 1.0;
        float r2 = dot( q, q );
        float k = exp( -r2 * 6.0 ) * ( 1.0 - smoothstep( 0.8, 1.0, r2 ) );
        gl_FragColor = vec4( uColor * k * vGlow, 1.0 );
      }`,
  });
  const points = new Points(geometry, material);
  points.name = 'Fireflies';
  points.renderOrder = 30;
  points.visible = false;
  return {
    points,
    setCount(n) {
      geometry.setDrawRange(0, Math.min(n, count));
    },
    /** drawing-buffer pixels per metre at 1 m, for the point size */
    setPixelsPerMeter(v, drawingHeight) {
      material.uniforms.uPixelsPerMeter.value = v;
      material.uniforms.uMaxSize.value = Math.max(2, (6 * drawingHeight) / 1080);
    },
    update() {
      points.visible = lighting.U.uFireflies.value > 0.001;
    },
  };
}
