"""Shared layout, palette, terrain function and mesh helpers for the forest-cottage scene.

Units are metres, Z is up. Every build_*.py module imports this file and must not modify it.
"""
import math
import random

import bpy
import bmesh
from mathutils import Euler, Matrix, Vector, noise

ROOT = r"D:\House"

# ---------------------------------------------------------------- layout
HOUSE_POS = Vector((0.0, 0.0))
HOUSE_ROT_Z = math.radians(-8.0)
HOUSE_GROUND = 0.25            # flattened ground height under the house
HOUSE_SIZE = (5.0, 4.2)        # local X (ridge axis) x local Y; front long side faces local -Y

POND_CENTER = Vector((-10.5, 3.5))
POND_RADIUS = 4.2
WATER_Z = -0.15
POND_BED_Z = -0.9

TERRACE_Y = 11.0               # rocky ledge north of the house, upper meadow beyond it
TERRACE_HEIGHT = 2.4

CLEARING_CENTER = Vector((-3.0, 2.0))
CLEARING_RADIUS = 16.0
TERRAIN_HALF = 55.0            # terrain covers [-55, 55] in X and Y

# Main camera looks at the house from this azimuth (degrees, from +X counter-clockwise).
VIEW_AZIMUTH = -60.0


# ---------------------------------------------------------------- palette (sRGB hex)
PALETTE = {
    "grass_light": "#b4c862", "grass_mid": "#86a84a", "grass_dark": "#557e37",
    "leaf_light": "#bfd36e", "leaf_mid": "#7faa52", "leaf_dark": "#3f6e3f", "leaf_teal": "#5c8f6c",
    "trunk": "#6b5240", "trunk_dark": "#4a3a2f",
    "wall_plank": "#6f7449", "timber": "#4b5033",
    "roof": "#7d6a64", "roof_dark": "#5f514d", "roof_light": "#968079",
    "stone": "#6e706b", "stone_light": "#8f8e86", "stone_dark": "#535550",
    "rock": "#a89d87", "rock_dark": "#7f7666", "rock_light": "#c2b8a2",
    "glass": "#3b3653",
    "water_deep": "#1c3a47", "water_shallow": "#3f6b5e",
    "dirt": "#8b7558", "mud": "#5d5040",
    "flower_yellow": "#f2c230", "flower_white": "#f3efe4", "flower_purple": "#8a63c9",
    "flower_pink": "#e08aa0", "lantern_glow": "#ffb35c",
}


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def color(name_or_hex, alpha=1.0):
    """Linear RGBA from a palette name or '#rrggbb'."""
    h = PALETTE.get(name_or_hex, name_or_hex).lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), alpha)


def jitter(col, rng, amount=0.08):
    """Multiply a linear colour by a random brightness factor around 1."""
    f = 1.0 + rng.uniform(-amount, amount)
    return (col[0] * f, col[1] * f, col[2] * f, col[3])


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(4))


def rng(seed):
    return random.Random(seed)


# ---------------------------------------------------------------- math
def smoothstep(e0, e1, x):
    if e0 == e1:
        return 0.0 if x < e0 else 1.0
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def n2(x, y, z=0.0):
    """Deterministic Perlin noise, roughly in [-1, 1]."""
    return noise.noise(Vector((x, y, z)))


# ---------------------------------------------------------------- terrain
def terrace_edge_y(x):
    return TERRACE_Y + 1.8 * math.sin(x / 6.5) + 0.8 * n2(x / 4.0, 3.7)


def pond_radius_at(angle):
    return POND_RADIUS * (1.0 + 0.18 * n2(math.cos(angle) * 1.3, math.sin(angle) * 1.3, 5.1))


def pond_signed_distance(x, y):
    """Distance to the pond's nominal shoreline; negative inside the pond."""
    dx, dy = x - POND_CENTER.x, y - POND_CENTER.y
    return math.hypot(dx, dy) - pond_radius_at(math.atan2(dy, dx))


def clearing_radius_at(angle):
    return CLEARING_RADIUS * (1.0 + 0.2 * n2(math.cos(angle) * 1.1, math.sin(angle) * 1.1, 9.3))


def clearing_signed_distance(x, y):
    """Distance to the forest edge; negative inside the meadow clearing."""
    dx, dy = x - CLEARING_CENTER.x, y - CLEARING_CENTER.y
    return math.hypot(dx, dy) - clearing_radius_at(math.atan2(dy, dx))


def house_distance(x, y):
    return math.hypot(x - HOUSE_POS.x, y - HOUSE_POS.y)


def terrain_height(x, y):
    h = 0.55 * n2(x / 16.0, y / 16.0, 1.3) + 0.18 * n2(x / 5.0, y / 5.0, 7.1)
    e = terrace_edge_y(x)
    h += TERRACE_HEIGHT * smoothstep(e - 1.0, e + 1.0, y)
    h = lerp(h, HOUSE_GROUND, smoothstep(8.0, 4.5, house_distance(x, y)))
    sd = pond_signed_distance(x, y)
    h = lerp(h, max(h, WATER_Z + 0.25), smoothstep(6.0, 1.5, sd))
    h = lerp(h, POND_BED_Z, smoothstep(1.2, -1.8, sd))
    return h


def house_matrix():
    """World matrix of the house's local frame (origin at ground level, centre of footprint)."""
    return (Matrix.Translation((HOUSE_POS.x, HOUSE_POS.y, HOUSE_GROUND))
            @ Matrix.Rotation(HOUSE_ROT_Z, 4, "Z"))


