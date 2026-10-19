// Client-generated grass: N blade roots sampled with a seeded RNG from grass.png (density R, height G/B), weighted
// toward what the two preset cameras see up close (so a phone-sized blade budget lands where blades are visible
// as blades; far meadow reads through the terrain's grass carpet). Blades are 3-triangle strips curved toward the
// combing direction, colour from ground_albedo + Blender's root -> tip gradient, light from the terrain layers
// (S, B, Lp, Z) and the real-time shadow sampled at the root through the meadow model the terrain carpet uses
// (shaderlib.js), all in the vertex shader.
import {
  BufferAttribute,
  DoubleSide,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  Mesh,
  ShaderMaterial,
  Sphere,
  Vector2,
  Vector3,
  Vector4,
} from 'three';
import { GRASS_COLOR, LIGHT_PARS, NOISE, SHADOW_PARS } from '../shaderlib.js';

export const GRASS_DEFAULTS = {
  bladeHeight: [0.2314, 0.3063],
  bladeWidth: 0.0592,
  scaleMin: 0.42,
  scaleMax: 1.37,
  rootColor: [0.09084, 0.20864, 0.0382],
  tipColor: [0.56471, 0.73046, 0.21586],
  rootMix: 0.35,
  tipMix: 0.38,
  valueRange: [0.84, 0.94],
  saturationRange: [0.86, 0.96],
  translucentTint: [0.67244, 0.76052, 0.19462],
  translucentMix: 0.15,
  occlusion: 0.8,
  // meadow model (shaderlib.js GRASS_COLOR), fitted to the Cycles renders: effective colour under direct light,
  // and the share of the sky light and bounce that reaches the grass
  meadowTone: [2.09, 2.8, 1.13],
  meadowSky: [0.34, 0.32, 0.52],
  meadowSkyView: 1.0,
  meadowNight: 0.85,
  // the root -> tip gradient (and darker roots) of a blade average 1 / 1.46 of the carpet colour at its blade-height
  // position: blades are brightened by this, so they add texture but not darkness and every blade count looks alike
  bladeGain: 1.46,
  wind: 0.06,
  seed: 7,
  minPixels: 0.9, // far blades widen to stay about this many pixels wide
  focus: { floor: 0.05, radius: 14 }, // camera weighting: floor + (radius / distance)^2 inside a preset's view
};

/** Seeded PRNG: returns a function giving uniform numbers in [0, 1). */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Seeded blade roots: [x, y, z, rand] * count in three.js coordinates. Every prefix is a random subset of the whole.
 * @param {{width:number,height:number,density:Uint8Array,heightCode:Uint16Array}} map
 * @param {{position: Vector3, forward: Vector3, cosHalf: number}[]} views  preset cameras for the weighting
 * @param {typeof GRASS_DEFAULTS} g  grass parameters (GRASS_DEFAULTS + manifest.grass)
 */
function generateBlades(map, manifest, count, views, g) {
  const { width: W, height: H, density, heightCode } = map;
  const half = manifest.terrainUV.half;
  const size = 2 * half;
  const hMin = g.heightMin;
  const hMax = g.heightMax;
  const focus = g.focus;

  const heightAt = (u, v) => {
    const fx = Math.min(Math.max(u * W - 0.5, 0), W - 1.001);
    const fy = Math.min(Math.max(v * H - 0.5, 0), H - 1.001);
    const x0 = Math.floor(fx);
    const y0 = Math.floor(fy);
    const tx = fx - x0;
    const ty = fy - y0;
    const i = y0 * W + x0;
    const a = heightCode[i] + (heightCode[i + 1] - heightCode[i]) * tx;
    const b = heightCode[i + W] + (heightCode[i + W + 1] - heightCode[i + W]) * tx;
    return hMin + ((a + (b - a) * ty) / 65535) * (hMax - hMin);
  };

  // weights: density * (floor + closest preset view (radius / d)^2 inside its view cone)
  const cdf = new Float64Array(W * H);
  const p = new Vector3();
  const d = new Vector3();
  let acc = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      const dens = density[i] / 255;
      if (dens > 0) {
        const u = (x + 0.5) / W;
        const v = (y + 0.5) / H;
        p.set(u * size - half, heightAt(u, v), v * size - half);
        let best = 0;
        for (const view of views) {
          d.subVectors(p, view.position);
          const dist = d.length();
          if (d.dot(view.forward) < view.cosHalf * dist) continue;
          const k = Math.min(1, (focus.radius / Math.max(dist, 1e-3)) ** 2);
          if (k > best) best = k;
        }
        acc += dens * (focus.floor + best);
      }
      cdf[i] = acc;
    }
  }
  const rng = mulberry32(g.seed);
  const out = new Float32Array(count * 4);
  if (acc <= 0) return { data: out, count: 0 };
  for (let k = 0; k < count; k++) {
    const r = rng() * acc;
    let lo = 0;
    let hi = W * H - 1;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (cdf[mid] < r) lo = mid + 1;
      else hi = mid;
    }
    const u = ((lo % W) + rng()) / W;
    const v = (Math.floor(lo / W) + rng()) / H;
    out[k * 4] = u * size - half;
    out[k * 4 + 1] = heightAt(u, v);
    out[k * 4 + 2] = v * size - half;
    out[k * 4 + 3] = rng();
  }
  return { data: out, count };
}

