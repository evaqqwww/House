// Time-of-day lighting hub: evaluates the shared model for an hour and drives the uniforms every material shares
// (same uniform objects, so one update reaches all of them).
//
// Light units L (radiance = albedo * L, linear):
//   direct   sunStrength * sunColor / PI * max(N.Lsun, 0) * shadow  (+ the same for the moon)
//   sky      skyRad(t) * S          skyRad = skyStrength * mix(skyHorizon, skyZenith, Z), Z = the surface's zenith
//                                   fraction (baked with S)
//   bounce   bounce(t) * B          bounce = (sunStrength * sunColor * max(sin el, 0) + moonStrength * moonColor * sin el_moon)
//                                            / sin(el_default): the light a flat ground gets, relative to the bake's
//   lamps    lamp(t) * lampColor * Lp
// The shadow map belongs to the stronger of sun and moon (the key light); the other one is unshadowed.
import { Color, Matrix4, Vector2, Vector3, Vector4 } from 'three';
import { createTimeOfDay, directionFromAngles } from './timeofday.js';

const LUMA = [0.2126, 0.7152, 0.0722];
const lum = (c) => LUMA[0] * c[0] + LUMA[1] * c[1] + LUMA[2] * c[2];
const LANTERN_DAY_EMISSION = 2.5; // timeofday.py: the lantern glass glows faintly with the lamps off
const SKY_RANGE = [-0.05, 0.6]; // World_Sky: horizon -> zenith over the view direction's height

// Constants of the real-time approximations that have no Blender counterpart (fitted to the Cycles renders).
const DEFAULTS = {
  skyMixUp: 0.89, // cosine-weighted mix seen by an up-facing surface under the open gradient sky
  groundAlbedo: [0.2, 0.3, 0.085], // meadow seen from above, for the ground half of the tree hemisphere light
  groundSun: 0.6, // fraction of the ground under the trees in direct light
  moonGain: 0.9, // direct moonlight relative to moonStrength / PI (Cycles night renders)
};

