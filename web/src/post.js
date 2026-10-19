// Post-processing (postprocessing 6.39), linear HDR (HalfFloat) throughout:
//   RenderPass -> [scene stats] -> BloomPass (highlights at reduced resolution + weighted mip chain, Blender's
//   kernel) -> EffectPass( MiniatureDof composite, Look ) -> screen (sRGB)
// Look is the Blender compositor in its order, in one shader: + bloom -> haze (depth: quadratic from start over
// depth, capped at hazeAmount; background = full) -> night grade (lum = Rec.709 luminance,
// dark = 1 - smoothstep(low, high, lum), c = mix(c, lum * nightColor, nightShift * dark)) -> * tint -> saturation
// -> vignette 0.22 -> exposure 2^EV -> Blender's view transform (AgX, look 'AgX - Base Contrast', viewlut.js). Every
// time-dependent value comes from the time-of-day state.
// MiniatureDof: thin-lens circle of confusion from the Blender cameras, gathered at reduced resolution with a fixed
// number of samples on a golden-angle spiral (rotated per pixel) from a mip-mapped copy of the image; each sample
// counts where its own CoC reaches the pixel, so blurred foreground spreads over sharp pixels and sharp foreground is
// not smeared by the background.
import {
  HalfFloatType,
  LinearFilter,
  LinearMipmapLinearFilter,
  NearestFilter,
  NoBlending,
  ShaderMaterial,
  Uniform,
  Vector2,
  Vector3,
  WebGLRenderTarget,
} from 'three';
import { LUT_LOG_RANGE, VIEW_GLSL } from './viewlut.js';
import {
  BlendFunction,
  Effect,
  EffectAttribute,
  EffectComposer,
  EffectPass,
  LambdaPass,
  MipmapBlurPass,
  Pass,
  RenderPass,
  Resolution,
  ShaderPass,
} from 'postprocessing';

// ---------------------------------------------------------------- bloom (Blender Glare 'Bloom')
// Blender (Glare node, type Bloom, threshold 0.9, smoothness 0.1, size 0.6, quality medium): highlights are the
// colour scaled to the luminance above the threshold; the glow added is strength * (kernel * highlights), with a
// kernel of total energy ~0.53 spread about evenly over the octaves from 2 px to ~400 px at 1920 x 1080 (measured
// on test images in background Blender). Here: the highlights at reduced resolution (the "core", 1 - 2 px), then a
// mip chain whose upsampling mixes each level with its own weight (BLOOM_WEIGHTS, fitted to Blender's radial energy
// profile), so the glow reaches as far as Blender's for every tier.
const highlightFrag = /* glsl */ `
uniform sampler2D inputBuffer;
uniform vec2 texelSize; // source texel
uniform vec3 threshold; // threshold, smoothness, tap offset (source texels)
varying vec2 vUv;
vec3 highlight( vec3 c ) {
  c = max( c, vec3( 0.0 ) );
  float l = dot( c, vec3( 0.2126, 0.7152, 0.0722 ) );
  float d = l - threshold.x;
  float k = max( threshold.y, 1e-4 );
  float nl = d >= k ? d : ( d <= -k ? 0.0 : ( d + k ) * ( d + k ) / ( 4.0 * k ) );
  return l > 0.0 ? c * ( nl / l ) : vec3( 0.0 );
}
void main() {
  // four taps cover the target texel (2 x 2 source pixels at half resolution, 4 x 4 at a quarter)
  vec2 o = texelSize * threshold.z;
  vec3 s = highlight( texture2D( inputBuffer, vUv + vec2( -o.x, -o.y ) ).rgb ) + highlight( texture2D( inputBuffer, vUv + vec2( o.x, -o.y ) ).rgb )
         + highlight( texture2D( inputBuffer, vUv + vec2( -o.x, o.y ) ).rgb ) + highlight( texture2D( inputBuffer, vUv + vec2( o.x, o.y ) ).rgb );
  gl_FragColor = vec4( 0.25 * s, 1.0 );
}`;

// glow weights per resolution scale of the highlights: [core, mip 0, mip 1, ...], fitted to the radial energy of
// Blender's bloom (an equal share per octave out to ~400 px at 1080p); their sum is the kernel energy (~0.53)
const BLOOM_WEIGHTS = {
  0.5: [0.101, 0.081, 0.073, 0.058, 0.064, 0.05, 0.064, 0.038],
  0.25: [0.14, 0.078, 0.084, 0.062, 0.052, 0.067, 0.039],
};

