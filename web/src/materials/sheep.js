// Moving surfaces use the current sky/ground hemisphere and light directions, rather than the static bake.
import { Color, ShaderMaterial } from 'three';
import { LIGHT_PARS, SHADOW_PARS } from '../shaderlib.js';

const vertexShader = /* glsl */ `
uniform vec3 uAlbedo;
varying vec3 vAlbedo;
varying vec3 vWorldPosition;
varying vec3 vWorldNormal;
void main() {
  vec4 wp = modelMatrix * vec4( position, 1.0 );
  // normalMatrix handles the ellipsoids' nonuniform scale; undo the camera rotation to get a world normal.
  vec3 vn = normalMatrix * normal;
  vWorldNormal = normalize( vec3( dot( viewMatrix[0].xyz, vn ), dot( viewMatrix[1].xyz, vn ), dot( viewMatrix[2].xyz, vn ) ) );
  vWorldPosition = wp.xyz;
  vAlbedo = uAlbedo;
  #ifdef USE_COLOR
    vAlbedo *= color.rgb;
  #endif
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const fragmentShader = /* glsl */ `
${LIGHT_PARS}
${SHADOW_PARS}
varying vec3 vAlbedo;
varying vec3 vWorldPosition;
varying vec3 vWorldNormal;
void main() {
  vec3 n = normalize( vWorldNormal );
  float sh = shadowLookup( shadowCoord( vWorldPosition, n ) );
  vec3 direct = keyRad() * max( dot( n, keyDir() ), 0.0 ) * sh;
  direct += fillRad() * max( dot( n, fillDir() ), 0.0 );
  vec3 hemisphere = mix( uGroundRad, skyLight( 0.85 ), clamp( 0.5 + 0.5 * n.y, 0.0, 1.0 ) );
  vec3 lamp = lampPointLight( vWorldPosition, n );
  gl_FragColor = vec4( vAlbedo * ( direct + 0.9 * hemisphere + lamp ), 1.0 );
  #include <colorspace_fragment>
}`;

export function createSheepMaterial(lighting, { color = new Color(0xeee6d8), vertexColors = false } = {}) {
  return new ShaderMaterial({
    name: 'Sheep_LiveLight',
    vertexColors,
    uniforms: { ...lighting.U, uAlbedo: { value: color.clone() } },
    vertexShader,
    fragmentShader,
  });
}

export function createSheepContactMaterial(lighting) {
  return new ShaderMaterial({
    name: 'Sheep_ContactShadow',
    uniforms: { uNight: lighting.U.uNight },
    transparent: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
      }`,
    fragmentShader: /* glsl */ `
      uniform float uNight;
      varying vec2 vUv;
      void main() {
        vec2 q = ( vUv - 0.5 ) * 2.0;
        float a = 1.0 - smoothstep( 0.05, 1.0, dot( q, q ) );
        gl_FragColor = vec4( 0.0, 0.0, 0.0, a * mix( 0.25, 0.18, uNight ) );
        #include <colorspace_fragment>
      }`,
  });
}
