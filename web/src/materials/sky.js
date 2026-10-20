// Sky dome with the sky dome colours of the current hour (timeofday.json skyDome, as Blender's World_Sky shows them to
// the camera): horizon -> zenith by t^curve, t over the view direction's height [-0.05, 0.6]. At night a few stars and
// the moon disc (moon direction from the time-of-day model). Drawn after the opaque scene at the far plane, so it only
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
