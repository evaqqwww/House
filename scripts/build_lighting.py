"""Sun, sky, cameras, render settings and compositing for the forest-cottage scene."""
import math

import bpy
from mathutils import Vector

import common
import build_house
import timeofday

# The default hour of timeofday.json; timeofday.apply(hour) moves every time-dependent setting below.
TOD = timeofday.load()
_DEFAULT = timeofday.state(TOD["defaultHour"])
SUN_AZIMUTH = _DEFAULT["sunAzimuth"]
SUN_ELEVATION = _DEFAULT["sunElevation"]
SUN_STRENGTH = _DEFAULT["sunStrength"]
SUN_COLOR = tuple(_DEFAULT["sunColor"])
SUN_ANGLE = _DEFAULT["sunAngleDeg"]  # degrees; softer shadow edges

SKY_ZENITH = (*_DEFAULT["skyZenith"], 1.0)
SKY_HORIZON = (*_DEFAULT["skyHorizon"], 1.0)
SKY_STRENGTH = _DEFAULT["skyStrength"]

HAZE_COLOR = (*_DEFAULT["hazeColor"], 1.0)
MIST_START = 70.0
MIST_DEPTH = 70.0
HAZE_AMOUNT = _DEFAULT["hazeAmount"]

LOOK = "AgX - Base Contrast"

CAMERAS = {
    # target/focus are offsets from the house ground point. corridor: trees whose canopy comes within this
    # margin of the camera-to-target line are listed in the camera's 'hide_objects' property; export_web turns
    # them into manifest cameras.*.corridor.hide, and the web viewer hides them in that preset.
    "CAM_Overview": dict(target=(-2.5, 2.0, 0.8), azimuth=-60.0, elevation=40.0, distance=62.0, lens=50.0,
                         fstop=0.04, focus=(0.0, 0.0, 2.0), corridor=None),
    "CAM_Closeup": dict(target=(0.0, 0.0, 3.6), azimuth=-62.0, elevation=6.0, distance=28.0, lens=60.0,
                        fstop=0.12, focus=(0.8, -1.0, 2.6), corridor=1.0),
}
FOREST_COLLECTION = "Forest"


def _sun(coll):
    data = bpy.data.lights.new("Sun", "SUN")
    data.energy = SUN_STRENGTH
    data.color = SUN_COLOR
    data.angle = math.radians(SUN_ANGLE)
    sun = bpy.data.objects.new("Sun", data)
    sun.rotation_euler = common.sun_rotation(SUN_AZIMUTH, SUN_ELEVATION)
    coll.objects.link(sun)
    return sun


def _moon(coll):
    data = bpy.data.lights.new("Moon", "SUN")
    data.energy = 0.0
    moon = bpy.data.objects.new("Moon", data)
    coll.objects.link(moon)
    return moon


def _lantern_light(coll):
    """A small point light at the hanging lantern; dark by day, timeofday.apply drives it with the lamps."""
    loc = timeofday.lantern_location()
    if loc is None:
        return None
    data = bpy.data.lights.new("LanternLight", "POINT")
    data.energy = 0.0
    data.shadow_soft_size = TOD["lamps"]["pointRadius"]
    light = bpy.data.objects.new("LanternLight", data)
    light.location = loc - Vector((0.0, 0.0, 0.2))     # just below the glass, which would block it
    coll.objects.link(light)
    return light


def _window_lights(coll):
    """Soft outward spill, separate from the visible glass so the panes keep their colour."""
    house = common.house_matrix()
    windows = [(side, build_house.wall_frame(sd), w)
               for side, sd in build_house.SIDES.items() for w in sd["windows"]]
    # The dormer relies on glass emission; an external area would wash the adjacent roof.
    for index, (side, frame, w) in enumerate(windows):
        width, height = w["a1"] - w["a0"], w["z1"] - w["z0"]
        center_z = (w["z0"] + w["z1"]) / 2
        world = house @ frame
        # Even the tilted source's inner edge clears the sill/shutters, which reach 0.22 m.
        location = world @ Vector(((w["a0"] + w["a1"]) / 2, center_z, 0.38))
        outward = world.to_3x3() @ Vector((0, 0, 1))
        data = bpy.data.lights.new(f"WindowSpill_{side}_{index}", "AREA")
        data.shape = "RECTANGLE"
        data.size, data.size_y = width * 1.6, height * 1.3
        data.energy = 0.0
        light = bpy.data.objects.new(data.name, data)
        light.location = location
        # Power follows the aperture, not the larger emitter: broad, low-luminance spill.
        light["window_spill_area"] = width * height
        # Attic light grazes nearby roofs; keep those pools subtler than the lower walls.
        light["window_spill_multiplier"] = 0.2 if center_z > build_house.WALL_TOP else 1.0
        common.look_at(light, location + outward - Vector((0, 0, 0.2)))
        coll.objects.link(light)