class BloomPass extends Pass {
  constructor({ threshold = 0.9, smoothness = 0.1, resolutionScale = 0.5 } = {}) {
    super('BloomPass');
    this.needsSwap = false;
    this.highlights = new WebGLRenderTarget(1, 1, { depthBuffer: false, type: HalfFloatType, minFilter: LinearFilter, magFilter: LinearFilter });
    this.highlights.texture.name = 'Bloom.Highlights';
    this.highlightMaterial = new ShaderMaterial({
      name: 'BloomHighlights',
      uniforms: { inputBuffer: { value: null }, texelSize: { value: new Vector2() }, threshold: { value: new Vector3(threshold, smoothness, 0.5) } },
      vertexShader: 'varying vec2 vUv; void main() { vUv = position.xy * 0.5 + 0.5; gl_Position = vec4( position.xy, 1.0, 1.0 ); }',
      fragmentShader: highlightFrag,
      blending: NoBlending,
      depthWrite: false,
      depthTest: false,
      toneMapped: false,
    });
    this.highlightPass = new ShaderPass(this.highlightMaterial);
    this.mipmapBlurPass = new MipmapBlurPass();
    this.scale = resolutionScale;
    this.size = new Vector2(1, 1);
    this.weights = [];
    this.radii = [];
    this.coreWeight = 0;
    this.wideWeight = 0;
    this.setResolutionScale(resolutionScale);
  }

  get core() {
    return this.highlights.texture;
  }

  get texture() {
    return this.mipmapBlurPass.texture;
  }

  /** weights = [core, mip 0 ... mip L-1]; the chain mixes level k with radius r_k so that level k gets weight w_k */
  setWeights(weights) {
    this.weights = weights.slice();
    const chain = weights.slice(1);
    const total = chain.reduce((a, b) => a + b, 0);
    this.coreWeight = weights[0];
    this.wideWeight = total;
    this.radii = [];
    let rest = 1;
    for (const w of chain) {
      const n = total > 0 ? w / total : 0;
      this.radii.push(rest > 1e-6 ? Math.min(Math.max(1 - n / rest, 0), 1) : 1);
      rest -= n;
    }
    if (this.mipmapBlurPass.levels !== chain.length) {
      this.mipmapBlurPass.levels = chain.length;
      this.setSize(this.size.x, this.size.y);
    }
  }

  render(renderer, inputBuffer) {
    if (!this.enabled) return;
    const m = this.highlightMaterial;
    m.uniforms.inputBuffer.value = inputBuffer.texture;
    m.uniforms.texelSize.value.set(1 / inputBuffer.width, 1 / inputBuffer.height);
    m.uniforms.threshold.value.z = 0.25 / this.scale;
    this.highlightPass.render(renderer, null, this.highlights);
    // MipmapBlurPass.render with a weight per level
    const mp = this.mipmapBlurPass;
    const { downsamplingMaterial: down, upsamplingMaterial: up, downsamplingMipmaps: dm, upsamplingMipmaps: um } = mp;
    let prev = this.highlights;
    mp.fullscreenMaterial = down;
    for (const mip of dm) {
      down.setSize(prev.width, prev.height);
      down.inputBuffer = prev.texture;
      renderer.setRenderTarget(mip);
      renderer.render(mp.scene, mp.camera);
      prev = mip;
    }
    mp.fullscreenMaterial = up;
    for (let i = um.length - 1; i >= 0; --i) {
      up.setSize(prev.width, prev.height);
      up.inputBuffer = prev.texture;
      up.supportBuffer = dm[i].texture;
      up.radius = this.radii[i] ?? 0.6;
      renderer.setRenderTarget(um[i]);
      renderer.render(mp.scene, mp.camera);
      prev = um[i];
    }
  }

  setSize(width, height) {
    this.size.set(width, height);
    const w = Math.max(1, Math.round(width * this.scale));
    const h = Math.max(1, Math.round(height * this.scale));
    this.highlights.setSize(w, h);
    this.mipmapBlurPass.setSize(w, h);
  }

  setResolutionScale(scale) {
    this.scale = scale;
    this.setWeights(BLOOM_WEIGHTS[scale] || BLOOM_WEIGHTS[0.5]);
    this.setSize(this.size.x, this.size.y);
  }

