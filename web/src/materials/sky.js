// Sky dome with the sky dome colours of the current hour (timeofday.json skyDome, as Blender's World_Sky shows them to
// the camera): horizon -> zenith by t^curve, t over the view direction's height [-0.05, 0.6]. At night a few stars and
// the moon disc (moon direction from the time-of-day model), behind softly shaded cumulus banks. Drawn after the opaque
// scene at the far plane, so it only
// fills the pixels left uncovered. It writes alpha 0 (everything else 1), and the look pass gives these pixels only
// bloom, the vignette and the view transform.
import { BackSide, Mesh, ShaderMaterial, SphereGeometry } from 'three';
import { NOISE } from '../shaderlib.js';

export const SKY_GLSL = /* glsl */ `
uniform vec3 uSkyZenith;
uniform vec3 uSkyHorizon;
uniform float uSkyCurve;
uniform vec2 uSkyRange;
vec3 skyRadiance( vec3 dir ) {
  float t = clamp( ( dir.y - uSkyRange.x ) / ( uSkyRange.y - uSkyRange.x ), 0.0, 1.0 );
  return mix( uSkyHorizon, uSkyZenith, pow( t, uSkyCurve ) );
}
`;

// Rounded ellipsoid lobes give the clouds a sculpted silhouette and smooth surface lighting without volume marching
// or extra textures/draw calls. Angular coordinates keep the banks distant when orbiting and wrap around the dome.
const CLOUD_GLSL = /* glsl */ `
uniform vec3 uSunDir;

void cloudLobe( vec2 p, vec2 center, vec2 radius, float depth, inout vec4 surface, inout float coverage ) {
  vec2 q = ( p - center ) / radius;
  float r2 = dot( q, q );
  float alpha = 1.0 - smoothstep( 0.94, 1.04, r2 );
  if ( alpha <= 0.0 ) return;
  float z = sqrt( max( 1.0 - r2, 0.001 ) );
  vec3 normal = normalize( vec3( q / radius, z / 0.28 ) );
  float front = depth + z * 0.28;
  // Smooth the front surfaces where lobes join, retaining the broad puff shapes.
  float k = clamp( 0.5 + 0.5 * ( front - surface.w ) / 0.10, 0.0, 1.0 );
  surface.xyz = mix( surface.xyz, normal, k );
  surface.w = mix( surface.w, front, k ) + 0.10 * k * ( 1.0 - k );
  coverage = max( coverage, alpha );
}

vec4 cloudBank( vec3 dir, float count, float height, float seed, float drift, float layerScale ) {
  float azimuth = atan( dir.z, dir.x );
  float x = ( azimuth / 6.2831853 + 0.5 ) * count + drift;
  float cell = floor( x );
  vec4 bank = vec4( 0.0 );
  for ( int i = -1; i <= 1; i++ ) {
    float id = cell + float( i );
    vec2 key = vec2( mod( id, count ), seed );
    vec4 rnd = hash42( key );
    vec4 shape = hash42( key + vec2( 37.2, 19.7 ) );
    // Most neighbourhoods stay full; a few have gaps between clusters instead of a uniform dotted row.
    float clusterDensity = hash12( vec2( floor( key.x / 3.0 ), seed + 11.2 ) );
    if ( clusterDensity < 0.24 && shape.x > 0.40 ) continue;
    vec2 p = vec2( x - id - 0.5, ( dir.y - height ) * count / 6.2831853 );
    p -= vec2( ( rnd.x - 0.5 ) * 0.65, ( rnd.y - 0.5 ) * 0.50 );
    // Small, medium and broad groups share a wider size distribution; the upper tier uses a smaller scale.
    float size = mix( 0.46, 1.10, pow( rnd.z, 1.15 ) ) * layerScale;
    p.x /= size * mix( 0.78, 1.35, shape.x );
    p.y /= size * mix( 0.42, 0.82, rnd.w );
    p.x *= shape.w < 0.5 ? -1.0 : 1.0;
    if ( abs( p.x ) > 0.74 || p.y < -0.30 || p.y > 0.68 ) continue;

    vec4 surface = vec4( 0.0, 0.0, 1.0, -10.0 );
    float coverage = 0.0;
    // Two to five unequal crown lobes, with an off-centre peak, replace the repeated six-lobe silhouette.
    float puffCount = 2.0 + floor( shape.z * 4.0 );
    float spread = mix( 0.25, 0.43, ( puffCount - 2.0 ) / 3.0 );
    float peak = mix( 0.18, 0.82, shape.y );
    for ( int l = 0; l < 5; l++ ) {
      if ( float( l ) >= puffCount ) continue;
      vec4 puff = hash42( key + vec2( float( l ) * 7.13 + 83.1, 53.4 ) );
      float u = float( l ) / ( puffCount - 1.0 );
      float crown = max( 0.0, 1.0 - abs( u - peak ) * 1.6 );
      vec2 center = vec2( ( u - 0.5 ) * spread * 2.0 + ( puff.x - 0.5 ) * 0.09,
                          0.015 + crown * mix( 0.10, 0.25, puff.y ) );
      vec2 radius = vec2( mix( 0.17, 0.25, puff.z ) * mix( 1.20, 0.90, ( puffCount - 2.0 ) / 3.0 ),
                          mix( 0.15, 0.30, puff.w ) );
      cloudLobe( p, center, radius, mix( -0.035, 0.045, puff.x ), surface, coverage );
    }
    cloudLobe( p, vec2( ( shape.y - 0.5 ) * 0.10, -0.07 ), vec2( 0.46, 0.15 ), 0.07, surface, coverage );

    vec3 n = normalize( surface.xyz );
    vec3 tangent = vec3( -sin( azimuth ), 0.0, cos( azimuth ) );
    vec3 normal = n.x * tangent + vec3( 0.0, n.y, 0.0 ) - n.z * vec3( cos( azimuth ), 0.0, sin( azimuth ) );
    vec3 lightDir = normalize( mix( uSunDir, uMoonDir, uNight ) );
    float light = clamp( 0.60 + 0.24 * n.y + 0.16 * dot( normal, lightDir ), 0.25, 1.0 );
    float twilight = 1.0 - smoothstep( 0.08, 0.55, uSunDir.y );
    vec3 dayTop = mix( vec3( 0.85, 0.90, 0.82 ), vec3( 0.95, 0.57, 0.32 ), twilight * 0.65 );
    vec3 day = dayTop * light + skyRadiance( dir ) * ( 0.28 + 0.18 * ( 1.0 - light ) );
    vec3 night = ( skyRadiance( dir ) * 1.35 + vec3( 0.006, 0.012, 0.035 ) ) * ( 0.72 + 0.28 * light );
    vec3 color = mix( day, night, uNight );
    // The lower third loses both contrast and opacity into the local sky, like distant, hazy cumulus bases.
    float base = smoothstep( -0.22, 0.12, p.y );
    float skyMix = 0.68 * ( 1.0 - smoothstep( -0.10, 0.24, p.y ) );
    color = mix( color, skyRadiance( dir ), skyMix );
    coverage *= base * mix( 1.0, 0.86, uNight );
    // The scene's cameras look down; the lower banks deliberately extend below the geometric horizon.
    coverage *= smoothstep( -0.75, -0.65, dir.y );
    bank.rgb = mix( bank.rgb, color, coverage );
    bank.a = bank.a + coverage * ( 1.0 - bank.a );
  }
  return bank;
}

vec4 skyClouds( vec3 dir ) {
  // Use a bounded periodic drift so long-running sessions retain small floating-point coordinates.
  float drift = mod( uTime * 0.0015, 6.2831853 );
  // The overview sees the artistic sky well below the horizon. Cull empty height bands before testing any lobes.
  vec4 lowBank = vec4( 0.0 );
  vec4 middleBank = vec4( 0.0 );
  vec4 farBank = vec4( 0.0 );
  vec4 nearBank = vec4( 0.0 );
  vec4 upperBank = vec4( 0.0 );
  if ( dir.y < -0.30 ) lowBank = cloudBank( dir, 22.0, -0.53, 43.7, drift * 22.0 / 6.2831853, 1.0 );
  if ( dir.y > -0.42 && dir.y < -0.10 ) middleBank = cloudBank( dir, 29.0, -0.28, 19.4, drift * 29.0 / 6.2831853, 1.0 );
  if ( dir.y > -0.20 && dir.y < 0.12 ) farBank = cloudBank( dir, 31.0, -0.09, 8.3, drift * 31.0 / 6.2831853, 1.0 );
  if ( dir.y > 0.00 && dir.y < 0.32 ) nearBank = cloudBank( dir, 30.0, 0.13, 27.1, drift * 30.0 / 6.2831853, 1.0 );
  // A staggered tier of smaller groups fills selected gaps above the main banks, adding depth and uneven density.
  if ( dir.y > 0.11 && dir.y < 0.40 ) upperBank = cloudBank( dir, 34.0, 0.205, 71.6, drift * 34.0 / 6.2831853, 0.72 );
  // The colours are premultiplied while accumulating lobes/banks.
  nearBank += upperBank * ( 1.0 - nearBank.a );
  farBank += ( middleBank + lowBank * ( 1.0 - middleBank.a ) ) * ( 1.0 - farBank.a );
  return vec4( nearBank.rgb + farBank.rgb * ( 1.0 - nearBank.a ), nearBank.a + farBank.a * ( 1.0 - nearBank.a ) );
}
`;

