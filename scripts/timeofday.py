"""Time-of-day model (timeofday.json, shared with the web viewer): evaluation, and application to the scene.

    import timeofday
    s = timeofday.state(18.0)          # interpolated keyframe values + sky dome colours + sun azimuth/elevation
    timeofday.apply(18.0)              # sun, moon, sky, sky dome, lamps, haze, bloom, saturation, exposure

apply() expects the objects and node names created by build_lighting (Sun, Moon, LanternLight, World_Sky's
SkyMix / SkyBackground / DomeMix nodes, Comp_Look's Bloom / HazeFactor / HazeColor / NightShift / Tint / Saturation /
SkyExposure nodes) and the house materials M_House_Lantern and M_House_Glass.
"""
import json
import math
import os

import bpy
from mathutils import Vector

import common

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timeofday.json")
LANTERN_DAY_EMISSION = 2.5      # the lantern glass glows faintly even with the lamps off


def load():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


def _mix(a, b, t):
    if isinstance(a, list):
        return [x + (y - x) * t for x, y in zip(a, b)]
    return a + (b - a) * t


def sun_position(hour, model=None):
    """(azimuth, elevation) in degrees; the elevation is negative between sunset and sunrise."""
    p = (model or load())["sunPath"]
    f = ((hour % 24.0) - p["riseHour"]) / (p["setHour"] - p["riseHour"])
    azimuth = p["azimuthRise"] + f * (p["azimuthSet"] - p["azimuthRise"])
    return azimuth, p["maxElevation"] * math.sin(math.pi * f)


def _track(keyframes, h):
    """Keyframe values at hour h: linear in hour between the neighbouring keyframes, wrapping at 24."""
    keys = sorted(keyframes, key=lambda k: k["hour"])
    ring = [dict(keys[-1], hour=keys[-1]["hour"] - 24.0)] + keys + [dict(keys[0], hour=keys[0]["hour"] + 24.0)]
    for k0, k1 in zip(ring[:-1], ring[1:]):
        if k0["hour"] <= h <= k1["hour"]:
            break
    t = (h - k0["hour"]) / max(k1["hour"] - k0["hour"], 1e-6)
    return {key: _mix(k0[key], k1[key], t) for key in k0 if key != "hour"}


def state(hour):
    m = load()
    h = hour % 24.0
    s = _track(m["keyframes"], h)
    dome = _track(m["skyDome"]["keyframes"], h)
    s["domeZenith"], s["domeHorizon"] = dome["zenith"], dome["horizon"]
    s["hour"] = h
    s["sunAzimuth"], s["sunElevation"] = sun_position(h, m)
    s["moon"] = m["moon"]
    s["lamps"] = m["lamps"]
    s["sunAngleDeg"] = m["sunAngleDeg"]
    return s


def lantern_location():
    ob = bpy.data.objects.get("House_LanternGlow")
    if ob is None:
        return None
    corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    return sum(corners, Vector()) / 8.0


def _set_emission(mat_name, color, strength):
    mat = bpy.data.materials.get(mat_name)
    if mat is None or not mat.use_nodes:
        return
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Emission Color"].default_value = (*color[:3], 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength


def apply(hour, scene=None):
    sc = scene or bpy.context.scene
    s = state(hour)
    up = s["sunElevation"] > 0.0
    sun = bpy.data.objects.get("Sun")
    if sun is not None:
        sun.data.energy = s["sunStrength"] if up else 0.0
        sun.data.color = s["sunColor"]
        sun.data.angle = math.radians(s["sunAngleDeg"])
        sun.rotation_euler = common.sun_rotation(s["sunAzimuth"], max(s["sunElevation"], 0.5))
    moon = bpy.data.objects.get("Moon")
    if moon is not None:
        moon.data.energy = s["moonStrength"]
        moon.data.color = s["moon"]["color"]
        moon.data.angle = math.radians(s["moon"]["angleDeg"])
        moon.rotation_euler = common.sun_rotation(s["moon"]["azimuth"], s["moon"]["elevation"])

    world = sc.world
    if world is not None and world.use_nodes:
        nodes = world.node_tree.nodes
        if "SkyMix" in nodes:
            nodes["SkyMix"].inputs["A"].default_value = (*s["skyHorizon"], 1.0)
            nodes["SkyMix"].inputs["B"].default_value = (*s["skyZenith"], 1.0)
        if "SkyBackground" in nodes:
            nodes["SkyBackground"].inputs["Strength"].default_value = s["skyStrength"]
        if "DomeMix" in nodes:
            nodes["DomeMix"].inputs["A"].default_value = (*s["domeHorizon"], 1.0)
            nodes["DomeMix"].inputs["B"].default_value = (*s["domeZenith"], 1.0)

    lamps = s["lamps"]
    _set_emission("M_House_Lantern", lamps["color"],
                  LANTERN_DAY_EMISSION + s["lamp"] * (lamps["lanternEmission"] - LANTERN_DAY_EMISSION))
    _set_emission("M_House_Glass", lamps["color"], s["lamp"] * lamps["windowEmission"])
    point = bpy.data.objects.get("LanternLight")
    if point is not None:
        point.data.energy = s["lamp"] * lamps["pointWatts"]
        point.data.color = lamps["color"]

    ng = sc.compositing_node_group
    if ng is not None:
        n = ng.nodes
        if "Bloom" in n:
            n["Bloom"].inputs["Strength"].default_value = s["bloom"]
        if "HazeFactor" in n:
            n["HazeFactor"].inputs["To Max"].default_value = s["hazeAmount"]
        if "HazeColor" in n:
            n["HazeColor"].inputs["A"].default_value = (*s["hazeColor"], 1.0)
        if "Saturation" in n:
            n["Saturation"].inputs["Saturation"].default_value = s["saturation"]
        if "NightShift" in n:
            n["NightShift"].inputs[1].default_value = s["nightShift"]
        if "Tint" in n:
            n["Tint"].inputs["B"].default_value = (*s["tint"], 1.0)
        if "SkyExposure" in n:
            k = 2.0 ** -s["exposure"]
            n["SkyExposure"].inputs["B"].default_value = (k, k, k, 1.0)
    sc.view_settings.exposure = s["exposure"]
    return s