def _world(sc):
    """World_Sky: the sky gradient lights the scene (SkyMix, SkyBackground); camera and reflection (glossy) rays see
    the sky dome colours instead (DomeMix, mixed by t^curve; timeofday.json skyDome)."""
    world = bpy.data.worlds.get("World_Sky") or bpy.data.worlds.new("World_Sky")
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = -0.05
    ramp.inputs["From Max"].default_value = 0.6
    mixc = nt.nodes.new("ShaderNodeMix")
    mixc.name = "SkyMix"
    mixc.data_type = "RGBA"
    mixc.inputs["A"].default_value = SKY_HORIZON
    mixc.inputs["B"].default_value = SKY_ZENITH
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.name = "SkyBackground"
    bg.inputs["Strength"].default_value = SKY_STRENGTH
    curve = nt.nodes.new("ShaderNodeMath")
    curve.operation = "POWER"
    curve.inputs[1].default_value = TOD["skyDome"]["curve"]
    dome = nt.nodes.new("ShaderNodeMix")
    dome.name = "DomeMix"
    dome.data_type = "RGBA"
    dome_bg = nt.nodes.new("ShaderNodeBackground")
    dome_bg.inputs["Strength"].default_value = 1.0
    path = nt.nodes.new("ShaderNodeLightPath")
    seen = nt.nodes.new("ShaderNodeMath")
    seen.operation = "ADD"
    seen.use_clamp = True
    shader = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(coord.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], mixc.inputs["Factor"])
    nt.links.new(mixc.outputs["Result"], bg.inputs["Color"])
    nt.links.new(ramp.outputs["Result"], curve.inputs[0])
    nt.links.new(curve.outputs["Value"], dome.inputs["Factor"])
    nt.links.new(dome.outputs["Result"], dome_bg.inputs["Color"])
    nt.links.new(path.outputs["Is Camera Ray"], seen.inputs[0])
    nt.links.new(path.outputs["Is Glossy Ray"], seen.inputs[1])
    nt.links.new(seen.outputs["Value"], shader.inputs["Fac"])
    nt.links.new(bg.outputs["Background"], shader.inputs[1])
    nt.links.new(dome_bg.outputs["Background"], shader.inputs[2])
    nt.links.new(shader.outputs["Shader"], out.inputs["Surface"])
    world.mist_settings.start = MIST_START
    world.mist_settings.depth = MIST_DEPTH
    world.mist_settings.falloff = "QUADRATIC"