  initialize(renderer, alpha, frameBufferType) {
    this.mipmapBlurPass.initialize(renderer, alpha, frameBufferType);
    if (frameBufferType !== undefined) this.highlights.texture.type = frameBufferType;
  }

  dispose() {
    this.highlights.dispose();
    this.highlightMaterial.dispose();
    this.mipmapBlurPass.dispose();
  }
}

// ---------------------------------------------------------------- look (Blender compositor + view transform)
const lookFrag = /* glsl */ `
uniform sampler2D bloomCore;
uniform sampler2D bloomWide;
uniform vec2 bloomWeights; // core, chain (Blender's glow weights * bloom strength)
uniform vec3 hazeColor;
uniform vec3 hazeParams; // start, depth, amount
uniform vec2 tanHalfFov;
uniform vec3 nightColor;
uniform vec2 nightRange;
uniform float nightShift;
uniform vec3 tint;
uniform float saturation;
uniform float vignette;
uniform vec2 vignetteAspect; // drawing-buffer size / its longer side
uniform vec2 vignetteRamp; // radii (in units of the longer side) where the darkening starts and is full
uniform float exposure;

${VIEW_GLSL}

void mainImage( const in vec4 inputColor, const in vec2 uv, const in float depth, out vec4 outputColor ) {
  vec3 c = max( inputColor.rgb, vec3( 0.0 ) ) + texture2D( bloomCore, uv ).rgb * bloomWeights.x + texture2D( bloomWide, uv ).rgb * bloomWeights.y;
  // haze (Blender mist, quadratic)
  float f = hazeParams.z;
  if ( depth < 0.999999 ) {
    float viewZ = getViewZ( depth );
    vec2 ndc = uv * 2.0 - 1.0;
    float dist = -viewZ * length( vec3( ndc * tanHalfFov, 1.0 ) );
    float m = clamp( ( dist - hazeParams.x ) / hazeParams.y, 0.0, 1.0 );
    f = hazeParams.z * m * m;
  }
  c = mix( c, hazeColor, f );
  // night grade: dark areas toward lum * nightColor, lamp-lit areas keep their colour
  float lum = dot( c, vec3( 0.2126, 0.7152, 0.0722 ) );
  float dark = 1.0 - smoothstep( nightRange.x, nightRange.y, lum );
  c = mix( c, lum * nightColor, nightShift * dark );
  c *= tint;
  // HSV saturation scale (hue and value kept), as Blender's Hue/Saturation node
  float v = max( c.r, max( c.g, c.b ) );
  c = max( v - ( v - c ) * saturation, vec3( 0.0 ) );
  // Blender: mix(image, black, vignette * (1 - blur(ellipse mask))); its mask (size 0.95, blurred 300 px) is a disc
  // of radius 0.475 x the image width: measured on a flat image, 1 - 0.22 * smoothstep(0.37, 0.58, r / width)
  vec2 p = ( uv - 0.5 ) * vignetteAspect;
  c *= 1.0 - vignette * smoothstep( vignetteRamp.x, vignetteRamp.y, length( p ) );
  outputColor = vec4( viewTransform( c * exposure ), inputColor.a );
}`;

class LookEffect extends Effect {
  constructor(uniforms) {
    super('LookEffect', lookFrag, { attributes: EffectAttribute.DEPTH, blendFunction: BlendFunction.SRC, uniforms });
  }
}

// ---------------------------------------------------------------- depth of field
// CoC radius in full-resolution pixels for planar depth z: cocScale * (z - focus) / z (signed: negative in front of
// the focus), clamped to maxBlur, with cocScale = A * W / (4 (s - f) tan(fovX / 2)) (A = aperture diameter f/N,
// W = drawing-buffer width). The Blender cameras blur the near foreground by well over 100 px at 1080p, so the
// gather reads a mip-mapped, reduced-resolution copy of the image (colour + signed CoC) at the mip level that matches
// its sample spacing, and sizes its disc per pixel from its own CoC and the largest foreground CoC nearby (tile
// maximum, dilated by maxBlur): blurred foreground spreads over sharp pixels, background never over sharper
// foreground.
const COC_GLSL = /* glsl */ `
uniform float cameraNearDof;
uniform float cameraFarDof;
uniform float focusDist;
uniform float cocScale;
uniform float maxBlur;
float linearZ( const in float depth ) {
  return -perspectiveDepthToViewZ( depth, cameraNearDof, cameraFarDof );
}
float cocSigned( const in float z ) {
  return clamp( cocScale * ( z - focusDist ) / max( z, 1e-3 ), -maxBlur, maxBlur );
}
float cocOf( const in float z ) {
  return abs( cocSigned( z ) );
}`;

