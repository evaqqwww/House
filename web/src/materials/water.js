// "water": pond surface. COLOR_0.rgb = depth tint (albedo), COLOR_0.a = opacity (from Blender).
// Body = tint lit like the terrain under it (baked layers + direct light with the shadow map); Fresnel reflection
// of the time-of-day sky gradient (low reflected rays see the forest rim, lit by the current light) and glints of
// the sun or moon on animated ripples.
import { Color, ShaderMaterial } from 'three';
import { LIGHT_PARS, NOISE, SHADOW_PARS } from '../shaderlib.js';
import { SKY_GLSL } from './sky.js';

const WATER = { forest: [0.05, 0.09, 0.045], body: 0.45, ripple: 0.06, glint: 0.6 };

export function createWaterMaterial(manifest, lighting, textures) {
  return new ShaderMaterial({
    name: 'Water',
    vertexColors: true,
    transparent: true,
    depthWrite: false,
    uniforms: {
      ...lighting.U,
      tSky: { value: textures.sky },
      tBounce: { value: textures.bounce },
      tLamp: { value: textures.lamp },
      tZenith: { value: textures.zenith },
      uTerrainHalf: { value: manifest.terrainUV.half },
      uForest: { value: new Color().fromArray(WATER.forest) },
      uRipple: { value: WATER.ripple },
      uGlint: { value: WATER.glint },
      uBody: { value: WATER.body },
    },
    vertexShader: /* glsl */ `
      ${SHADOW_PARS}
      varying vec4 vTint;
      varying vec3 vWorld;
      varying vec3 vShadow;
      void main() {
        vTint = color; // RGBA vertex colour (USE_COLOR_ALPHA)
        vec4 wp = modelMatrix * vec4( position, 1.0 );
        vWorld = wp.xyz;
        vShadow = shadowCoord( wp.xyz, vec3( 0.0, 1.0, 0.0 ) );
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: /* glsl */ `
      #include <common>
      ${LIGHT_PARS}
      ${SHADOW_PARS}
      ${NOISE}
      ${SKY_GLSL}
      uniform float uTime;
      uniform sampler2D tSky;
      uniform sampler2D tBounce;
      uniform sampler2D tLamp;
      uniform sampler2D tZenith;
      uniform float uTerrainHalf;
      uniform vec3 uForest;
      uniform float uRipple;
      uniform float uGlint;
      uniform float uBody; // light reaching the water body (scattered under the surface)
      varying vec4 vTint;
      varying vec3 vWorld;
      varying vec3 vShadow;

      float heightField( vec2 p ) {
        float h = 0.0;
        h += 0.50 * vnoise( p * 1.6 + vec2( uTime * 0.25, uTime * 0.18 ) );
        h += 0.30 * vnoise( p * 3.3 - vec2( uTime * 0.31, -uTime * 0.22 ) );
        h += 0.20 * vnoise( p * 7.1 + vec2( -uTime * 0.45, uTime * 0.37 ) );
        return h;
      }

      void main() {
        vec2 p = vWorld.xz;
        float e = 0.04;
        float h0 = heightField( p );
        vec3 n = normalize( vec3( -( heightField( p + vec2( e, 0.0 ) ) - h0 ) / e * uRipple, 1.0,
                                  -( heightField( p + vec2( 0.0, e ) ) - h0 ) / e * uRipple ) );
        vec3 V = normalize( cameraPosition - vWorld );
        vec3 R = reflect( -V, n );
        R.y = abs( R.y );
        float F = 0.02 + 0.98 * pow( 1.0 - saturate( dot( n, V ) ), 5.0 );

        float sh = shadowLookup( vShadow );
        vec3 kd = keyDir();
        // the forest rim fills the reflection up to ~35 degrees elevation
        vec3 forest = uForest * ( uSkyUp * 0.6 + uGroundRad + keyRad() * max( kd.y, 0.0 ) * 0.5 ) + uLampRad * 0.02;
        vec3 refl = mix( forest, skyRadiance( R ), smoothstep( 0.35, 0.75, R.y ) );

        vec2 uv = ( vWorld.xz + uTerrainHalf ) / ( 2.0 * uTerrainHalf );
        vec3 S = decodeSky( texture2D( tSky, uv ).rgb );
        vec3 B = decodeBounce( texture2D( tBounce, uv ).rgb );
        float Lp = decodeLamp( texture2D( tLamp, uv ).r );
        vec3 light = keyRad() * max( kd.y, 0.0 ) * sh + fillRad() * max( fillDir().y, 0.0 ) + bakedLight( S, B, Lp, texture2D( tZenith, uv ).r );
        vec3 body = vTint.rgb * light * uBody;

        float rk = saturate( dot( R, kd ) );
        float rf = saturate( dot( R, fillDir() ) );
        vec3 sparkle = uGlint * ( keyRad() * sh * ( pow( rk, 900.0 ) * 6.0 + pow( rk, 60.0 ) * 0.08 )
                                + fillRad() * ( pow( rf, 900.0 ) * 6.0 + pow( rf, 60.0 ) * 0.08 ) ) * 3.14159;

        // Cycles absorbs and refracts the light from the pond bottom; the flat surface shows less of it
        float a = mix( vTint.a, 1.0, 0.4 );
        vec3 premul = body * a * ( 1.0 - F ) + refl * F + sparkle;
        float outA = saturate( a * ( 1.0 - F ) + F );
        gl_FragColor = vec4( premul / max( outA, 1e-3 ), outA );
        #include <colorspace_fragment>
      }`,
  });
}
