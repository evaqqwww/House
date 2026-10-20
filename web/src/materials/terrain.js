// "terrain": albedo (COLOR_0) lit by the real-time sun/moon with the shadow map plus the baked terrain layers
// (terrain_sky = S, terrain_bounce = B, terrain_lamp = Lp, terrain_zenith = Z; planar UV, see manifest.terrainUV).
// Where grass grows (grass.png density) the ground takes the colour and the light response of the grass carpet
// (the meadow model in shaderlib.js) with soft combed streaks, so the meadow reads as grass between the instanced
// blades and looks the same whatever the blade count of the tier.
import { Color, ShaderMaterial, Vector2, Vector3 } from 'three';
import { GRASS_COLOR, LIGHT_PARS, NOISE, SHADOW_PARS } from '../shaderlib.js';

const vertexShader = /* glsl */ `
${LIGHT_PARS}
${SHADOW_PARS}
varying vec2 vUv;
varying vec3 vAlbedo;
varying vec3 vKey;
varying vec3 vFill;
varying vec3 vShadow;
varying vec3 vWorld;
varying vec3 vNormal;
void main() {
  vec4 wp = modelMatrix * vec4( position, 1.0 );
  vec3 n = normalize( mat3( modelMatrix ) * normal );
  vNormal = n;
  vKey = keyRad() * max( dot( n, keyDir() ), 0.0 );
  vFill = fillRad() * max( dot( n, fillDir() ), 0.0 );
  vAlbedo = color.rgb;
  vUv = uv;
  vWorld = wp.xyz;
  vShadow = shadowCoord( wp.xyz, n );
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const fragmentShader = /* glsl */ `
${LIGHT_PARS}
${SHADOW_PARS}
${NOISE}
${GRASS_COLOR}
uniform sampler2D tSky;
uniform sampler2D tBounce;
uniform sampler2D tLamp;
uniform sampler2D tDensity;
uniform sampler2D tCover;
uniform sampler2D tZenith;
uniform vec3 uCarpet; // x: carpet strength, y: streak contrast, z: blade-height position seen from above
varying vec2 vUv;
varying vec3 vAlbedo;
varying vec3 vKey;
varying vec3 vFill;
varying vec3 vShadow;
varying vec3 vWorld;
varying vec3 vNormal;
void main() {
  vec3 S = decodeSky( texture2D( tSky, vUv ).rgb );
  vec3 B = decodeBounce( texture2D( tBounce, vUv ).rgb );
  float Lp = decodeLamp( texture2D( tLamp, vUv ).r );
  float dens = texture2D( tDensity, vUv ).r;
  float cover = texture2D( tCover, vUv ).r;
  // At night the entire surface response follows ground cover, independently of how many blades survive a canopy.
  // Keep the established daytime appearance while the night response fades in with the lighting.
  float surface = mix( dens, cover, uGroundNight );
  float Z = texture2D( tZenith, vUv ).r;

  // combed grass streaks: elongated value noise in a slowly turning direction
  vec2 p = vWorld.xz;
  float a = 0.6 + 0.8 * vnoise( p * 0.05 );
  vec2 q = mat2( cos( a ), -sin( a ), sin( a ), cos( a ) ) * p;
  float streak = vnoise( q * vec2( 1.3, 7.5 ) ) * 0.65 + vnoise( q * vec2( 3.1, 17.0 ) ) * 0.35;
  float clump = vnoise( p * 0.33 );
  float t = clamp( uCarpet.z + ( streak - 0.5 ) * 0.7 + ( clump - 0.5 ) * 0.3, 0.0, 1.0 );
  vec3 carpet = meadowTone( grassColor( vAlbedo, t ) ) * ( 1.0 + uCarpet.y * ( streak - 0.5 ) );
  float k = clamp( surface * uCarpet.x, 0.0, 1.0 );
  // Restore most of the sparse forest-floor colour beneath canopies, while retaining bounded night lighting.
  // Broad, static clumps keep the recovery from tracing a uniform canopy disc.
  float rootRecovery = 0.65 * mix( 0.85, 1.15, clump );
  float albedoSurface = surface - uGroundNight * rootRecovery * max( cover - dens, 0.0 );
  vec3 albedo = mix( vAlbedo, carpet, clamp( albedoSurface * uCarpet.x, 0.0, 1.0 ) );

  float sh = dot( vKey, vKey ) > 0.0 ? shadowLookup( vShadow ) : 1.0;
  vec3 skyK = mix( vec3( 1.0 ), meadowSkyView( dot( normalize( cameraPosition - vWorld ), normalize( vNormal ) ) ), k );
  vec2 dk = mix( vec2( 1.0 ), meadowDirect(), k );
  vec3 cool = vKey * sh * dk.x + vFill * dk.y + ( skyLight( Z ) * S + uBounceRad * B ) * skyK;
  // Canopy thinning controls blades, not the ground's night response: otherwise every tree leaves a blue disc.
  // Lamp response uses stable surface coverage, so tree-root pools do not change shape as twilight advances.
  float lightCoverage = smoothstep( 0.1, 0.65, cover );
  float lampSurface = cover - rootRecovery * max( cover - dens, 0.0 );
  vec3 lampAlbedo = mix( vAlbedo, carpet, clamp( lampSurface * uCarpet.x, 0.0, 1.0 ) );
  float coolCoverage = smoothstep( 0.1, 0.65, cover );
  // Bare banks and terrace slopes share the ground's night exposure; sparse grass must not reveal a bright rim.
  vec3 bareCoolGain = vec3( mix( 1.0, uMeadowNightCool, uGroundNight ) );
  vec3 lk = mix( vec3( 1.0 ), meadowLampGain(), lightCoverage );
  vec3 radiance = albedo * cool * mix( bareCoolGain, meadowCoolGain(), coolCoverage )
                + lampAlbedo * uLampRad * Lp * lk;
  gl_FragColor = vec4( radiance, 1.0 );
  #include <colorspace_fragment>
}`;

export function grassColorUniforms(manifest, defaults) {
  const g = { ...defaults, ...manifest.grass };
  return {
    uGrassRoot: { value: new Color().fromArray(g.rootColor) },
    uGrassTip: { value: new Color().fromArray(g.tipColor) },
    uGrassMix: { value: new Vector2(g.rootMix, g.tipMix) },
    uMeadowTone: { value: new Vector3().fromArray(g.meadowTone) },
    uMeadowSky: { value: new Vector3().fromArray(g.meadowSky) },
    uMeadowSkyView: { value: g.meadowSkyView },
    uMeadowNight: { value: g.meadowNight },
    uMeadowNightCool: { value: g.meadowNightCool },
    uMeadowNightTint: { value: new Vector3().fromArray(g.meadowNightTint) },
    uMeadowLampTone: { value: new Vector3().fromArray(g.meadowLampTone) },
  };
}

// grass carpet: strength over full grass density, streak contrast, blade-height position seen from above
const CARPET = { carpet: 0.85, streaks: 0.22, height: 0.62 };

export function createTerrainMaterial(lighting, textures, colorUniforms) {
  return new ShaderMaterial({
    name: 'Terrain',
    vertexColors: true,
    uniforms: {
      ...lighting.U,
      ...colorUniforms,
      tSky: { value: textures.sky },
      tBounce: { value: textures.bounce },
      tLamp: { value: textures.lamp },
      tDensity: { value: textures.density },
      tCover: { value: textures.cover },
      tZenith: { value: textures.zenith },
      uCarpet: { value: new Vector3(CARPET.carpet, CARPET.streaks, CARPET.height) },
    },
    vertexShader,
    fragmentShader,
  });
}