const FULLSCREEN_VERT = 'varying vec2 vUv; void main() { vUv = position.xy * 0.5 + 0.5; gl_Position = vec4( position.xy, 1.0, 1.0 ); }';

// colour + signed CoC at the gather resolution (one bilinear colour tap covers the 2 x 2 pixels of a texel)
const dofPrefilterFrag = /* glsl */ `
#include <common>
#include <packing>
uniform sampler2D inputBuffer;
uniform highp sampler2D depthBuffer;
${COC_GLSL}
varying vec2 vUv;
void main() {
  gl_FragColor = vec4( texture2D( inputBuffer, vUv ).rgb, cocSigned( linearZ( texture2D( depthBuffer, vUv ).r ) ) );
}`;

// largest foreground CoC per tile (TILE x TILE prefilter texels), read from prefilter mip 1
const dofTileFrag = /* glsl */ `
uniform sampler2D prefilter;
uniform vec2 prefilterSize; // texels of prefilter level 0
varying vec2 vUv;
void main() {
  vec2 base = floor( vUv * prefilterSize / float( TILE ) ) * float( TILE );
  float m = 0.0;
  for ( int y = 0; y < TILE / 2; y ++ ) for ( int x = 0; x < TILE / 2; x ++ ) {
    vec2 p = ( base + vec2( x, y ) * 2.0 + 1.0 ) / prefilterSize;
    m = max( m, -textureLod( prefilter, p, 1.0 ).a );
  }
  gl_FragColor = vec4( m, 0.0, 0.0, 1.0 );
}`;

// separable maximum over +-reach tiles
const dofDilateFrag = /* glsl */ `
uniform sampler2D tiles;
uniform vec2 tileStep; // one tile, in uv
uniform float reach; // tiles
varying vec2 vUv;
void main() {
  float m = 0.0;
  for ( int i = -DILATE; i <= DILATE; i ++ ) {
    if ( abs( float( i ) ) > reach ) continue;
    m = max( m, texture2D( tiles, vUv + tileStep * float( i ) ).r );
  }
  gl_FragColor = vec4( m, 0.0, 0.0, 1.0 );
}`;

const dofGatherFrag = /* glsl */ `
uniform sampler2D prefilter;
uniform sampler2D nearCoc;
uniform vec2 texelSize; // full-resolution texel
uniform float dofScale; // gather resolution / full resolution
uniform float maxBlur;
varying vec2 vUv;
const float GOLDEN_ANGLE = 2.39996323;
void main() {
  vec4 c0 = textureLod( prefilter, vUv, 0.0 );
  float cc = abs( c0.a );
  // disc radius (full-res px): this pixel's CoC, or the largest foreground CoC that can reach it
  float R = clamp( max( cc, texture2D( nearCoc, vUv ).r ), 0.5, maxBlur );
  float spacing = R * dofScale * 1.7725 / sqrt( float( DOF_SAMPLES ) ); // gather texels between samples
  float lod = clamp( log2( max( spacing * 0.6, 1.0 ) ), 0.0, 5.0 );
  vec3 color = c0.rgb;
  float total = 1.0;
  float spread = cc;
  // per-pixel rotation of the spiral (interleaved gradient noise) turns the sparse pattern into fine noise
  float rot = 6.2831853 * fract( 52.9829189 * fract( dot( gl_FragCoord.xy, vec2( 0.06711056, 0.00583715 ) ) ) );
  float ring = R / sqrt( float( DOF_SAMPLES ) );
  for ( int i = 0; i < DOF_SAMPLES; i ++ ) {
    float fi = float( i ) + 0.5;
    float radius = R * sqrt( fi / float( DOF_SAMPLES ) );
    float ang = fi * GOLDEN_ANGLE + rot;
    vec4 s = textureLod( prefilter, vUv + vec2( cos( ang ), sin( ang ) ) * texelSize * radius, lod );
    float ss = abs( s.a );
    bool behind = s.a > c0.a;
    if ( behind ) ss = min( ss, cc * 2.0 ); // background never spreads over sharper foreground
    float m = smoothstep( radius - ring, radius + ring * 0.5, ss );
    color += mix( color / total, s.rgb, m );
    total += 1.0;
    if ( !behind ) spread = max( spread, ss * m );
  }
  gl_FragColor = vec4( color / total, spread );
}`;