function bladeGeometry() {
  // (side, t): side -1/+1 = blade edges, 0 = tip; 3 triangles
  const pos = new Float32Array([-1, 0, 0, 1, 0, 0, -1, 0.45, 0, 1, 0.45, 0, 0, 1, 0]);
  const index = new Uint16Array([0, 1, 3, 0, 3, 2, 2, 3, 4]);
  return { position: pos, index };
}

const vertexShader = /* glsl */ `
#define SHADOW_VERTEX
${LIGHT_PARS}
${SHADOW_PARS}
${NOISE}
${GRASS_COLOR}
attribute vec4 iBlade; // root x, y, z, random
uniform float uTime;
uniform vec2 uHeightRange;
uniform vec2 uScaleRange;
uniform float uBladeWidth;
uniform float uTerrainHalf;
uniform sampler2D tAlbedo;
uniform sampler2D tSky;
uniform sampler2D tBounce;
uniform sampler2D tLamp;
uniform sampler2D tZenith;
uniform vec2 uValueRange;
uniform vec2 uSatRange;
uniform float uGrassOcclusion; // light at the blade root
uniform float uBladeGain; // blade colour relative to the carpet (shaderlib.js meadow model)
uniform vec4 uTranslucent; // tint rgb, mix
uniform float uWind;
uniform float uMinWidth; // radians per pixel * min pixels
varying vec3 vColor;

// blades are thin and random in heading: about the flat-ground term on average (so blades and carpet match), plus a
// little of the horizontal light the upright blades catch when the sun is low
float bladeDirect( vec3 l ) {
  return 0.75 * max( l.y, 0.0 ) + 0.25 * sqrt( max( 1.0 - l.y * l.y, 0.0 ) ) * step( 0.0, l.y );
}

void main() {
  vec3 root = iBlade.xyz;
  float rnd = iBlade.w;
  vec4 h = hash42( root.xz * 7.31 + rnd * 13.1 );
  float s = mix( uScaleRange.x, uScaleRange.y, fract( rnd * 7.13 ) );
  float height = mix( uHeightRange.x, uHeightRange.y, h.x ) * s;
  float width = uBladeWidth * mix( 0.85, 1.15, h.y ) * s;
  float dist = distance( cameraPosition, root );
  width = max( width, uMinWidth * dist );
  float bend = mix( 0.2, 0.55, h.z );
  float t = position.y;
  float side = position.x;
  float prof = t < 0.45 ? mix( 1.0, 0.75, t / 0.45 ) : 0.0;
  float twist = ( h.w - 0.5 ) * 1.4 * t;
  float hw = 0.5 * width * prof * side;
  vec3 p = vec3( hw * cos( twist ), height * t * ( 1.0 - 0.18 * bend * t ), -bend * height * t * t + hw * sin( twist ) );
  vec2 leanDir = normalize( h.yz - 0.5 + 1e-4 );
  p.xz += leanDir * p.y * ( h.w * 0.22 );
  float yaw = fract( rnd * 31.7 ) * 6.28318530718;
  float c = cos( yaw ), sn = sin( yaw );
  p = vec3( p.x * c + p.z * sn, p.y, -p.x * sn + p.z * c );
  #ifdef USE_WIND
    float phase = dot( root.xz, vec2( 0.21, 0.17 ) );
    float gust = 0.6 + 0.4 * sin( uTime * 0.35 + root.x * 0.05 );
    p.xz += vec2( sin( uTime * 1.6 + phase ), cos( uTime * 1.3 + phase * 1.7 ) ) * uWind * gust * t * t * height;
  #endif

  vec2 uv = ( root.xz + uTerrainHalf ) / ( 2.0 * uTerrainHalf );
  vec3 ground = textureLod( tAlbedo, uv, 0.0 ).rgb;
  vec3 S = decodeSky( textureLod( tSky, uv, 0.0 ).rgb );
  vec3 B = decodeBounce( textureLod( tBounce, uv, 0.0 ).rgb );
  float Lp = decodeLamp( textureLod( tLamp, uv, 0.0 ).r );
  float Z = textureLod( tZenith, uv, 0.0 ).r;

  vec3 col = grassColor( ground, t );
  float value = mix( uValueRange.x, uValueRange.y, fract( h.x * 17.31 ) );
  float satK = mix( uSatRange.x, uSatRange.y, fract( h.y * 71.7 ) );
  float v = max( col.r, max( col.g, col.b ) );
  col = meadowTone( ( v - ( v - col ) * satK ) * value );

  vec3 kd = keyDir();
  vec3 up = vec3( 0.0, 1.0, 0.0 );
  float sh = shadowLookup( shadowCoord( root + up * 0.12, up ) );
  vec2 dk = meadowDirect();
  vec3 key = keyRad() * bladeDirect( kd ) * sh * dk.x;
  vec3 fill = fillRad() * bladeDirect( fillDir() ) * dk.y;
  vec3 light = key + fill + meadowSkyView( normalize( cameraPosition - root ).y ) * ( skyLight( Z ) * S + uBounceRad * B ) + uLampRad * Lp * uMeadowNight;
  float ao = mix( uGrassOcclusion, 1.0, smoothstep( 0.0, 0.85, t ) );
  // light through the blades when looking toward the key light
  vec3 view = normalize( root - cameraPosition );
  float back = pow( max( dot( view, kd ), 0.0 ), 2.0 );
  vec3 trans = uTranslucent.a * uTranslucent.rgb * keyRad() * sh * ( 0.3 + 2.0 * back ) * t;
  vColor = col * light * ao * uBladeGain + trans;

  gl_Position = projectionMatrix * viewMatrix * vec4( root + p, 1.0 );
}`;

