// Time-of-day model shared with Blender (scripts/timeofday.json, evaluated by scripts/timeofday.py): the same
// keyframe interpolation (linear in hour, wrapping at 24), sun path, moon and presets, plus the Blender -> three.js
// direction conversion. Pure functions of the model in manifest.timeOfDay.

const mod24 = (h) => ((h % 24) + 24) % 24;

function mix(a, b, t) {
  if (Array.isArray(a)) return a.map((x, i) => x + (b[i] - x) * t);
  return a + (b - a) * t;
}

/** Unit vector toward a light at azimuth/elevation (degrees, Blender convention) in three.js coordinates. */
export function directionFromAngles(azimuthDeg, elevationDeg, out = [0, 0, 0]) {
  const az = (azimuthDeg * Math.PI) / 180;
  const el = (elevationDeg * Math.PI) / 180;
  // Blender (cos el cos az, cos el sin az, sin el) -> three (x, z, -y)
  out[0] = Math.cos(el) * Math.cos(az);
  out[1] = Math.sin(el);
  out[2] = -Math.cos(el) * Math.sin(az);
  return out;
}

export function createTimeOfDay(model) {
  if (!model || !Array.isArray(model.keyframes) || !model.sunPath) throw new Error('manifest.timeOfDay is missing or incomplete');
  const keys = [...model.keyframes].sort((a, b) => a.hour - b.hour);
  const ring = [...keys, { ...keys[0], hour: keys[0].hour + 24 }];

  /** [azimuth, elevation] in degrees; the elevation is negative between sunset and sunrise (timeofday.sun_position). */
  function sunPosition(hour) {
    const p = model.sunPath;
    const f = (mod24(hour) - p.riseHour) / (p.setHour - p.riseHour);
    return [p.azimuthRise + f * (p.azimuthSet - p.azimuthRise), p.maxElevation * Math.sin(Math.PI * f)];
  }

  /** Interpolated keyframe values + sun position, as timeofday.state(hour). */
  function state(hour) {
    const h = mod24(hour);
    let k0 = ring[ring.length - 2];
    let k1 = ring[ring.length - 1];
    for (let i = 0; i < ring.length - 1; i++) {
      if (ring[i].hour <= h && h <= ring[i + 1].hour) {
        k0 = ring[i];
        k1 = ring[i + 1];
        break;
      }
    }
    const t = (h - k0.hour) / Math.max(k1.hour - k0.hour, 1e-6);
    const s = {};
    for (const key of Object.keys(k0)) if (key !== 'hour') s[key] = mix(k0[key], k1[key], t);
    s.hour = h;
    [s.sunAzimuth, s.sunElevation] = sunPosition(h);
    return s;
  }

  return {
    model,
    state,
    presets: model.presets,
    defaultHour: model.defaultHour,
  };
}

/** Signed shortest hour difference b - a on the 24 h circle, in (-12, 12]. */
export function hourDelta(a, b) {
  let d = mod24(b) - mod24(a);
  if (d > 12) d -= 24;
  if (d <= -12) d += 24;
  return d;
}