const dofCompositeFrag = /* glsl */ `
uniform sampler2D blurBuffer;
${COC_GLSL}
void mainImage( const in vec4 inputColor, const in vec2 uv, const in float depth, out vec4 outputColor ) {
  vec4 b = texture2D( blurBuffer, uv );
  float cc = cocOf( linearZ( depth ) );
  float k = smoothstep( 0.6, 2.2, max( cc, b.a ) );
  outputColor = vec4( mix( inputColor.rgb, b.rgb, k ), inputColor.a );
}`;

const DOF_TILE = 8; // prefilter texels per tile side
const DOF_DILATE = 12; // largest dilation (tiles) the shader is compiled for

class MiniatureDofEffect extends Effect {
  constructor(camera, { resolutionScale = 0.5, samples = 32 } = {}) {
    const shared = {
      cameraNearDof: new Uniform(camera.near),
      cameraFarDof: new Uniform(camera.far),
      focusDist: new Uniform(30),
      cocScale: new Uniform(10),
      maxBlur: new Uniform(20),
    };
    super('MiniatureDofEffect', dofCompositeFrag, {
      attributes: EffectAttribute.DEPTH,
      blendFunction: BlendFunction.SRC,
      uniforms: new Map([['blurBuffer', new Uniform(null)], ...Object.entries(shared)]),
    });
    this.camera = camera;
    this.shared = shared;
    const target = (name, mip = false) => {
      const t = new WebGLRenderTarget(1, 1, {
        depthBuffer: false,
        type: HalfFloatType,
        minFilter: mip ? LinearMipmapLinearFilter : LinearFilter,
        magFilter: LinearFilter,
        generateMipmaps: mip,
      });
      t.texture.name = name;
      return t;
    };
    this.prefilter = target('MiniatureDof.Prefilter', true);
    this.tiles = target('MiniatureDof.Tiles');
    this.tilesH = target('MiniatureDof.TilesH');
    this.tilesV = target('MiniatureDof.TilesV');
    for (const t of [this.tiles, this.tilesH, this.tilesV]) t.texture.minFilter = t.texture.magFilter = NearestFilter;
    this.renderTarget = target('MiniatureDof.Blur');
    this.uniforms.get('blurBuffer').value = this.renderTarget.texture;
    const material = (name, fragmentShader, uniforms, defines = {}) =>
      new ShaderMaterial({ name, defines, uniforms, vertexShader: FULLSCREEN_VERT, fragmentShader, blending: NoBlending, depthWrite: false, depthTest: false, toneMapped: false });
    this.prefilterMaterial = material('MiniatureDofPrefilter', dofPrefilterFrag, { inputBuffer: { value: null }, depthBuffer: { value: null }, ...shared });
    this.tileMaterial = material('MiniatureDofTiles', dofTileFrag, { prefilter: { value: this.prefilter.texture }, prefilterSize: { value: new Vector2() } }, { TILE: DOF_TILE });
    this.dilateMaterial = material('MiniatureDofDilate', dofDilateFrag, { tiles: { value: null }, tileStep: { value: new Vector2() }, reach: { value: 1 } }, { DILATE: DOF_DILATE });
    this.gatherMaterial = material(
      'MiniatureDofGather',
      dofGatherFrag,
      { prefilter: { value: this.prefilter.texture }, nearCoc: { value: this.tilesV.texture }, texelSize: { value: new Vector2() }, dofScale: { value: resolutionScale }, maxBlur: shared.maxBlur },
      { DOF_SAMPLES: samples },
    );
    this.prefilterPass = new ShaderPass(this.prefilterMaterial);
    this.tilePass = new ShaderPass(this.tileMaterial);
    this.dilatePass = new ShaderPass(this.dilateMaterial);
    this.gatherPass = new ShaderPass(this.gatherMaterial);
    this.resolution = new Resolution(this, Resolution.AUTO_SIZE, Resolution.AUTO_SIZE, resolutionScale);
    this.resolution.addEventListener('change', () => this.setSize(this.resolution.baseWidth, this.resolution.baseHeight));
  }

  set mainCamera(value) {
    this.camera = value;
  }

  setSamples(n) {
    if (this.gatherMaterial.defines.DOF_SAMPLES !== n) {
      this.gatherMaterial.defines.DOF_SAMPLES = n;
      this.gatherMaterial.needsUpdate = true;
    }
  }