def _camera(coll, name, cfg):
    base = Vector((common.HOUSE_POS.x, common.HOUSE_POS.y, common.HOUSE_GROUND))
    target = base + Vector(cfg["target"])
    az, el = math.radians(cfg["azimuth"]), math.radians(cfg["elevation"])
    data = bpy.data.cameras.new(name)
    data.lens = cfg["lens"]
    data.clip_start = 0.5
    data.clip_end = 400.0
    cam = bpy.data.objects.new(name, data)
    cam.location = target + cfg["distance"] * Vector(
        (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    common.look_at(cam, target)
    coll.objects.link(cam)

    focus = bpy.data.objects.new(name + "_Focus", None)
    focus.location = base + Vector(cfg["focus"])
    focus.empty_display_size = 0.5
    coll.objects.link(focus)
    data.dof.use_dof = True
    data.dof.focus_object = focus
    data.dof.aperture_fstop = cfg["fstop"]
    data.dof.aperture_blades = 7
    if cfg.get("corridor") is not None:
        cam["hide_objects"] = ";".join(_corridor_trees(cam.location, target, cfg["corridor"]))
    return cam


def _corridor_trees(cam_loc, target, margin):
    """Names of trees (not bushes) whose canopy reaches the camera-to-target line between the two points."""
    forest = bpy.data.collections.get(FOREST_COLLECTION)
    if forest is None:
        return []
    a = Vector((cam_loc.x, cam_loc.y))
    seg = Vector((target.x, target.y)) - a
    length2 = seg.length_squared
    names = []
    for ob in forest.all_objects:
        if ob.type != "MESH" or "bush" in ob.name.lower():
            continue
        p = Vector((ob.location.x, ob.location.y))
        t = (p - a).dot(seg) / length2
        if not 0.0 < t < 1.0:
            continue
        radius = 0.5 * max(ob.dimensions.x, ob.dimensions.y)
        if (a + seg * t - p).length < radius + margin:
            names.append(ob.name)
    return names


def _render_settings(sc, samples):
    common.setup_cycles(samples)
    c = sc.cycles
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.02
    c.use_light_tree = True
    c.denoiser = "OPENIMAGEDENOISE"
    c.denoising_use_gpu = True
    c.max_bounces = 8
    c.diffuse_bounces = 4
    c.glossy_bounces = 3
    c.transmission_bounces = 6
    c.transparent_max_bounces = 16
    c.blur_glossy = 1.0
    sc.render.resolution_x, sc.render.resolution_y = 1920, 1080
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True              # the compositor puts the sky back from the Environment pass
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = LOOK
    sc.view_settings.exposure = 0.0
    sc.view_layers[0].use_pass_mist = True
    sc.view_layers[0].use_pass_environment = True


def _compositor(sc):
    """Comp_Look. The film is transparent: Image is the scene alone (premultiplied, alpha = coverage, anti-aliased and
    depth-of-field blurred like the colour) and the Environment pass the sky dome seen past it. The scene takes bloom
    (of the whole frame), haze, night grade, tint, colour balance and saturation; the sky dome keeps the colours
    timeofday.json gives it (SkyExposure = 2^-exposure, set by timeofday.apply, cancels the exposure of the view
    settings); both add up before the vignette."""
    ng = bpy.data.node_groups.get("Comp_Look")
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new("Comp_Look", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    n, ln = ng.nodes, ng.links

    rl = n.new("CompositorNodeRLayers")
    rl.location = (-900, 0)

    def mix(blend, a, b, location, factor=1.0):
        node = n.new("ShaderNodeMix")
        node.location = location
        node.data_type = "RGBA"
        node.blend_type = blend
        node.inputs["Factor"].default_value = factor
        for socket, src in (("A", a), ("B", b)):
            if isinstance(src, tuple):
                node.inputs[socket].default_value = src
            else:
                ln.new(src, node.inputs[socket])
        return node

    env = rl.outputs["Environment"]
    frame = mix("ADD", rl.outputs["Image"], env, (-800, 150))           # scene + sky dome
    glare = n.new("CompositorNodeGlare")
    glare.name = "Bloom"
    glare.location = (-650, 150)
    glare.inputs["Type"].default_value = "Bloom"
    glare.inputs["Threshold"].default_value = 0.9
    glare.inputs["Strength"].default_value = 0.18
    glare.inputs["Size"].default_value = 0.6
    ln.new(frame.outputs["Result"], glare.inputs["Image"])
    bloom = mix("SUBTRACT", glare.outputs["Image"], frame.outputs["Result"], (-500, 150))
    scene = mix("ADD", rl.outputs["Image"], bloom.outputs["Result"], (-500, 0))

    haze_fac = n.new("ShaderNodeMapRange")
    haze_fac.name = "HazeFactor"
    haze_fac.location = (-650, -300)
    haze_fac.inputs["To Max"].default_value = HAZE_AMOUNT
    ln.new(rl.outputs["Mist"], haze_fac.inputs["Value"])

    # the scene is premultiplied: the haze colour is weighted by the coverage
    haze_col = mix("MULTIPLY", HAZE_COLOR, rl.outputs["Alpha"], (-650, -150))
    haze_col.name = "HazeColor"
    haze = n.new("ShaderNodeMix")
    haze.name = "HazeMix"
    haze.location = (-400, 0)
    haze.data_type = "RGBA"
    ln.new(haze_fac.outputs["Result"], haze.inputs["Factor"])
    ln.new(scene.outputs["Result"], haze.inputs["A"])
    ln.new(haze_col.outputs["Result"], haze.inputs["B"])

    # Night tint retains the source chroma and luminance; warm spill stays protected even in shadow.
    night = TOD["nightGrade"]
    rgb = n.new("CompositorNodeSeparateColor")
    rgb.mode = "RGB"
    rgb.location = (-850, -550)
    ln.new(haze.outputs["Result"], rgb.inputs["Image"])

    def scalar(operation, a, b, location):
        node = n.new("ShaderNodeMath")
        node.operation = operation
        node.location = location
        for index, value in enumerate((a, b)):
            if isinstance(value, (int, float)):
                node.inputs[index].default_value = value
            else:
                ln.new(value, node.inputs[index])
        return node.outputs["Value"]

    # Explicit scene-linear Rec.709 luminance matches the web look regardless of colour-management settings.
    def luminance(channels, location):
        x, y = location
        red = scalar("MULTIPLY", channels.outputs["Red"], 0.2126, (x, y))
        green = scalar("MULTIPLY", channels.outputs["Green"], 0.7152, (x, y - 150))
        blue = scalar("MULTIPLY", channels.outputs["Blue"], 0.0722, (x, y - 300))
        rg = scalar("ADD", red, green, (x + 150, y))
        return scalar("ADD", rg, blue, (x + 300, y))

    lum = luminance(rgb, (-700, -450))
    dark = n.new("ShaderNodeMapRange")
    dark.name = "NightDarkRange"
    dark.location = (-250, -450)
    dark.interpolation_type = "SMOOTHSTEP"
    dark.inputs["From Min"].default_value = night["low"]
    dark.inputs["From Max"].default_value = night["high"]
    dark.inputs["To Min"].default_value = 1.0
    dark.inputs["To Max"].default_value = 0.0
    ln.new(lum, dark.inputs["Value"])
    cool_channel = scalar("MAXIMUM", rgb.outputs["Green"], rgb.outputs["Blue"], (-700, -900))
    difference = scalar("SUBTRACT", rgb.outputs["Red"], cool_channel, (-550, -800))
    safe_lum = scalar("MAXIMUM", lum, 1e-5, (-400, -650))
    warmth = scalar("DIVIDE", difference, safe_lum, (-250, -650))
    cool = n.new("ShaderNodeMapRange")
    cool.name = "NightWarmthRange"
    cool.location = (-100, -650)
    cool.interpolation_type = "SMOOTHSTEP"
    cool.inputs["From Min"].default_value = night["warmth"][0]
    cool.inputs["From Max"].default_value = night["warmth"][1]
    cool.inputs["To Min"].default_value = 1.0
    cool.inputs["To Max"].default_value = 0.0
    ln.new(warmth, cool.inputs["Value"])
    protected = scalar("MULTIPLY", dark.outputs["Result"], cool.outputs["Result"], (50, -550))
    shift = n.new("ShaderNodeMath")
    shift.name = "NightShift"
    shift.location = (200, -450)
    shift.operation = "MULTIPLY"
    shift.inputs[1].default_value = 0.0
    ln.new(protected, shift.inputs[0])
    scot = n.new("ShaderNodeMix")
    scot.name = "NightColor"
    scot.location = (-250, -250)
    scot.data_type = "RGBA"
    scot.blend_type = "MULTIPLY"
    scot.inputs["Factor"].default_value = 1.0
    scot.inputs["B"].default_value = (*night["color"], 1.0)
    ln.new(haze.outputs["Result"], scot.inputs["A"])
    night_rgb = n.new("CompositorNodeSeparateColor")
    night_rgb.mode = "RGB"
    night_rgb.location = (-850, -1100)
    ln.new(scot.outputs["Result"], night_rgb.inputs["Image"])
    night_lum = luminance(night_rgb, (-700, -1100))
    safe_night_lum = scalar("MAXIMUM", night_lum, 1e-5, (-250, -1250))
    night_scale = scalar("DIVIDE", lum, safe_night_lum, (-100, -1100))
    normalized = mix("MULTIPLY", scot.outputs["Result"], night_scale, (50, -1000))
    normalized.name = "NightNormalized"
    graded = n.new("ShaderNodeMix")
    graded.location = (-100, -150)
    graded.data_type = "RGBA"
    ln.new(shift.outputs["Value"], graded.inputs["Factor"])
    ln.new(haze.outputs["Result"], graded.inputs["A"])
    ln.new(normalized.outputs["Result"], graded.inputs["B"])

    tint = n.new("ShaderNodeMix")
    tint.name = "Tint"
    tint.location = (50, -150)
    tint.data_type = "RGBA"
    tint.blend_type = "MULTIPLY"
    tint.inputs["Factor"].default_value = 1.0
    tint.inputs["B"].default_value = (1.0, 1.0, 1.0, 1.0)
    ln.new(graded.outputs["Result"], tint.inputs["A"])

    grade = n.new("CompositorNodeColorBalance")
    grade.location = (-150, 0)
    ln.new(tint.outputs["Result"], grade.inputs["Image"])

    sat = n.new("CompositorNodeHueSat")
    sat.name = "Saturation"
    sat.location = (100, 0)
    sat.inputs["Saturation"].default_value = 0.98
    ln.new(grade.outputs["Image"], sat.inputs["Image"])

    mask = n.new("CompositorNodeEllipseMask")
    mask.location = (100, -300)
    mask.inputs["Size"].default_value = (0.95, 0.95)
    soft = n.new("CompositorNodeBlur")
    soft.location = (300, -300)
    soft.inputs["Size"].default_value = (300, 300)
    ln.new(mask.outputs["Mask"], soft.inputs["Image"])
    vig = n.new("ShaderNodeMix")
    vig.location = (500, 0)
    vig.data_type = "RGBA"
    vig.blend_type = "MULTIPLY"
    vig.inputs["Factor"].default_value = 0.22
    vig.inputs["B"].default_value = (0.0, 0.0, 0.0, 1.0)
    inv = n.new("ShaderNodeMath")
    inv.location = (300, -150)
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    ln.new(soft.outputs["Image"], inv.inputs[1])
    vfac = n.new("ShaderNodeMath")
    vfac.location = (400, -150)
    vfac.operation = "MULTIPLY"
    vfac.inputs[1].default_value = 0.22
    ln.new(inv.outputs["Value"], vfac.inputs[0])
    ln.new(vfac.outputs["Value"], vig.inputs["Factor"])

    sky = mix("MULTIPLY", env, (1.0, 1.0, 1.0, 1.0), (250, 250))
    sky.name = "SkyExposure"
    both = mix("ADD", sat.outputs["Image"], sky.outputs["Result"], (400, 0))
    ln.new(both.outputs["Result"], vig.inputs["A"])

    opaque = n.new("CompositorNodeSetAlpha")
    opaque.location = (650, 0)
    opaque.inputs["Type"].default_value = "Replace Alpha"
    opaque.inputs["Alpha"].default_value = 1.0
    ln.new(vig.outputs["Result"], opaque.inputs["Image"])
    out = n.new("NodeGroupOutput")
    out.location = (850, 0)
    ln.new(opaque.outputs["Image"], out.inputs[0])
    sc.compositing_node_group = ng


def build(samples=256):
    sc = bpy.context.scene
    coll = common.collection("Lighting")
    _sun(coll)
    _moon(coll)
    _lantern_light(coll)
    _window_lights(coll)
    _world(sc)
    cams = {name: _camera(coll, name, cfg) for name, cfg in CAMERAS.items()}
    sc.camera = cams["CAM_Overview"]
    _render_settings(sc, samples)
    _compositor(sc)
    s = timeofday.apply(TOD["defaultHour"], sc)
    return {"cameras": list(cams), "hour": s["hour"], "sun": (s["sunAzimuth"], s["sunElevation"], s["sunStrength"])}