const fragmentShader = /* glsl */ `
varying vec3 vColor;
void main() {
  gl_FragColor = vec4( vColor, 1.0 );
  #include <colorspace_fragment>
}`;

export function createGrass(manifest, grassMap, textures, lighting, colorUniforms, views) {
  const g = { ...GRASS_DEFAULTS, ...manifest.grass };
  const half = manifest.terrainUV.half;

  const geometry = new InstancedBufferGeometry();
  const blade = bladeGeometry();
  geometry.setAttribute('position', new BufferAttribute(blade.position, 3));
  geometry.setIndex(new BufferAttribute(blade.index, 1));
  geometry.boundingSphere = new Sphere(new Vector3(0, 1, 0), half * 1.5);

  const material = new ShaderMaterial({
    name: 'Grass',
    side: DoubleSide,
    defines: { USE_WIND: '' },
    uniforms: {
      ...lighting.U,
      ...colorUniforms,
      uHeightRange: { value: new Vector2().fromArray(g.bladeHeight) },
      uScaleRange: { value: new Vector2(g.scaleMin, g.scaleMax) },
      uBladeWidth: { value: g.bladeWidth },
      uTerrainHalf: { value: half },
      tAlbedo: { value: textures.albedo },
      tSky: { value: textures.sky },
      tBounce: { value: textures.bounce },
      tLamp: { value: textures.lamp },
      tZenith: { value: textures.zenith },
      uValueRange: { value: new Vector2().fromArray(g.valueRange) },
      uSatRange: { value: new Vector2().fromArray(g.saturationRange) },
      uGrassOcclusion: { value: g.occlusion },
      uBladeGain: { value: g.bladeGain },
      uTranslucent: { value: new Vector4(...g.translucentTint, g.translucentMix) },
      uWind: { value: g.wind },
      uMinWidth: { value: 0 },
    },
    vertexShader,
    fragmentShader,
  });

  const mesh = new Mesh(geometry, material);
  mesh.name = 'Grass';
  mesh.frustumCulled = false;
  mesh.matrixAutoUpdate = false;

  const state = { generated: 0, count: 0 };

  function setCount(n) {
    if (n > state.generated) {
      const { data, count } = generateBlades(grassMap, manifest, n, views, g);
      geometry.setAttribute('iBlade', new InstancedBufferAttribute(data, 4));
      state.generated = count;
    }
    state.count = Math.min(n, state.generated);
    geometry.instanceCount = state.count;
    mesh.visible = state.count > 0;
  }

  /** keeps far blades about minPixels wide: radians per drawing-buffer pixel of the current camera */
  function setPixelAngle(radiansPerPixel) {
    material.uniforms.uMinWidth.value = radiansPerPixel * g.minPixels;
  }

  return {
    mesh,
    material,
    setCount,
    setPixelAngle,
    get count() {
      return state.count;
    },
  };
}