  setDepthTexture(depthTexture) {
    this.prefilterMaterial.uniforms.depthBuffer.value = depthTexture;
  }

  update(renderer, inputBuffer) {
    this.shared.cameraNearDof.value = this.camera.near;
    this.shared.cameraFarDof.value = this.camera.far;
    this.prefilterMaterial.uniforms.inputBuffer.value = inputBuffer.texture;
    this.prefilterPass.render(renderer, null, this.prefilter); // the renderer builds its mipmaps afterwards
    this.tilePass.render(renderer, null, this.tiles);
    // the dilation reaches as far as the largest blur
    const tileFull = DOF_TILE / this.resolution.scale; // full-res pixels per tile
    const d = this.dilateMaterial.uniforms;
    d.reach.value = Math.min(Math.ceil(this.shared.maxBlur.value / tileFull), DOF_DILATE);
    d.tiles.value = this.tiles.texture;
    d.tileStep.value.set(1 / this.tiles.width, 0);
    this.dilatePass.render(renderer, null, this.tilesH);
    d.tiles.value = this.tilesH.texture;
    d.tileStep.value.set(0, 1 / this.tiles.height);
    this.dilatePass.render(renderer, null, this.tilesV);
    this.gatherPass.render(renderer, null, this.renderTarget);
  }

  setSize(width, height) {
    this.resolution.setBaseSize(width, height);
    const w = this.resolution.width;
    const h = this.resolution.height;
    this.prefilter.setSize(w, h);
    this.renderTarget.setSize(w, h);
    const tw = Math.max(1, Math.ceil(w / DOF_TILE));
    const th = Math.max(1, Math.ceil(h / DOF_TILE));
    for (const t of [this.tiles, this.tilesH, this.tilesV]) t.setSize(tw, th);
    this.tileMaterial.uniforms.prefilterSize.value.set(w, h);
    this.gatherMaterial.uniforms.texelSize.value.set(1 / width, 1 / height);
    this.gatherMaterial.uniforms.dofScale.value = w / Math.max(width, 1);
  }

  initialize(renderer, alpha, frameBufferType) {
    if (frameBufferType !== undefined) for (const t of [this.prefilter, this.renderTarget]) t.texture.type = frameBufferType;
  }

  /** focus distance s (m), CoC scale (px), max radius (px) */
  setLens(s, cocScale, maxBlur) {
    this.shared.focusDist.value = s;
    this.shared.cocScale.value = cocScale;
    this.shared.maxBlur.value = maxBlur;
  }

  /** approximate GPU bytes of the buffers */
  get bytes() {
    const p = this.prefilter;
    const t = this.tiles;
    return p.width * p.height * 8 * (4 / 3 + 1) + t.width * t.height * 8 * 3;
  }

  dispose() {
    super.dispose();
    for (const t of [this.prefilter, this.tiles, this.tilesH, this.tilesV, this.renderTarget]) t.dispose();
    for (const m of [this.prefilterMaterial, this.tileMaterial, this.dilateMaterial, this.gatherMaterial]) m.dispose();
  }
}

