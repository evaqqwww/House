// GLSL shared by the materials: the time-of-day light uniforms (lighting.js), the shadow map lookup (shadows.js)
// and small helpers. Lighting is in light units L (radiance = albedo * L).

export const LIGHT_PARS = /* glsl */ `
uniform vec3 uSunDir;
uniform vec3 uSunRad;
uniform vec3 uMoonDir;
uniform vec3 uMoonRad;
uniform vec2 uKeyShadow;
uniform vec3 uSkyHorizonRad;
uniform vec3 uSkyZenithRad;
uniform vec3 uSkyUp;
uniform vec3 uGroundRad;
uniform vec3 uBounceRad;
uniform vec3 uLampRad;
uniform vec3 uLampPos;
uniform vec3 uLampPoint;
uniform vec3 uLayerScale; // decode scales of the baked layers: sky S, bounce B, lamp Lp

// the key light owns the shadow map; the fill light (the other of sun and moon) is unshadowed
vec3 keyDir() { return uKeyShadow.x > 0.5 ? uSunDir : uMoonDir; }
vec3 keyRad() { return uKeyShadow.x > 0.5 ? uSunRad : uMoonRad; }
vec3 fillDir() { return uKeyShadow.x > 0.5 ? uMoonDir : uSunDir; }
vec3 fillRad() { return uKeyShadow.x > 0.5 ? uMoonRad : uSunRad; }

// baked layers are stored as sqrt(x / scale)
vec3 decodeSky( vec3 c ) { return c * c * uLayerScale.x; }
vec3 decodeBounce( vec3 c ) { return c * c * uLayerScale.y; }
float decodeLamp( float c ) { return c * c * uLayerScale.z; }

// sky light colour for a surface with zenith fraction Z (0: sees only horizon-coloured sky, 1: only zenith)
vec3 skyLight( float Z ) {
  return mix( uSkyHorizonRad, uSkyZenithRad, Z );
}

// sky + bounce + lamp light from the baked layers
vec3 bakedLight( vec3 S, vec3 B, float Lp, float Z ) {
  return skyLight( Z ) * S + uBounceRad * B + uLampRad * Lp;
}

// Cycles point light at the lantern (trees only; everything else has it baked in Lp)
vec3 lampPointLight( vec3 p, vec3 n ) {
  vec3 l = uLampPos - p;
  float d2 = max( dot( l, l ), 0.3 );
  return uLampPoint * max( dot( n, l * inversesqrt( d2 ) ), 0.0 ) / d2;
}
`;

export const SHADOW_PARS = /* glsl */ `
#ifndef SHADOW_TAPS
  #define SHADOW_TAPS 4
#endif
uniform highp sampler2DShadow uShadowMap;
uniform mat4 uShadowMatrix;
uniform vec4 uShadowParams; // texel (uv), tap spacing (texels), depth bias, normal offset (m)

vec3 shadowCoord( vec3 worldPos, vec3 worldNormal ) {
  return ( uShadowMatrix * vec4( worldPos + worldNormal * uShadowParams.w, 1.0 ) ).xyz;
}

// Taps are hardware 2x2 PCF lookups (LessEqual compare, linear filtering) on an N x N grid whose spacing
// (uShadowParams.y, shadows.js) makes the grid span about 0.4 m on every tier: a soft penumbra like the 5 degree sun
// of the Cycles renders. Vertex shaders (grass, trees; define SHADOW_VERTEX) use at most the 4-tap grid.
float shadowLookup( vec3 c ) {
  if ( c.x <= 0.0 || c.x >= 1.0 || c.y <= 0.0 || c.y >= 1.0 || c.z >= 1.0 ) return 1.0;
  c.z -= uShadowParams.z;
  float r = uShadowParams.x * uShadowParams.y;
  #if SHADOW_TAPS >= 16 && !defined( SHADOW_VERTEX )
    #define SHADOW_TAP( i, j ) texture( uShadowMap, vec3( c.xy + vec2( i - 1.5, j - 1.5 ) * r, c.z ) )
    return ( 1.0 / 16.0 ) * (
      SHADOW_TAP( 0.0, 0.0 ) + SHADOW_TAP( 1.0, 0.0 ) + SHADOW_TAP( 2.0, 0.0 ) + SHADOW_TAP( 3.0, 0.0 ) +
      SHADOW_TAP( 0.0, 1.0 ) + SHADOW_TAP( 1.0, 1.0 ) + SHADOW_TAP( 2.0, 1.0 ) + SHADOW_TAP( 3.0, 1.0 ) +
      SHADOW_TAP( 0.0, 2.0 ) + SHADOW_TAP( 1.0, 2.0 ) + SHADOW_TAP( 2.0, 2.0 ) + SHADOW_TAP( 3.0, 2.0 ) +
      SHADOW_TAP( 0.0, 3.0 ) + SHADOW_TAP( 1.0, 3.0 ) + SHADOW_TAP( 2.0, 3.0 ) + SHADOW_TAP( 3.0, 3.0 ) );
  #elif SHADOW_TAPS >= 4
    return 0.25 * (
      texture( uShadowMap, vec3( c.xy + vec2( -0.5, -0.5 ) * r, c.z ) ) +
      texture( uShadowMap, vec3( c.xy + vec2( 0.5, -0.5 ) * r, c.z ) ) +
      texture( uShadowMap, vec3( c.xy + vec2( -0.5, 0.5 ) * r, c.z ) ) +
      texture( uShadowMap, vec3( c.xy + vec2( 0.5, 0.5 ) * r, c.z ) ) );
  #else
    return texture( uShadowMap, c );
  #endif
}
`;