export function createSkyDome(lighting) {
  const U = lighting.U;
  const material = new ShaderMaterial({
    name: 'Sky',
    uniforms: {
      uSkyZenith: U.uSkyZenith,
      uSkyHorizon: U.uSkyHorizon,
      uSkyCurve: U.uSkyCurve,
      uSkyRange: U.uSkyRange,
      uNight: U.uNight,
      uMoonDir: U.uMoonDir,
      uSunDir: U.uSunDir,
      uTime: U.uTime,
    },
    vertexShader: /* glsl */ `
      varying vec3 vDir;
      void main() {
        vDir = position;
        vec4 p = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
        gl_Position = p.xyww; // far plane: fills only what the scene left uncovered
      }`,
    fragmentShader: /* glsl */ `
      ${SKY_GLSL}
      ${NOISE}
      uniform float uNight;
      uniform vec3 uMoonDir;
      uniform float uTime;
      varying vec3 vDir;
      ${CLOUD_GLSL}
      void main() {
        vec3 dir = normalize( vDir );
        vec3 col = skyRadiance( dir );
        if ( uNight > 0.0 && dir.y > -0.3 ) {
          // stars: one candidate per cell of a direction grid, over all the sky the cameras see (they look down, so
          // much of it lies past the terrain, below the horizon)
          vec3 g = dir * 90.0;
          vec3 cell = floor( g );
          float hsh = hash12( cell.xy + cell.z * 17.13 );
          if ( hsh > 0.985 ) {
            vec3 c = cell + 0.5 + ( hash42( cell.yz + cell.x * 3.7 ).xyz - 0.5 ) * 0.6;
            float d = length( g - c );
            float tw = 0.75 + 0.25 * sin( uTime * ( 1.5 + 3.0 * hsh ) + hsh * 40.0 );
            col += vec3( 0.75, 0.8, 1.0 ) * smoothstep( 0.22, 0.0, d ) * ( hsh - 0.985 ) * 40.0 * tw * uNight * smoothstep( -0.3, -0.15, dir.y );
          }
          // moon disc and halo
          float m = dot( dir, uMoonDir );
          col += vec3( 0.85, 0.9, 1.0 ) * ( smoothstep( 0.99975, 0.99985, m ) * 3.0 + pow( max( m, 0.0 ), 300.0 ) * 0.15 ) * uNight;
        }
        vec4 clouds = skyClouds( dir );
        col = col * ( 1.0 - clouds.a ) + clouds.rgb;
        gl_FragColor = vec4( col, 0.0 ); // alpha 0 marks the sky dome for the look pass
        #include <colorspace_fragment>
      }`,
    side: BackSide,
    depthWrite: false,
  });
  const dome = new Mesh(new SphereGeometry(300, 32, 16), material);
  dome.name = 'SkyDome';
  dome.frustumCulled = false;
  dome.renderOrder = 1000;
  dome.onBeforeRender = (renderer, scene, camera) => {
    dome.position.copy(camera.position);
    dome.updateMatrixWorld();
  };
  return dome;
}