// ---------------------------------------------------------------- composer
/** @param {import('three').Data3DTexture} viewLut  Blender's view transform (viewlut.js) */
export function createPost(renderer, scene, camera, lighting, manifest, tier, viewLut) {
  const model = lighting.tod.model;
  const { haze } = manifest;
  const composer = new EffectComposer(renderer, { frameBufferType: HalfFloatType, multisampling: tier.msaa });
  composer.addPass(new RenderPass(scene, camera));

  const sceneStats = { calls: 0, triangles: 0 };
  composer.addPass(
    new LambdaPass(() => {
      sceneStats.calls = renderer.info.render.calls;
      sceneStats.triangles = renderer.info.render.triangles;
    }),
  );

  const bloom = new BloomPass({ resolutionScale: tier.bloomScale });
  composer.addPass(bloom);

  const ng = model.nightGrade;
  const U = new Map([
    ['bloomCore', new Uniform(bloom.core)],
    ['bloomWide', new Uniform(bloom.texture)],
    ['bloomWeights', new Uniform(new Vector2())],
    ['hazeColor', new Uniform(new Vector3())],
    ['hazeParams', new Uniform(new Vector3(haze.start, Math.max(haze.depth, 1e-3), 0.35))],
    ['tanHalfFov', new Uniform(new Vector2(1, 1))],
    ['nightColor', new Uniform(new Vector3().fromArray(ng.color))],
    ['nightRange', new Uniform(new Vector2(ng.low, ng.high))],
    ['nightShift', new Uniform(0)],
    ['tint', new Uniform(new Vector3(1, 1, 1))],
    ['saturation', new Uniform(1)],
    ['vignette', new Uniform(0.22)],
    ['vignetteAspect', new Uniform(new Vector2(1, 0.5625))],
    ['vignetteRamp', new Uniform(new Vector2(0.37, 0.58))],
    ['exposure', new Uniform(1)],
    ['viewLut', new Uniform(viewLut)],
    ['viewLutRange', new Uniform(new Vector2().fromArray(LUT_LOG_RANGE))],
  ]);
  // two EffectPasses (with / without depth of field) share the Look uniforms
  const dof = new MiniatureDofEffect(camera, { resolutionScale: tier.dofScale, samples: tier.dofSamples });
  const passDof = new EffectPass(camera, dof, new LookEffect(U));
  const passFlat = new EffectPass(camera, new LookEffect(U));
  passDof.dithering = true;
  passFlat.dithering = true;
  composer.addPass(passDof);
  composer.addPass(passFlat);

  const state = { dofAllowed: tier.dof, maxBlur1080: 140, bloom: 0 };

  // Blender adds strength * glow; the glow weights hold the kernel energy
  function syncBloom() {
    U.get('bloomWeights').value.set(state.bloom * bloom.coreWeight, state.bloom * bloom.wideWeight);
    U.get('bloomWide').value = bloom.texture;
  }

  function applyTimeOfDay(s) {
    state.bloom = s.bloom;
    syncBloom();
    U.get('hazeColor').value.fromArray(s.hazeColor);
    U.get('hazeParams').value.z = s.hazeAmount;
    U.get('nightShift').value = s.nightShift;
    U.get('tint').value.fromArray(s.tint);
    U.get('saturation').value = s.saturation;
    U.get('exposure').value = Math.pow(2, s.exposure);
  }
  lighting.onChange((st) => applyTimeOfDay(st.s));

  function syncPasses() {
    const on = state.dofAllowed;
    passDof.enabled = on;
    passFlat.enabled = !on;
    // the enabled one is the last pass that runs: it renders to the canvas
    passDof.renderToScreen = on;
    passFlat.renderToScreen = !on;
  }

  /** @param {{distance:number, aperture:number, lensM:number}} focus  aperture = lens diameter f/N in metres */
  function update(cam, focus, drawingWidth, drawingHeight) {
    const ty = Math.tan((cam.fov * Math.PI) / 360);
    U.get('tanHalfFov').value.set(ty * cam.aspect, ty);
    const long = Math.max(drawingWidth, drawingHeight, 1);
    U.get('vignetteAspect').value.set(drawingWidth / long, drawingHeight / long);
    if (!passDof.enabled) return;
    const s = Math.max(focus.distance, focus.lensM * 1.5);
    const tanX = ty * cam.aspect;
    const cocScale = (focus.aperture * drawingWidth) / (4 * (s - focus.lensM) * tanX);
    // the blur radius limit follows the shorter side, so portrait and landscape blur alike
    const k = Math.min(drawingWidth, drawingHeight) / 1080;
    dof.setLens(s, cocScale, state.maxBlur1080 * k);
  }

  function setTier(t) {
    composer.multisampling = t.msaa;
    state.dofAllowed = t.dof;
    if (t.dof) {
      dof.resolution.scale = t.dofScale;
      dof.setSamples(t.dofSamples);
    }
    bloom.setResolutionScale(t.bloomScale);
    syncBloom();
    syncPasses();
  }
  syncPasses();

  /** approximate GPU bytes of the post buffers at the current size */
  function bytes(width, height) {
    const px = width * height;
    const samples = Math.max(composer.multisampling, 1);
    let b = px * 8 * 2 + px * 4; // two HalfFloat RGBA buffers + depth
    if (composer.multisampling > 0) b += px * (8 + 4) * samples; // multisampled colour + depth renderbuffers
    const hl = bloom.highlights;
    b += hl.width * hl.height * 8 * 1.7; // highlights + mip chain (down + up)
    if (passDof.enabled) b += dof.bytes;
    return b;
  }

  return { composer, sceneStats, update, setTier, bytes };
}