# ---------------------------------------------------------------- scene helpers
def _purge_collection(coll):
    for child in list(coll.children):
        _purge_collection(child)
        bpy.data.collections.remove(child)
    for ob in list(coll.objects):
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is None or data.users > 0:
            continue
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)
        elif isinstance(data, bpy.types.Light):
            bpy.data.lights.remove(data)
        elif isinstance(data, bpy.types.Camera):
            bpy.data.cameras.remove(data)


def collection(name, parent=None, clear=True):
    """Get or create a collection linked under parent (default: scene root); optionally empty it."""
    parent = parent or bpy.context.scene.collection
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in parent.children:
        parent.children.link(coll)
    if clear:
        _purge_collection(coll)
    return coll


def material(name, base=(0.8, 0.8, 0.8, 1.0), roughness=0.85, use_vcol=False, translucency=0.0,
             emission=None, emission_strength=0.0, metallic=0.0, transmission=0.0, ior=1.45,
             specular=0.5):
    """Create or rebuild a Principled material. use_vcol feeds the 'Col' colour attribute into Base Color."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["IOR"].default_value = ior
    bsdf.inputs["Transmission Weight"].default_value = transmission
    bsdf.inputs["Specular IOR Level"].default_value = specular
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = emission
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    surface = bsdf.outputs["BSDF"]
    if use_vcol:
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = "Col"
        vc.location = (-400, 0)
        nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    if translucency > 0.0:
        tr = nt.nodes.new("ShaderNodeBsdfTranslucent")
        tr.location = (0, -250)
        if use_vcol:
            nt.links.new(vc.outputs["Color"], tr.inputs["Color"])
        else:
            tr.inputs["Color"].default_value = base
        mx = nt.nodes.new("ShaderNodeMixShader")
        mx.location = (200, 0)
        mx.inputs["Fac"].default_value = translucency
        nt.links.new(surface, mx.inputs[1])
        nt.links.new(tr.outputs["BSDF"], mx.inputs[2])
        surface = mx.outputs["Shader"]
    nt.links.new(surface, out.inputs["Surface"])
    mat.diffuse_color = base
    return mat


class MeshBuilder:
    """Accumulates many small pieces into one mesh with a per-corner 'Col' colour and material indices."""

    def __init__(self):
        self.bm = bmesh.new()
        self.col = self.bm.loops.layers.float_color.new("Col")

    def paint(self, faces, col, mat=0):
        c = tuple(col) if len(col) == 4 else (col[0], col[1], col[2], 1.0)
        for f in faces:
            f.material_index = mat
            for loop in f.loops:
                loop[self.col] = c

    def box(self, center, size, rot=(0.0, 0.0, 0.0), col=(1, 1, 1, 1), mat=0, matrix=None):
        """Axis-aligned box of full dimensions `size`, rotated by Euler `rot`, then transformed by `matrix`."""
        m = (Matrix.Translation(center) @ Euler(rot, "XYZ").to_matrix().to_4x4()
             @ Matrix.Diagonal((size[0], size[1], size[2], 1.0)))
        if matrix is not None:
            m = matrix @ m
        res = bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)
        faces = {f for v in res["verts"] for f in v.link_faces}
        self.paint(faces, col, mat)
        return res["verts"]

    def geometry(self, verts, faces, matrix=None, col=(1, 1, 1, 1), mat=0, vert_cols=None):
        """Append raw geometry: verts = [(x,y,z)], faces = [(i,j,k,...)]; optional per-vertex colours."""
        bv = [self.bm.verts.new(matrix @ Vector(v) if matrix is not None else Vector(v)) for v in verts]
        out = []
        for f in faces:
            face = self.bm.faces.new([bv[i] for i in f])
            out.append(face)
            if vert_cols is None:
                self.paint([face], col, mat)
            else:
                face.material_index = mat
                for loop, i in zip(face.loops, f):
                    c = vert_cols[i]
                    loop[self.col] = tuple(c) if len(c) == 4 else (c[0], c[1], c[2], 1.0)
        return bv, out

    def to_object(self, name, coll, materials, smooth=False):
        me = bpy.data.meshes.new(name)
        self.bm.normal_update()
        self.bm.to_mesh(me)
        self.bm.free()
        for m in materials:
            me.materials.append(m)
        if smooth:
            me.shade_smooth()
        attr = me.color_attributes.get("Col")
        if attr is not None:
            me.color_attributes.active_color = attr
            me.color_attributes.render_color_index = me.color_attributes.active_color_index
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        return ob


def add_bevel(ob, width=0.015, segments=2, angle_deg=40.0):
    mod = ob.modifiers.new("Bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(angle_deg)
    mod.harden_normals = True
    return mod


def triangle_count(ob):
    deps = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(deps)
    me = ev.to_mesh()
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    ev.to_mesh_clear()
    return tris


# ---------------------------------------------------------------- render helpers
def setup_cycles(samples=64):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.refresh_devices()
        if any(d.type == kind for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == kind
            sc.cycles.device = "GPU"
            break
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.view_settings.view_transform = "AgX"


def look_at(ob, target):
    direction = Vector(target) - ob.location
    ob.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def sun_rotation(azimuth_deg, elevation_deg):
    """Euler for a sun light shining from the given azimuth/elevation."""
    return Euler((0.0, math.radians(90.0 - elevation_deg), math.radians(azimuth_deg)), "XYZ")