export function createLighting(manifest) {
  const tod = createTimeOfDay(manifest.timeOfDay);
  const model = tod.model;
  const lamps = model.lamps;
  const moon = model.moon;
  const sinDefault = Math.sin((manifest.sunDefault.elevation * Math.PI) / 180); // the sun the bounce layer is baked with
  const moonDir = new Vector3().fromArray(directionFromAngles(moon.azimuth, moon.elevation));

  const U = {
    uTime: { value: 0 },
    uSunDir: { value: new Vector3(0, 1, 0) },
    uSunRad: { value: new Color(0, 0, 0) },
    uMoonDir: { value: moonDir.clone() },
    uMoonRad: { value: new Color(0, 0, 0) },
    uKeyShadow: { value: new Vector2(1, 0) }, // (sun, moon): 1 = this light uses the shadow map
    uSkyHorizonRad: { value: new Color() },
    uSkyZenithRad: { value: new Color() },
    uSkyUp: { value: new Color() },
    uGroundRad: { value: new Color() },
    uBounceRad: { value: new Color() },
    uLampRad: { value: new Color() },
    uLampPos: { value: new Vector3(3.3, 1.9, 2.0) },
    uLampPoint: { value: new Color() },
    uLayerScale: { value: new Vector3(manifest.skyScale, manifest.bounceScale, manifest.lampScale) },
    uSkyZenith: { value: new Color() },
    uSkyHorizon: { value: new Color() },
    uSkyCurve: { value: model.skyDome.curve }, // dome gradient: horizon -> zenith by t^curve
    uSkyRange: { value: new Vector2().fromArray(SKY_RANGE) },
    uNight: { value: 0 },
    uFireflies: { value: 0 },
    uShadowMap: { value: null },
    uShadowMatrix: { value: new Matrix4() },
    uShadowParams: { value: new Vector4(1 / 2048, 1, 0.0005, 0.05) }, // texel (uv), kernel (texels), depth bias, normal offset (m)
  };
  // emission of the house glass (window panes) and the lantern glass, by material name
  const emission = {
    M_House_Glass: { value: new Color() },
    M_House_Lantern: { value: new Color() },
  };

  const state = {
    hour: NaN,
    s: null,
    key: 'sun',
    keyDir: new Vector3(0, 1, 0),
  };
  const listeners = new Set();
  const tmp = [0, 0, 0];

  function setHour(hour) {
    const s = tod.state(hour);
    const up = s.sunElevation > 0;
    // Blender aims the sun at max(elevation, 0.5 deg) and switches it off below the horizon
    const sunDir = U.uSunDir.value.fromArray(directionFromAngles(s.sunAzimuth, Math.max(s.sunElevation, 0.5), tmp));
    const sunStrength = up ? s.sunStrength : 0;
    U.uSunRad.value.fromArray(s.sunColor).multiplyScalar(sunStrength / Math.PI);
    U.uMoonRad.value.fromArray(moon.color).multiplyScalar((s.moonStrength * DEFAULTS.moonGain) / Math.PI);

    // key light: the one that lights flat ground more
    const sunFlat = sunStrength * lum(s.sunColor) * Math.max(sunDir.y, 0);
    const moonFlat = s.moonStrength * lum(moon.color) * Math.max(moonDir.y, 0);
    state.key = sunFlat >= moonFlat ? 'sun' : 'moon';
    U.uKeyShadow.value.set(state.key === 'sun' ? 1 : 0, state.key === 'moon' ? 1 : 0);
    state.keyDir.copy(state.key === 'sun' ? sunDir : moonDir);

    const mixc = (a, b, t) => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * t);
    U.uSkyUp.value.fromArray(mixc(s.skyHorizon, s.skyZenith, DEFAULTS.skyMixUp)).multiplyScalar(s.skyStrength);
    U.uSkyHorizonRad.value.fromArray(s.skyHorizon).multiplyScalar(s.skyStrength);
    U.uSkyZenithRad.value.fromArray(s.skyZenith).multiplyScalar(s.skyStrength);
    // the visible sky (dome, water reflections) has colours of its own (timeofday.json skyDome)
    U.uSkyZenith.value.fromArray(s.domeZenith);
    U.uSkyHorizon.value.fromArray(s.domeHorizon);

    const sinEl = Math.max(Math.sin((s.sunElevation * Math.PI) / 180), 0);
    const b = U.uBounceRad.value;
    b.fromArray(s.sunColor).multiplyScalar((sunStrength * sinEl) / sinDefault);
    const moonBounce = (s.moonStrength * Math.max(moonDir.y, 0)) / sinDefault;
    b.r += moonBounce * moon.color[0];
    b.g += moonBounce * moon.color[1];
    b.b += moonBounce * moon.color[2];

    // ground light seen from below (tree hemisphere): meadow albedo * (direct on flat ground + sky)
    const ga = DEFAULTS.groundAlbedo;
    const flat = new Color().copy(U.uSunRad.value).multiplyScalar(Math.max(sunDir.y, 0));
    flat.add(new Color().copy(U.uMoonRad.value).multiplyScalar(Math.max(moonDir.y, 0)));
    const g = U.uGroundRad.value;
    g.copy(flat).multiplyScalar(DEFAULTS.groundSun).add(U.uSkyUp.value);
    g.r *= ga[0];
    g.g *= ga[1];
    g.b *= ga[2];

    U.uLampRad.value.fromArray(lamps.color).multiplyScalar(s.lamp);
    // Cycles point light of P watts: radiance of a white diffuse surface = P / (4 PI^2 d^2) * cos
    U.uLampPoint.value.fromArray(lamps.color).multiplyScalar((s.lamp * lamps.pointWatts) / (4 * Math.PI * Math.PI));
    emission.M_House_Glass.value.fromArray(lamps.color).multiplyScalar(s.lamp * lamps.windowEmission);
    emission.M_House_Lantern.value
      .fromArray(lamps.color)
      .multiplyScalar(LANTERN_DAY_EMISSION + s.lamp * (lamps.lanternEmission - LANTERN_DAY_EMISSION));

    const el = s.sunElevation;
    U.uNight.value = Math.min(Math.max((-el - 3) / 12, 0), 1);
    U.uFireflies.value = s.fireflies;

    state.hour = s.hour;
    state.s = s;
    for (const fn of listeners) fn(state);
    return state;
  }

  function setLampPosition(v) {
    U.uLampPos.value.copy(v);
  }

  return {
    tod,
    U,
    emission,
    state,
    setHour,
    setLampPosition,
    onChange(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    get hour() {
      return state.hour;
    },
  };
}