export const NOISE = /* glsl */ `
float hash12( vec2 p ) {
  vec3 p3 = fract( vec3( p.xyx ) * 0.1031 );
  p3 += dot( p3, p3.yzx + 33.33 );
  return fract( ( p3.x + p3.y ) * p3.z );
}
vec4 hash42( vec2 p ) {
  vec4 p4 = fract( vec4( p.xyxy ) * vec4( 0.1031, 0.1030, 0.0973, 0.1099 ) );
  p4 += dot( p4, p4.wzxy + 33.33 );
  return fract( ( p4.xxyz + p4.yzzw ) * p4.zywx );
}
float vnoise( vec2 p ) {
  vec2 i = floor( p );
  vec2 f = fract( p );
  vec2 u = f * f * ( 3.0 - 2.0 * f );
  return mix( mix( hash12( i ), hash12( i + vec2( 1.0, 0.0 ) ), u.x ),
              mix( hash12( i + vec2( 0.0, 1.0 ) ), hash12( i + vec2( 1.0, 1.0 ) ), u.x ), u.y );
}
`;

// Grass colour (Blender's M_Grass): root = mix(ground, rootColor, rootMix), tip = mix(ground, tipColor, tipMix),
// the ground colour in between. Shared by the blades and by the terrain carpet under them, which use one meadow
// model calibrated against the Cycles renders (light units of flat ground, radiance = meadowTone(colour) * light):
//   direct light (sun, moon, lamps)  * 1          the blade tops catch it, the canopy scatters it (green)
//   sky light and sun bounce          * uMeadowSky the sky reaches into the dense grass much less than to bare ground
// uMeadowTone turns the grass colour into the meadow's effective colour under direct light (it folds in the light
// scattered between the blades, which the baked layers leave out: they are baked without the grass). The sky light
// reaches only the top of the grass: seen at a grazing angle the meadow shows those sky-lit tops, seen from above
// the darker depths between the blades (meadowSkyView, 1 at 45 degrees). The moon and the lantern light the grass
// from the side or at a grazing angle and read dimmer than the sun behind the preset cameras (uMeadowNight).
export const GRASS_COLOR = /* glsl */ `
uniform vec3 uGrassRoot;
uniform vec3 uGrassTip;
uniform vec2 uGrassMix; // rootMix, tipMix
uniform vec3 uMeadowTone;
uniform vec3 uMeadowSky;
uniform float uMeadowSkyView; // gain of the sky light per unit of (cos 45 deg - cos view angle)
uniform float uMeadowNight; // gain of the moonlight and the lamp light on the grass
// gains of the key and fill light on the grass: the moon (whichever it is) gets uMeadowNight
vec2 meadowDirect() {
  return uKeyShadow.x > 0.5 ? vec2( 1.0, uMeadowNight ) : vec2( uMeadowNight, 1.0 );
}
vec3 meadowSkyView( float mu ) {
  return uMeadowSky * clamp( 1.0 + uMeadowSkyView * ( 0.7071 - mu ), 0.5, 1.6 );
}
vec3 grassColor( vec3 ground, float t ) {
  vec3 rootC = mix( ground, uGrassRoot, uGrassMix.x );
  vec3 tipC = mix( ground, uGrassTip, uGrassMix.y );
  vec3 col = mix( rootC, ground, min( 2.0 * t, 1.0 ) );
  return mix( col, tipC, pow( t, 1.4 ) );
}
vec3 meadowTone( vec3 col ) {
  return col * uMeadowTone;
}
`;
