"""Bakes the forest-cottage light layers in Cycles and exports the mobile web assets.

Background Blender only. Works on the opened copy in memory and never saves the .blend:
    blender -b D:/House/house.blend -P D:/House/scripts/export_web.py -- [--samples N] [--quick] [--out DIR]

  --samples N   samples of every bake (default 1024; adaptive sampling is off for bakes)
  --quick       development set: 32 samples, wider blur
  --out DIR     output folder, created if missing (default D:/House/web/public/assets)

Light layers (light units L: radiance = albedo * L, all linear), combined at run time with the time of day
(timeofday.json):
  radiance = albedo * (sun + moon [real time, shadow map]
                       + skyRad(t) * S + bounce(t) * B + lamp(t) * lampColor * Lp) + emission(t)
  S   DIFFUSE light (direct + indirect) under a uniform white sky of radiance 1, sun / moon / lamps off
      (1 on open flat ground, less where occluded, interreflections included)
  B   DIFFUSE indirect light of the sun at the default-hour direction, strength 1, white; sky and lamps off
  Lp  DIFFUSE light (direct + indirect) of the lamp emitters at lamp = 1 (lantern glass, window glass and the
      LanternLight point and soft window spill areas), sky / sun / moon off, stored as luminance(L) / luminance(lampColor)
  Z   zenith fraction: luminance of the light under a grey sky shaped like the zenith weight of the sky gradient
      over luminance(S); skyRad = skyStrength * mix(skyHorizon, skyZenith, Z) per surface (up-facing open ground
      sees mostly zenith colour, walls and rock faces mostly horizon colour)
Every bake leaves out the grass and the smoke; the terrain bakes also leave out the meadow flowers.

Output (manifest.version 2):
  scene.glb          GLB, +Y up, EXT_meshopt_compression + KHR_mesh_quantization after
                     web/scripts/optimize-glb.mjs, cameras CAM_Overview / CAM_Closeup with extras. extras.shade:
                       lit      house, rocks, meadow flowers, lily pads, reeds. COLOR_0 = albedo,
                                COLOR_1 = sqrt(S / skyScale) rgb + sqrt(Lp / lampScale) a,
                                COLOR_2 = sqrt(B / bounceScale) rgb + Z a (the optimizer renames the exporter's
                                _SKY / _BOUNCE attributes)
                       terrain  COLOR_0 = albedo, TEXCOORD_0 = planar UV
                       treeLOD  <Variant>_L0/_L1/_L2 at the origin, primitives M_Bark / M_Leaves, COLOR_0 = albedo,
                                canopy normals; placed from manifest.trees.instances
                       water    PondWater, COLOR_0 = depth colour + alpha
                       smoke    House_Smoke
  terrain_sky.png    1024^2 RGB data: sqrt(S / skyScale)
  terrain_bounce.png 512^2 RGB data: sqrt(B / bounceScale)
  terrain_lamp.png   512^2 grey data: sqrt(Lp / lampScale)
  terrain_zenith.png 512^2 grey data: Z
  ground_albedo.png  512^2 sRGB: the ground colour under the grass
  ground_cover.png   512^2 grey data: grass surface coverage before canopy thinning
  grass.png          512^2 RGB data: R = grass density, G/B = terrain height (16 bit, big endian)
  manifest.json      layer scales, map half size, grass, trees (instances, LOD distances), time of day, default sun
                     elevation, lantern light position, haze, camera targets and close-up corridor, file sizes
All maps share the planar mapping u = (x + 55) / 110, v = (z + 55) / 110 (three.js x / z); the first PNG row is
v = 0 (flipY = false).

Mobile reduction: bevels with one segment, long edges split only to the vertex spacing the smooth layers need,
rocks decimated, House_Foliage split into thin leaves (half of them kept, enlarged) and solid parts (shrub lumps
decimated, blossoms as octahedra), meadow flowers rebuilt from light prototypes (petals as single triangles,
stems and the leaves hidden in the grass dropped), a coarser terrain (fine in the clearing, coarse in the
forest), objects merged per material, trees as LOD meshes from build_trees.build_lod_meshes.

Vertex bakes sample only at corners. Faces whose corners are all hidden but that are visible at the centre or
halfway from it to a corner get a vertex at the most open of these points and are re-baked. Thin single-sided
parts (THIN) get their back side baked too and become two-sided. Corners that got no sky light or sit inside other
closed geometry take the light of the valid corners of their face or vertex, slightly darkened, or of a valid
vertex nearby, strongly darkened; faces with all corners hidden that lie inside a single closed piece are deleted
(faces covered by several pieces stay: the joints between the pieces may show them).
"""
import contextlib
import json
import math
import os
import struct
import subprocess
import sys
import time
import zlib

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

sys.dont_write_bytecode = True
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import common  # noqa: E402
import build_lighting  # noqa: E402
import build_terrain  # noqa: E402
import build_trees  # noqa: E402
import timeofday  # noqa: E402

WEB_DIR = os.path.join(common.ROOT, "web")
OUT_DIR = os.path.join(WEB_DIR, "public", "assets")
SAMPLES = 1024                 # every bake; the scene's adaptive sampling would stop bakes early
QUICK_SAMPLES = 32
HALF = common.TERRAIN_HALF     # terrain and maps cover [-HALF, HALF]^2
RES = {"S": 1024, "B": 512, "Lp": 512, "Z": 512}
ALBEDO_RES = 512
GRASS_RES = 512
BLUR = {"S": 1.2, "B": 1.5, "Lp": 0.7, "Z": 1.5}     # Gaussian sigma (texels) against bake noise; --quick doubles it
PERCENTILE = 99.9
HEADROOM = 1.1                 # scale = HEADROOM * percentile
LAMP_PERCENTILE = 99.5         # of the lamp-lit values (Lp > LAMP_FLOOR)
LAMP_FLOOR = 1e-3
LUMA = np.array([0.2126, 0.7152, 0.0722])

HIDDEN_DARKEN = 0.8            # hidden corners = valid corners of the face * this (a hint of contact shade)
INSIDE_RAY = 5.0               # max ray length per step of the inside test (m)
MARCH_HITS = 8                 # surfaces followed along each ray of the inside test
DARK_SKY = 0.02                # a corner with less sky light (S) is no visible sample: it sits in a closed cavity
CAVITY_RADIUS = 1.0            # hidden corners with no valid neighbour borrow light from a valid vertex this close
CAVITY_DARKEN = 0.35           # ... darkened this much: faces seen only through gaps are dim, not black
POKE_MIN_AREA = 0.01          # hidden faces smaller than this (m2) are not worth an extra vertex
OPEN_RADIUS = 0.3              # reach of the openness test that picks where a hidden face gets its vertex (m)

# mobile reduction
BEVEL_SEGMENTS = 1
# long edges are split to this length before the bevel is applied (S / B / Lp vary slowly; corners of planks and
# shingles inside their neighbours need visible neighbours)
SUBDIVIDE = {"House_Foundation": 0.6, "House_Chimney": 0.6, "House_Timber": 0.8, "House_Planks": 0.8,
             "House_Props": 0.6, "House_Glass": 0.25}
DECIMATE = {"Rocks_Cliff": 0.62, "Rocks_Boulders": 0.75, "Rocks_Stones": 0.5}   # collapse ratio after subdivision
FOLIAGE_KEEP = 0.5             # share of the house leaves kept (enlarged by 1 / sqrt(keep))
FOLIAGE_LUMP_RATIO = 0.5       # decimation of the shrub lumps
BLOSSOM_KEEP = (0.6, 1.2)      # share of the blossoms on the shrubs kept, and their enlargement
WELD_NORMAL = 0.02             # corners of a vertex whose normals differ less than this share one light value
TERRAIN_GRID = dict(fine=0.45, coarse=2.6, ramp=10.0, core_x=(-27.0, 21.0), core_y=(-20.0, 24.0))
# merged after baking: (export name, members); the rest keeps its name
MERGE = (("House_Wood", ("House_Timber", "House_Planks", "House_Props")),
         ("House_Stone", ("House_Foundation", "House_Chimney")),
         ("House_Plants", ("House_Foliage", "House_Shrubs")),
         ("Rocks", ("Rocks_Cliff", "Rocks_Boulders", "Rocks_Stones")))
FLOWERS_EXPORT = "MeadowFlowers"

MESHOPT_EXP_BITS = 16          # exporter default 12 bits gives ~2.7 cm steps at |x| = 55 m
MESHOPT_QUAT_BITS = 12

FOREST = "Forest"
TERRAIN = "Terrain"
TERRAIN_BAKE = "Terrain_Bake"  # the full-resolution terrain, used for baking only
WATER = "PondWater"
SMOKE = "House_Smoke"
FLOWERS_BAKED = "Flowers_Baked"
SHRUBS = "House_Shrubs"
INSTANCERS = ("Grass", "Flowers")
GROUND_COVER_MESH = "GroundCoverBase"
CAMERAS = ("CAM_Overview", "CAM_Closeup")
LIT_EXTRA = ("LilyPads", "Reeds")
# single-sided thin parts seen from both sides (leaves, petals, reed blades): both sides are baked and every
# face gets a back-facing twin
THIN = ("House_Foliage", FLOWERS_BAKED, "Reeds")
# closed solids used for the inside test of hidden corners (plus the terrain from below)
OCCLUDER_SKIP = ("House_Foliage", SHRUBS, "House_Glass", "House_LanternGlow")
GLOW = ("House_Glass", "House_LanternGlow")    # emitters: fix_hidden_corners keeps their hidden-looking faces

LIGHT_PASSES = {"DIRECT", "INDIRECT"}
LAYERS = {"S": ("sky", LIGHT_PASSES), "B": ("bounce", {"INDIRECT"}), "Lp": ("lamp", LIGHT_PASSES),
          "Z": ("zenith", LIGHT_PASSES)}

# Mirrors build_terrain._grass_material
GRASS_ROOT_MIX = 0.35
GRASS_TIP_MIX = 0.38
GRASS_VALUE = (0.84, 0.94)
GRASS_SATURATION = (0.86, 0.96)
GRASS_TRANSLUCENT = ("#d6e27a", 0.15)
CANOPY_GRASS = (0.95, 0.55)    # grass density fades to 0 from this fraction of a canopy radius to this one
LOD_TREE_BUDGET = 200000       # tree triangles per camera preset the suggested LOD distances keep under
C3 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])   # Blender -> three.js
LF = chr(10)                   # manifest.json line ending on every platform


# ---------------------------------------------------------------- small helpers
def _args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"samples": None, "out": OUT_DIR, "quick": False}
    i = 0
    while i < len(argv):
        if argv[i] == "--samples":
            opts["samples"] = int(argv[i + 1])
            i += 2
        elif argv[i] == "--out":
            opts["out"] = argv[i + 1]
            i += 2
        elif argv[i] == "--quick":
            opts["quick"] = True
            i += 1
        else:
            raise SystemExit(f"export_web.py: unknown argument {argv[i]!r}")
    if opts["samples"] is None:
        opts["samples"] = QUICK_SAMPLES if opts["quick"] else SAMPLES
    return opts


def to_three(v):
    """Blender (Z up) -> three.js (Y up): (x, z, -y)."""
    return [float(v[0]), float(v[2]), -float(v[1])]


def _r(v, n=5):
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_r(x, n) for x in v]
    return round(float(v), n)


def _log(stage, t0, **info):
    extra = " ".join(f"{k}={v}" for k, v in info.items())
    print(f"[export_web] {stage}: {time.time() - t0:.1f}s {extra}", flush=True)


def linear_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def sqrt_encode(x, scale):
    """8-bit sqrt encoding of a non-negative linear value: c = sqrt(x / scale), decoded as c^2 * scale."""
    return np.round(np.sqrt(np.clip(x / scale, 0.0, 1.0)) * 255.0).astype(np.uint8)


def _png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path, img8, srgb=False):
    """8-bit grey (h, w) or RGB (h, w, 3) PNG from a uint8 array, rows top-down, per-row adaptive filtering.
    Data maps carry no colour chunk; srgb=True adds the sRGB chunk."""
    if img8.ndim == 2:
        img8 = img8[..., None]
    h, w, c = img8.shape
    x = img8.reshape(h, w * c).astype(np.int16)
    a = np.zeros_like(x)
    a[:, c:] = x[:, :-c]
    b = np.zeros_like(x)
    b[1:] = x[:-1]
    d = np.zeros_like(x)
    d[1:, c:] = x[:-1, :-c]
    p = a + b - d
    pa, pb, pc = np.abs(p - a), np.abs(p - b), np.abs(p - d)
    paeth = np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, d))
    cands = np.stack([x, x - a, x - b, x - ((a + b) >> 1), x - paeth]) % 256
    cost = np.minimum(cands, 256 - cands).sum(axis=2)
    best = cost.argmin(axis=0)
    rows = cands[best, np.arange(h)].astype(np.uint8)
    raw = np.concatenate([best.astype(np.uint8)[:, None], rows], axis=1).tobytes()
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2 if c == 3 else 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_png_chunk(b"IHDR", ihdr))
        if srgb:
            f.write(_png_chunk(b"sRGB", b"\x00"))
        f.write(_png_chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(_png_chunk(b"IEND", b""))


def _select(objs):
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    for ob in objs:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def _color_attr(me, name, domain="CORNER"):
    attr = me.color_attributes.get(name) or me.color_attributes.new(name, "FLOAT_COLOR", domain)
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = me.color_attributes.active_color_index
    return attr


def _read_colors(me, name):
    attr = me.color_attributes[name]
    out = np.empty(len(attr.data) * 4, np.float32)
    attr.data.foreach_get("color", out)
    return out.reshape(-1, 4)


def _write_colors(me, name, cols):
    me.color_attributes[name].data.foreach_set("color", np.ascontiguousarray(cols, np.float32).ravel())


def _local_coords(me):
    co = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def _world_coords(ob):
    co = _local_coords(ob.data)
    m = np.array(ob.matrix_world)
    return co @ m[:3, :3].T + m[:3, 3]


def _topology(me):
    """(loop_start, loop_total, corner vertex indices, face of every corner)."""
    lt = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_total", lt)
    ls = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_start", ls)
    cv = np.empty(len(me.loops), np.int64)
    me.loops.foreach_get("vertex_index", cv)
    return ls, lt, cv, np.repeat(np.arange(len(lt)), lt)


def _tris(me):
    return int(sum(len(p.vertices) - 2 for p in me.polygons))


def _face_islands(me):
    """Island index per face (faces sharing a vertex belong to the same island)."""
    ls, lt, cv, poly_of = _topology(me)
    lab = np.arange(len(me.vertices))
    while True:
        fmin = np.full(len(lt), len(lab))
        np.minimum.at(fmin, poly_of, lab[cv])
        new = lab.copy()
        np.minimum.at(new, cv, fmin[poly_of])
        if np.array_equal(new, lab):
            break
        lab = new
    _, island = np.unique(lab[cv[ls]], return_inverse=True)
    return island


def _mesh_from_arrays(name, verts, faces, corner_cols, mats=None, materials=()):
    """Mesh with a 'Col' corner colour (one RGBA per corner, faces in order), flat shaded."""
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(verts, float).tolist(), [], [tuple(int(i) for i in f) for f in faces])
    me.update()
    for m in materials:
        me.materials.append(m)
    if mats is not None:
        me.polygons.foreach_set("material_index", np.asarray(mats, np.int32))
    me.shade_flat()
    attr = _color_attr(me, "Col")
    attr.data.foreach_set("color", np.asarray(corner_cols, np.float32).ravel())
    return me


def _grid_sample(xs, ys, F, x, y):
    """Bilinear sample of F (ny, nx, ...) given on the rectilinear grid xs, ys at points (x, y)."""
    i = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
    j = np.clip(np.searchsorted(ys, y) - 1, 0, len(ys) - 2)
    fx = np.clip((x - xs[i]) / (xs[i + 1] - xs[i]), 0.0, 1.0)
    fy = np.clip((y - ys[j]) / (ys[j + 1] - ys[j]), 0.0, 1.0)
    if F.ndim == 3:
        fx, fy = fx[:, None], fy[:, None]
    return (F[j, i] * (1 - fx) * (1 - fy) + F[j, i + 1] * fx * (1 - fy)
            + F[j + 1, i] * (1 - fx) * fy + F[j + 1, i + 1] * fx * fy)


def _map_xy(res):
    """Blender world (x, y) of every texel centre of a res^2 map, rows top-down in PNG order (row 0 = three.js
    z = -HALF = Blender y = +HALF)."""
    t = (np.arange(res) + 0.5) / res * 2.0 * HALF - HALF
    X, Z = np.meshgrid(t, t)
    return X, -Z


def gaussian_blur(img, sigma):
    """Separable Gaussian blur of an (h, w, c) array, mirrored edges."""
    if sigma <= 0.0:
        return img
    r = int(math.ceil(3.0 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = img.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="reflect")
        n = out.shape[axis]
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None)] * out.ndim
            sl[axis] = slice(i, i + n)
            acc += np.float32(w) * p[tuple(sl)]
        out = acc
    return out.astype(np.float64)


def _downsample(img, f):
    h, w = img.shape[:2]
    return img.reshape(h // f, f, w // f, f, -1).mean(axis=(1, 3))


# ---------------------------------------------------------------- bake lighting setups
def sky_gradient_range(world):
    """(From Min, From Max) of the sky gradient's Map Range in the scene world (build_lighting._world)."""
    rng = next((n for n in world.node_tree.nodes if n.bl_idname == "ShaderNodeMapRange"), None) \
        if world is not None and world.node_tree else None
    if rng is None:
        return -0.05, 0.6
    return float(rng.inputs["From Min"].default_value), float(rng.inputs["From Max"].default_value)


class BakeLights:
    """Switches the scene between the bake setups ('sky', 'bounce', 'lamp', 'zenith') and restores it.
    The smoke is hidden in all of them (it is an emitter)."""

    def __init__(self, sc, state):
        self.sc = sc
        self.s = state
        self.sun = bpy.data.objects.get("Sun")
        self.moon = bpy.data.objects.get("Moon")
        self.local_lights = timeofday.local_lights()
        self.smoke = bpy.data.objects.get(SMOKE)
        self.world = sc.world
        self.white = self._flat_world("World_BakeWhite", 1.0)
        self.black = self._flat_world("World_BakeBlack", 0.0)
        self.zenith = self._zenith_world("World_BakeZenith")
        self.saved = {
            "sun": (self.sun.data.energy, tuple(self.sun.data.color), self.sun.rotation_euler.copy(),
                    self.sun.data.angle),
            "moon": self.moon.data.energy if self.moon else None,
            "local_lights": [(light, light.data.energy, tuple(light.data.color),
                              getattr(light.data, "shadow_soft_size", None))
                             for light in self.local_lights],
            "smoke": self.smoke.hide_render if self.smoke else None,
            "emission": {n: self._emission(n) for n in ("M_House_Lantern", "M_House_Glass")},
        }

    @staticmethod
    def _flat_world(name, strength):
        w = bpy.data.worlds.new(name)
        w.use_nodes = True
        bg = next(n for n in w.node_tree.nodes if n.type == "BACKGROUND")
        bg.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        bg.inputs["Strength"].default_value = strength
        return w

    def _zenith_world(self, name):
        """Grey sky of radiance = the zenith weight of the scene's sky gradient, clamp((dir.z - lo) / (hi - lo)),
        so S under it over S under the white sky is the share of zenith colour a surface sees."""
        lo, hi = sky_gradient_range(self.world)
        w = bpy.data.worlds.new(name)
        w.use_nodes = True
        nt = w.node_tree
        bg = next(n for n in nt.nodes if n.type == "BACKGROUND")
        bg.inputs["Strength"].default_value = 1.0
        coord = nt.nodes.new("ShaderNodeTexCoord")
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        ramp = nt.nodes.new("ShaderNodeMapRange")
        ramp.clamp = True
        ramp.inputs["From Min"].default_value = lo
        ramp.inputs["From Max"].default_value = hi
        nt.links.new(coord.outputs["Generated"], sep.inputs["Vector"])
        nt.links.new(sep.outputs["Z"], ramp.inputs["Value"])
        nt.links.new(ramp.outputs["Result"], bg.inputs["Color"])
        return w

    @staticmethod
    def _bsdf(name):
        mat = bpy.data.materials.get(name)
        if mat is None or mat.node_tree is None:
            return None
        return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)

    def _emission(self, name):
        b = self._bsdf(name)
        return None if b is None else (tuple(b.inputs["Emission Color"].default_value),
                                       b.inputs["Emission Strength"].default_value)

    def _set_emission(self, name, color, strength):
        b = self._bsdf(name)
        if b is not None:
            b.inputs["Emission Color"].default_value = (*color[:3], 1.0)
            b.inputs["Emission Strength"].default_value = strength

    def set(self, mode):
        s, lamps = self.s, self.s["lamps"]
        self.sc.world = {"sky": self.white, "zenith": self.zenith}.get(mode, self.black)
        sun = self.sun.data
        sun.angle = math.radians(s["sunAngleDeg"])
        self.sun.rotation_euler = common.sun_rotation(s["sunAzimuth"], s["sunElevation"])
        if mode == "bounce":
            sun.energy, sun.color = 1.0, (1.0, 1.0, 1.0)
        else:
            sun.energy = 0.0
        if self.moon:
            self.moon.data.energy = 0.0
        lamp = mode == "lamp"
        timeofday.set_local_lights(lamps, 1.0 if lamp else 0.0, self.local_lights)
        self._set_emission("M_House_Lantern", lamps["color"], lamps["lanternEmission"] if lamp else 0.0)
        self._set_emission("M_House_Glass", lamps["color"], lamps["windowEmission"] if lamp else 0.0)
        if self.smoke:
            self.smoke.hide_render = True

    def restore(self):
        e, c, r, a = self.saved["sun"]
        self.sun.data.energy, self.sun.data.color, self.sun.rotation_euler, self.sun.data.angle = e, c, r, a
        if self.moon:
            self.moon.data.energy = self.saved["moon"]
        for light, energy, color, radius in self.saved["local_lights"]:
            light.data.energy, light.data.color = energy, color
            if radius is not None:
                light.data.shadow_soft_size = radius
        if self.smoke:
            self.smoke.hide_render = self.saved["smoke"]
        for name, v in self.saved["emission"].items():
            if v is not None:
                self._set_emission(name, v[0], v[1])
        self.sc.world = self.world


# ---------------------------------------------------------------- Geometry Nodes instances
def _instances(ob):
    """(world matrices (n, 4, 4), reference index (n,), references) of a Geometry Nodes instancer, all read from
    one evaluation."""
    deps = bpy.context.evaluated_depsgraph_get()
    gs = ob.evaluated_get(deps).evaluated_geometry()
    pc = gs.instances_pointcloud()
    refs = list(gs.instance_references())
    n = len(pc.points)
    m = np.empty(n * 16, np.float32)
    pc.attributes["instance_transform"].data.foreach_get("value", m)
    mats = np.array(ob.matrix_world)[None] @ m.reshape(n, 4, 4).transpose(0, 2, 1).astype(np.float64)
    idx = np.empty(n, np.int32)
    pc.attributes[".reference_index"].data.foreach_get("value", idx)
    return mats, idx, refs


def grass_info():
    """Blade size and colour parameters of the 'Grass' instancer (the web builds its own blades)."""
    mats, _, refs = _instances(bpy.data.objects["Grass"])
    scale = np.cbrt(np.abs(np.linalg.det(mats[:, :3, :3])))
    heights, widths = [], []
    for ref in refs:
        co = _local_coords(ref.data)
        heights.append(_r(co[:, 2].max(), 4))
        widths.append(_r(np.linalg.norm(co[1] - co[0]), 4))
    return {
        "scaleMin": _r(scale.min(), 5), "scaleMax": _r(scale.max(), 5),
        "bladeHeight": [min(heights), max(heights)],
        "bladeWidth": _r(sum(widths) / len(widths), 4),
        "rootColor": _r(common.color("grass_dark")[:3], 5), "rootMix": GRASS_ROOT_MIX,
        "tipColor": _r(common.color(build_terrain.GRASS_TIP)[:3], 5), "tipMix": GRASS_TIP_MIX,
        "valueRange": list(GRASS_VALUE), "saturationRange": list(GRASS_SATURATION),
        "translucentTint": _r(common.color(GRASS_TRANSLUCENT[0])[:3], 5), "translucentMix": GRASS_TRANSLUCENT[1],
    }


# ---------------------------------------------------------------- mobile meadow flowers
def mobile_flower_proto(me):
    """(verts, faces, corner colours) of a light flower-cluster prototype: every petal quad becomes one
    triangle (base coloured towards the head centre); head-centre boxes are dropped; blossom boxes of the
    spikes become two crossed vertical quads; stems and the leaves at the foot (hidden in the grass) go."""
    co = _local_coords(me)
    ls, lt, cv, _ = _topology(me)
    col = _read_colors(me, "Col")
    isl = _face_islands(me)
    petals, boxes = [], []
    for k in range(isl.max() + 1):
        faces = np.nonzero(isl == k)[0]
        corners = np.concatenate([np.arange(ls[f], ls[f] + lt[f]) for f in faces])
        p = co[np.unique(cv[corners])]
        lo, hi = p.min(0), p.max(0)
        if len(faces) == 1 and lt[faces[0]] == 4:
            if lo[2] < 0.15:
                continue
            c = np.arange(ls[faces[0]], ls[faces[0]] + 4)
            petals.append((co[cv[c]], col[c[0], :3]))
        elif len(faces) == 6 and hi[2] - lo[2] <= 0.08:
            boxes.append((0.5 * (lo + hi), hi - lo, col[corners, :3].mean(0)))
    verts, faces, cols = [], [], []
    centres = np.array([b[0] for b in boxes]) if boxes else np.zeros((0, 3))
    used = set()
    for q, pc in petals:
        B, Mp, T, Mm = q
        cc = pc
        if len(centres):
            d = np.linalg.norm(centres[:, :2] - B[:2], axis=1)
            j = int(d.argmin())
            if d[j] < 0.03:
                cc = boxes[j][2]
                used.add(j)
        hw = 0.5 * (Mp - Mm)
        E = B + 0.9 * (T - B)
        n = len(verts)
        verts += [B, E + 0.9 * hw, E - 0.9 * hw]
        faces.append((n, n + 1, n + 2))
        cols += [0.3 * pc + 0.7 * cc, pc, pc]
    for j, (c, s, bc) in enumerate(boxes):
        if j in used:
            continue
        for ax in ((s[0] * 0.5, 0.0), (0.0, s[1] * 0.5)):
            n = len(verts)
            dz = s[2] * 0.5
            verts += [c + (-ax[0], -ax[1], -dz), c + (ax[0], ax[1], -dz), c + (ax[0], ax[1], dz),
                      c + (-ax[0], -ax[1], dz)]
            faces.append((n, n + 1, n + 2, n + 3))
            cols += [bc] * 4
    return np.array(verts), faces, np.array(cols)


def realize_flowers(coll):
    """Mesh object FLOWERS_BAKED holding every evaluated 'Flowers' instance, built from light prototypes."""
    mats, idx, refs = _instances(bpy.data.objects["Flowers"])
    V, F, COL = [], [], []
    material = None
    nv = 0
    for r, ref in enumerate(refs):
        sel = np.nonzero(idx == r)[0]
        if not len(sel):
            continue
        material = material or ref.data.materials[0]
        pv, pf, pc = mobile_flower_proto(ref.data)
        h = np.c_[pv, np.ones(len(pv))]
        for m in mats[sel]:
            V.append((h @ m.T)[:, :3])
            F += [tuple(nv + i for i in f) for f in pf]
            COL.append(pc)
            nv += len(pv)
    vcol = np.concatenate(COL)
    cv = np.concatenate([np.array(f) for f in F])
    me = _mesh_from_arrays(FLOWERS_BAKED, np.concatenate(V), F, np.c_[vcol[cv], np.ones(len(cv))],
                           materials=[material])
    out = bpy.data.objects.new(FLOWERS_BAKED, me)
    coll.objects.link(out)
    return out


# ---------------------------------------------------------------- mobile house foliage
def _subset_mesh(me, keep_faces, name):
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    drop = [bm.faces[i] for i in np.nonzero(~keep_faces)[0]]
    bmesh.ops.delete(bm, geom=drop, context="FACES")
    out = bpy.data.meshes.new(name)
    bm.to_mesh(out)
    bm.free()
    for m in me.materials:
        out.materials.append(m)
    return out


def _decimated(me, ratio, coll):
    tmp = bpy.data.objects.new("_Decimate", me)
    coll.objects.link(tmp)
    mod = tmp.modifiers.new("Decimate", "DECIMATE")
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    deps = bpy.context.evaluated_depsgraph_get()
    out = bpy.data.meshes.new_from_object(tmp.evaluated_get(deps), preserve_all_data_layers=True, depsgraph=deps)
    bpy.data.objects.remove(tmp, do_unlink=True)
    return out


def split_foliage(ob, coll):
    """House_Foliage keeps FOLIAGE_KEEP of its leaves, enlarged so the cover stays (thin, baked two-sided);
    the closed parts move to SHRUBS: shrub lumps decimated, blossoms (material 1) as octahedra, stems as is."""
    me = ob.data
    ls, lt, cv, poly_of = _topology(me)
    co = _local_coords(me)
    col = _read_colors(me, "Col")
    mi = np.empty(len(lt), np.int64)
    me.polygons.foreach_get("material_index", mi)
    isl = _face_islands(me)
    n_isl = isl.max() + 1
    nf = np.bincount(isl, minlength=n_isl)
    imat = np.zeros(n_isl, np.int64)
    np.maximum.at(imat, isl, mi)
    tri_faces = np.bincount(isl, weights=(lt == 3).astype(float), minlength=n_isl)
    leaf = (nf == 2) & (tri_faces == 2) & (imat == 0)
    blossom = imat == 1
    lump = (nf >= 60) & ~blossom
    solid = ~leaf & ~blossom & ~lump
    rng = np.random.default_rng(5)
    kept = leaf & (rng.random(n_isl) < FOLIAGE_KEEP)
    grow = 1.0 / math.sqrt(FOLIAGE_KEEP)
    first = np.full(n_isl, -1)
    for f in np.nonzero(kept[isl])[0]:
        if first[isl[f]] < 0:
            first[isl[f]] = cv[ls[f]]
    keep_v = np.zeros(len(co), bool)
    keep_v[cv[np.isin(poly_of, np.nonzero(kept[isl])[0])]] = True
    vi = np.nonzero(keep_v)[0]
    vert_isl = np.zeros(len(co), np.int64)
    vert_isl[cv] = isl[poly_of]
    base = co[first[vert_isl[vi]]]
    co2 = co.copy()
    co2[vi] = base + (co[vi] - base) * grow
    grown = me.copy()
    grown.vertices.foreach_set("co", co2.astype(np.float32).ravel())
    leaves_me = _subset_mesh(grown, kept[isl], "House_Foliage_leaves")
    bpy.data.meshes.remove(grown)
    report = {"islands": int(n_isl), "leaves": int(leaf.sum()), "leavesKept": int(kept.sum()),
              "lumps": int(lump.sum()), "blossoms": int(blossom.sum()), "solids": int(solid.sum()),
              "trianglesBefore": _tris(me)}

    lump_me = _decimated(_subset_mesh(me, lump[isl], "House_Lumps"), FOLIAGE_LUMP_RATIO, coll)
    solid_me = _subset_mesh(me, solid[isl], "House_Solids")
    # blossoms -> octahedra
    V, F, C, M = [], [], [], []
    oct_v = np.array([(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], float)
    oct_f = ((0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5))
    keep_b = blossom & (rng.random(n_isl) < BLOSSOM_KEEP[0])
    report["blossomsKept"] = int(keep_b.sum())
    for k in np.nonzero(keep_b)[0]:
        faces = np.nonzero(isl == k)[0]
        corners = np.concatenate([np.arange(ls[f], ls[f] + lt[f]) for f in faces])
        p = co[np.unique(cv[corners])]
        lo, hi = p.min(0), p.max(0)
        n = len(V) * 6
        V.append(0.5 * (lo + hi) + oct_v * 0.5 * (hi - lo) * BLOSSOM_KEEP[1])
        F += [tuple(n + i for i in f) for f in oct_f]
        C += [col[corners].mean(0)] * 24
        M += [1] * 8
    bl_me = _mesh_from_arrays("House_Blossoms", np.concatenate(V), F, np.array(C), mats=M,
                              materials=list(me.materials)) if V else None
    bm = bmesh.new()
    for part in (lump_me, solid_me, bl_me):
        if part is not None:
            bm.from_mesh(part)
    shrubs_me = bpy.data.meshes.new(SHRUBS)
    bm.to_mesh(shrubs_me)
    bm.free()
    for m in me.materials:
        shrubs_me.materials.append(m)
    shrubs_me.shade_smooth()
    shrubs = bpy.data.objects.new(SHRUBS, shrubs_me)
    shrubs.matrix_world = ob.matrix_world
    for c in ob.users_collection:
        c.objects.link(shrubs)
    leaves_me.shade_smooth()
    ob.data = leaves_me
    report.update(leafTriangles=_tris(leaves_me), shrubTriangles=_tris(shrubs_me))
    return shrubs, report


# ---------------------------------------------------------------- lit objects
def lit_objects():
    names = [ob.name for ob in bpy.data.collections["House"].objects if ob.type == "MESH" and ob.name != SMOKE]
    names += [ob.name for ob in bpy.data.collections["Rocks"].objects if ob.type == "MESH"]
    names += list(LIT_EXTRA)
    return [bpy.data.objects[n] for n in sorted(names)]


def subdivide_long_edges(me, max_len, passes=10):
    """Halve every edge longer than max_len (grid fill), repeatedly; corner attributes are interpolated."""
    bm = bmesh.new()
    bm.from_mesh(me)
    for _ in range(passes):
        edges = [e for e in bm.edges if e.calc_length() > max_len]
        if not edges:
            break
        bmesh.ops.subdivide_edges(bm, edges=edges, cuts=1, use_grid_fill=True, use_single_edge=True)
    bm.to_mesh(me)
    bm.free()
    me.update()


def apply_modifiers(ob):
    """Give `ob` its own mesh with all modifiers applied (in memory only)."""
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(deps), preserve_all_data_layers=True, depsgraph=deps)
    old = ob.data
    old.name = old.name + "_src"
    me.name = ob.name
    ob.modifiers.clear()
    ob.data = me
    return me


def prepare_lit(objs, layers):
    """Own meshes, one-segment bevels, long edges split, modifiers applied, decimation, and one corner colour per
    light layer. Returns {object: triangles}."""
    tris = {}
    for ob in objs:
        if ob.data.users > 1:
            ob.data = ob.data.copy()
        for m in ob.modifiers:
            if m.type == "BEVEL" and m.segments > BEVEL_SEGMENTS:
                m.segments = BEVEL_SEGMENTS
        max_len = SUBDIVIDE.get(ob.name)
        smooth = any(m.type == "SUBSURF" for m in ob.modifiers)
        if max_len and not smooth:
            subdivide_long_edges(ob.data, max_len)
        if ob.name in DECIMATE:
            mod = ob.modifiers.new("MobileDecimate", "DECIMATE")
            mod.ratio = DECIMATE[ob.name]
            mod.use_collapse_triangulate = True
        if ob.modifiers:
            apply_modifiers(ob)
        if max_len and smooth:
            subdivide_long_edges(ob.data, max_len)
        for name in layers:
            _color_attr(ob.data, name)
        tris[ob.name] = _tris(ob.data)
    return tris


def terrain_uv(ob):
    """Planar UV: u = (x + HALF) / (2 HALF), v = (y + HALF) / (2 HALF) in Blender world coords (the glTF
    exporter flips v, which makes v = (z + HALF) / (2 HALF) in three.js coords)."""
    me = ob.data
    for uv in list(me.uv_layers):
        me.uv_layers.remove(uv)
    layer = me.uv_layers.new(name="MapUV")
    co = _world_coords(ob)
    _, _, cv, _ = _topology(me)
    uv = (co[cv, :2] + HALF) / (2.0 * HALF)
    layer.data.foreach_set("uv", uv.astype(np.float32).ravel())
    me.uv_layers.active = layer


# ---------------------------------------------------------------- baking
def bake_image(ob, name, res, bake_type, passes, samples):
    """Bake `ob` into a new float image; returns (res, res, 3) linear RGB, rows bottom-up."""
    sc = bpy.context.scene
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=True)
    nodes = []
    for slot in ob.material_slots:
        nt = slot.material.node_tree
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = img
        nt.nodes.active = node
        nodes.append((nt, node))
    sc.cycles.samples = samples
    _select([ob])
    bpy.ops.object.bake(type=bake_type, pass_filter=passes, target="IMAGE_TEXTURES", margin=4,
                        use_clear=True, use_selected_to_active=False)
    px = np.empty(res * res * 4, np.float32)
    img.pixels.foreach_get(px)
    for nt, node in nodes:
        nt.nodes.remove(node)
    bpy.data.images.remove(img)
    return px.reshape(res, res, 4)[..., :3].astype(np.float64)


def bake_vertex(objs, layer, passes, samples):
    """DIFFUSE light bake of `objs` into their corner colour `layer`."""
    bpy.context.scene.cycles.samples = samples
    for ob in objs:
        _color_attr(ob.data, layer)
    _select(objs)
    bpy.ops.object.bake(type="DIFFUSE", pass_filter=passes, target="VERTEX_COLORS", use_clear=True,
                        use_selected_to_active=False)


def _set_hidden(objs, hidden):
    for ob in objs:
        ob.hide_render = hidden


def bake_layers(lights, layers, terrain, terrain_hide, verts, samples, res, blur):
    """For every layer: image bake of the terrain (blurred against noise) and vertex bake of `verts`."""
    maps = {}
    for name, (mode, passes) in layers.items():
        lights.set(mode)
        if terrain is not None:
            _set_hidden(terrain_hide, True)
            try:
                img = bake_image(terrain, "Web_" + name, res[name], "DIFFUSE", passes, samples)
            finally:
                _set_hidden(terrain_hide, False)
            maps[name] = gaussian_blur(img, blur.get(name, 0.0))
        if verts:
            bake_vertex(verts, name, passes, samples)
    return maps


def bake_ground_albedo(terrain, samples, hide):
    """DIFFUSE colour of the ground under the grass: the terrain 'Col' is brightened where grass grows, so the
    plain ground colour of GroundCoverBase (the same grid) takes its place for the bake."""
    me = terrain.data
    original = _read_colors(me, "Col")
    _write_colors(me, "Col", _read_colors(bpy.data.meshes[GROUND_COVER_MESH], "Col"))
    _set_hidden(hide, True)
    try:
        return bake_image(terrain, "WebGroundAlbedo", ALBEDO_RES, "DIFFUSE", {"COLOR"}, samples)
    finally:
        _set_hidden(hide, False)
        _write_colors(me, "Col", original)


# ---------------------------------------------------------------- hidden corners
def _components(tris, n):
    """Connected component label of every vertex (0 .. count - 1) from the triangles."""
    lab = np.arange(n)
    e0 = np.concatenate([tris[:, 0], tris[:, 1]])
    e1 = np.concatenate([tris[:, 1], tris[:, 2]])
    while True:
        a, b = lab[e0], lab[e1]
        if (a == b).all():
            break
        m = np.minimum(a, b)
        np.minimum.at(lab, a, m)
        np.minimum.at(lab, b, m)
        while True:
            nxt = lab[lab]
            if (nxt == lab).all():
                break
            lab = nxt
    return np.unique(lab, return_inverse=True)[1]


class Occluders:
    """BVH of the closed occluders; `piece` gives the closed piece (connected part: a plank, a stone, a rock) of
    every BVH triangle."""

    def __init__(self, objs):
        verts, tris, pieces = [], [], []
        off = count = 0
        for ob in objs:
            me = ob.data
            me.calc_loop_triangles()
            co = _world_coords(ob)
            t = np.empty(len(me.loop_triangles) * 3, np.int64)
            me.loop_triangles.foreach_get("vertices", t)
            t = t.reshape(-1, 3)
            lab = _components(t, len(co))
            verts.append(co)
            tris.append(t + off)
            pieces.append(lab[t[:, 0]] + count)
            off += len(co)
            count += int(lab.max()) + 1 if len(lab) else 0
        V = np.concatenate(verts)
        T = np.concatenate(tris)
        self.bvh = BVHTree.FromPolygons(V.tolist(), T.tolist(), all_triangles=True)
        self.ray = self.bvh.ray_cast
        self.piece = np.concatenate(pieces)


def _ray_dirs(n):
    """The normal and two directions tilted 45 degrees from it."""
    helper = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    t = n.cross(helper).normalized()
    return (n, (n + t).normalized(), (n - t).normalized())


def _inside(ray, origin, normal):
    """True when most of three rays leave through more back faces than they enter through front faces:
    the point is inside closed geometry (overlapping solids allowed; below the terrain counts as inside)."""
    votes = 0
    for d in _ray_dirs(normal):
        o, depth = origin, 0
        for _ in range(MARCH_HITS):
            loc, nrm, _, _ = ray(o, d, INSIDE_RAY)
            if loc is None:
                break
            depth += 1 if nrm.dot(d) > 0.0 else -1
            o = loc + d * 1e-4
        votes += depth > 0
    return votes >= 2


def _openness(ray, origin, normal):
    """Share of nine directions (the normal and eight 60 degrees from it) that leave OPEN_RADIUS without a hit:
    low in a crease along a brace or under a beam."""
    helper = Vector((0.0, 0.0, 1.0)) if abs(normal.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    t1 = normal.cross(helper).normalized()
    t2 = normal.cross(t1)
    dirs = [normal] + [normal * 0.5 + (t1 * math.cos(a) + t2 * math.sin(a)) * 0.866
                       for a in (k * math.pi / 4 for k in range(8))]
    return sum(ray(origin, d, OPEN_RADIUS)[0] is None for d in dirs) / len(dirs)


def _containers(occ, origin, normal):
    """Closed pieces that contain the point: along at least two of the three rays the first hit on the piece
    leaves it (inside a convex piece every ray does)."""
    votes = {}
    for d in _ray_dirs(normal):
        o, seen = origin, set()
        for _ in range(MARCH_HITS):
            loc, nrm, idx, _ = occ.ray(o, d, INSIDE_RAY)
            if loc is None:
                break
            k = int(occ.piece[idx])
            if k not in seen:
                seen.add(k)
                if nrm.dot(d) > 0.0:
                    votes[k] = votes.get(k, 0) + 1
            o = loc + d * 1e-4
    return {k for k, v in votes.items() if v >= 2}


def _face_points(mw, poly, verts):
    """World positions of a face's centre and corners, the corners moved 1 mm toward the centre."""
    c = poly.center
    pts = [mw @ c]
    for v in poly.vertices:
        p = verts[v].co
        d = c - p
        pts.append(mw @ (p + d * min(1.0, 1e-3 / max(d.length, 1e-9))))
    return pts


def _inside_one_piece(occ, points, normal):
    """True when one closed piece contains every point (pushed 1 mm off the face along its normal): for convex
    pieces (planks, timbers, shingles, stones) the whole face between the points is then inside it. A face
    covered only by several pieces does not count: the joints between them (mortar gaps, the sides of a brace
    crossing a plank) may show it."""
    common = None
    for p in points:
        found = _containers(occ, p + normal * 1e-3, normal)
        common = found if common is None else common & found
        if not common:
            return False
    return True


def _cull_faces(me, faces):
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in faces], context="FACES")
    bm.to_mesh(me)
    bm.free()
    me.update()


def _corner_validity(ob, ray, cv, sky):
    """Valid corners: they received sky light and their vertex is not inside other closed geometry."""
    me = ob.data
    n3 = ob.matrix_world.to_3x3().inverted().transposed()
    co = _world_coords(ob)
    black = sky < DARK_SKY
    lit = sky[~black]
    healthy = np.zeros(len(co), bool)        # a well-lit corner proves its vertex is visible
    healthy[cv[sky > (0.5 * float(np.median(lit)) if len(lit) else 0.0)]] = True
    inside_v = np.zeros(len(co), bool)
    verts = me.vertices
    for i in np.nonzero(~healthy)[0]:
        n = (n3 @ verts[i].normal).normalized()
        inside_v[i] = _inside(ray, Vector(co[i]) + n * 1e-3, n)
    return co, black, inside_v[cv]


def _albedo(ob):
    """Per-corner diffuse albedo: the 'Col' attribute, or the material's base colour where the material
    does not read it (glass, lantern)."""
    me = ob.data
    _, _, cv, poly_of = _topology(me)
    attr = me.color_attributes.get("Col")
    if attr is None:
        out = np.ones((len(cv), 3))
    else:
        c = _read_colors(me, "Col")[:, :3].astype(np.float64)
        out = c[cv] if attr.domain == "POINT" else c
    mi = np.empty(len(me.polygons), np.int64)
    me.polygons.foreach_get("material_index", mi)
    mi = mi[poly_of]
    for k, slot in enumerate(ob.material_slots):
        nt = slot.material.node_tree if slot.material else None
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None) if nt else None
        if bsdf is not None and not bsdf.inputs["Base Color"].is_linked:
            out[mi == k] = bsdf.inputs["Base Color"].default_value[:3]
    return np.clip(out, 0.0, 1.0)


def fix_hidden_corners(objs, occ, layers):
    """Vertex bakes sample exactly at the corners. A corner that received (almost) no sky light, or lies inside
    other closed geometry, is no visible sample. In every layer it takes the mean of the valid corners of its
    face * HIDDEN_DARKEN, else of the valid corners at its vertex, else (faces seen only through gaps) the value
    of the nearest valid vertex within CAVITY_RADIUS * CAVITY_DARKEN. Faces with all corners hidden that lie
    inside a single closed piece are deleted (plank ends in the posts, shingle tops under the next row, stone
    backs in the chimney core), except on the emitters (GLOW). Returns {object: {fixed, unfixed, tris}}."""
    ray = occ.ray
    state = []
    nl = len(layers)
    for ob in objs:
        me = ob.data
        _, lt, cv, poly_of = _topology(me)
        vals = np.concatenate([_read_colors(me, L)[:, :3].astype(np.float64) for L in layers], axis=1)
        co, black, inside = _corner_validity(ob, ray, cv, vals[:, :3].max(axis=1))
        valid = ~black & ~inside
        w = valid.astype(np.float64)
        n_face = np.bincount(poly_of, weights=w, minlength=len(lt))
        n_vert = np.bincount(cv, weights=w, minlength=len(co))
        face_l = np.stack([np.bincount(poly_of, weights=vals[:, k] * w, minlength=len(lt))
                           for k in range(3 * nl)], 1)
        vert_l = np.stack([np.bincount(cv, weights=vals[:, k] * w, minlength=len(co)) for k in range(3 * nl)], 1)
        face_l /= np.maximum(n_face, 1)[:, None]
        vert_l /= np.maximum(n_vert, 1)[:, None]
        by_face = ~valid & (n_face[poly_of] > 0)
        by_vert = ~valid & ~by_face & (n_vert[cv] > 0)
        vals[by_face] = face_l[poly_of[by_face]] * HIDDEN_DARKEN
        vals[by_vert] = vert_l[cv[by_vert]] * HIDDEN_DARKEN
        state.append(dict(ob=ob, co=co, cv=cv, vals=vals, n_face=n_face,
                          rest=~valid & ~by_face & ~by_vert, vert_l=vert_l, has_valid=n_vert > 0,
                          fixed=int(by_face.sum() + by_vert.sum())))

    points = [(s["co"][i], s["vert_l"][i]) for s in state for i in np.nonzero(s["has_valid"])[0]]
    kd = KDTree(len(points))
    for k, (p, _) in enumerate(points):
        kd.insert(p, k)
    kd.balance()
    report = {}
    for s in state:
        rest, cv, vals = s["rest"], s["cv"], s["vals"]
        fill = np.full((len(s["co"]), 3 * nl), np.nan)
        for v in np.unique(cv[rest]):
            _, k, dist = kd.find(s["co"][v])
            if k is not None and dist <= CAVITY_RADIUS:
                fill[v] = points[k][1] * CAVITY_DARKEN
        ok = rest & ~np.isnan(fill[cv, 0])
        vals[ok] = fill[cv[ok]]
        near = int(ok.sum())
        ob, me = s["ob"], s["ob"].data
        for k, L in enumerate(layers):
            c = np.ones((len(cv), 4), np.float32)
            c[:, :3] = vals[:, 3 * k:3 * k + 3]
            _write_colors(me, L, c)
        mw = ob.matrix_world
        n3 = mw.to_3x3().inverted().transposed()
        cull = []
        # the lantern glow box sits inside the lantern frame, so its sides test as hidden, but they are what
        # shows through the frame
        for p in np.nonzero(s["n_face"] == 0)[0] if ob.name not in GLOW else ():
            poly = me.polygons[p]
            n = (n3 @ poly.normal).normalized()
            if _inside_one_piece(occ, _face_points(mw, poly, me.vertices), n):
                cull.append(int(p))
        if cull:
            _cull_faces(me, cull)
        report[ob.name] = {"fixed": s["fixed"] + near, "unfixed": int(rest.sum()) - near, "tris": _tris(me)}
    return report


def weld_corners(objs, layers):
    """Corners of one vertex with (nearly) the same normal and albedo get the mean of their light values. A vertex
    bake samples every corner separately (each with its own noise); one value per vertex on smooth surfaces halves
    the noise and lets the exporter share those vertices. Returns {object: [corners, distinct corner values]}."""
    out = {}
    for ob in objs:
        me = ob.data
        _, _, cv, _ = _topology(me)
        n = np.empty(len(cv) * 3, np.float32)
        me.corner_normals.foreach_get("vector", n)
        key = np.c_[cv, np.round(n.reshape(-1, 3) / WELD_NORMAL), np.round(_albedo(ob) * 1024.0)]
        _, grp = np.unique(key, axis=0, return_inverse=True)
        grp = grp.ravel()
        cnt = np.bincount(grp).astype(np.float64)
        for L in layers:
            v = _read_colors(me, L)
            mean = np.stack([np.bincount(grp, weights=v[:, k]) / cnt for k in range(3)], 1)
            v[:, :3] = mean[grp]
            _write_colors(me, L, v)
        out[ob.name] = [int(len(cv)), int(len(cnt))]
    return out


def poke_cavity_faces(objs, occ):
    """Faces of at least POKE_MIN_AREA whose corners all came out hidden but which are visible at their centre or
    halfway from it to a corner (a brace crossing the middle of a plank) get a vertex at the most open of the
    visible points (not in the crease along the brace), so a re-bake samples their visible part instead of the
    occluded corners; the hidden corners then take its light. Returns {object name: poked faces}."""
    ray = occ.ray
    poked = {}
    for ob in objs:
        me = ob.data
        _, lt, cv, poly_of = _topology(me)
        sky = _read_colors(me, "S")[:, :3].max(axis=1)
        _, black, inside = _corner_validity(ob, ray, cv, sky)
        valid = ~black & ~inside
        n_face = np.bincount(poly_of, weights=valid.astype(np.float64), minlength=len(lt))
        area = np.empty(len(lt))
        me.polygons.foreach_get("area", area)
        scale = abs(ob.matrix_world.to_3x3().determinant()) ** (2.0 / 3.0)
        candidates = (n_face == 0) & (area * scale >= POKE_MIN_AREA)
        mw = ob.matrix_world
        n3 = mw.to_3x3().inverted().transposed()
        faces = []
        verts = me.vertices
        for p in np.nonzero(candidates)[0]:
            poly = me.polygons[p]
            n = (n3 @ poly.normal).normalized()
            c = poly.center
            best = None
            for q in [c] + [(c + verts[v].co) * 0.5 for v in poly.vertices]:
                o = mw @ q + n * 1e-3
                if _inside(ray, o, n):
                    continue
                score = _openness(ray, o, n)
                if best is None or score > best[0]:
                    best = (score, q.copy())
            if best is not None:
                faces.append((int(p), best[1]))
        if not faces:
            continue
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        for f, q in [(bm.faces[i], q) for i, q in faces]:
            bmesh.ops.poke(bm, faces=[f])["verts"][0].co = q
        bm.to_mesh(me)
        bm.free()
        me.update()
        poked[ob.name] = len(faces)
    return poked


def bake_back_sides(objs, lights, layers, samples):
    """Bake the back side of thin single-sided parts and give every face a reversed twin carrying the front
    bake: the mesh ends up two-sided, each side with its own light."""
    for ob in objs:
        me = ob.data
        for L in layers:
            front = me.color_attributes.new("Front_" + L, "FLOAT_COLOR", "CORNER")
            front.data.foreach_set("color", _read_colors(me, L).ravel())
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bm.to_mesh(me)
        bm.free()
        me.update()
    bake_layers(lights, layers, None, (), objs, samples, {}, {})
    for ob in objs:
        me = ob.data
        bm = bmesh.new()
        bm.from_mesh(me)
        dup = bmesh.ops.duplicate(bm, geom=bm.faces[:] + bm.edges[:] + bm.verts[:])
        twins = [g for g in dup["geom"] if isinstance(g, bmesh.types.BMFace)]
        bmesh.ops.reverse_faces(bm, faces=twins)
        for L in layers:
            val = bm.loops.layers.float_color[L]
            front = bm.loops.layers.float_color["Front_" + L]
            for f in twins:
                for loop in f.loops:
                    loop[val] = loop[front]
        bm.to_mesh(me)
        bm.free()
        for L in layers:
            me.color_attributes.remove(me.color_attributes["Front_" + L])
        me.update()
    return {ob.name: _tris(ob.data) for ob in objs}


# ---------------------------------------------------------------- export terrain
def _original_grid(terrain):
    """xs, ys of the build_terrain grid and the terrain's per-vertex height and albedo on it."""
    xs = build_terrain._axis(-HALF, HALF, -27.0, 21.0, build_terrain.GRID_FINE, build_terrain.GRID_COARSE)
    ys = build_terrain._axis(-HALF, HALF, -20.0, 24.0, build_terrain.GRID_FINE, build_terrain.GRID_COARSE)
    co = _world_coords(terrain)
    nx, ny = len(xs), len(ys)
    assert len(co) == nx * ny, "terrain is not the build_terrain grid"
    G = co.reshape(ny, nx, 3)
    assert np.abs(G[0, :, 0] - xs).max() < 1e-3 and np.abs(G[:, 0, 1] - ys).max() < 1e-3
    col = _read_colors(terrain.data, "Col")
    assert terrain.data.color_attributes["Col"].domain == "POINT"
    return xs, ys, G[..., 2], col.reshape(ny, nx, 4)


def export_terrain(full, coll):
    """Coarser export terrain (TERRAIN_GRID spacing), heights and albedo bilinear from the full grid,
    triangulated along the shorter diagonal; the full terrain is renamed TERRAIN_BAKE."""
    xs, ys, H, COL = _original_grid(full)
    g = TERRAIN_GRID
    ax = build_terrain._axis(-HALF, HALF, *g["core_x"], g["fine"], g["coarse"], g["ramp"])
    ay = build_terrain._axis(-HALF, HALF, *g["core_y"], g["fine"], g["coarse"], g["ramp"])
    X, Y = np.meshgrid(ax, ay)
    x, y = X.ravel(), Y.ravel()
    z = _grid_sample(xs, ys, H, x, y)
    col = _grid_sample(xs, ys, COL, x, y)
    V = np.stack([x, y, z], 1)
    nx = len(ax)
    j, i = np.meshgrid(np.arange(len(ay) - 1), np.arange(nx - 1), indexing="ij")
    a = (j * nx + i).ravel()
    b, c, d = a + 1, a + nx + 1, a + nx
    d1 = np.linalg.norm(V[a] - V[c], axis=1)
    d2 = np.linalg.norm(V[b] - V[d], axis=1)
    use_ac = d1 <= d2
    F = np.concatenate([np.where(use_ac[:, None], np.stack([a, b, c], 1), np.stack([a, b, d], 1)),
                        np.where(use_ac[:, None], np.stack([a, c, d], 1), np.stack([b, c, d], 1))])
    full.name = TERRAIN_BAKE
    full.data.name = TERRAIN_BAKE
    me = bpy.data.meshes.new(TERRAIN)
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    me.materials.append(full.data.materials[0])
    me.shade_smooth()
    attr = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", col.astype(np.float32).ravel())
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = me.color_attributes.active_color_index
    ob = bpy.data.objects.new(TERRAIN, me)
    coll.objects.link(ob)
    ob.hide_render = True                  # never part of a bake
    terrain_uv(ob)
    return ob


def ground_cover_map(full, out):
    """Write ground_cover.png and return the float surface coverage before canopy thinning."""
    xs, ys, _, _ = _original_grid(full)
    cover = bpy.data.meshes[GROUND_COVER_MESH]
    gm = np.empty(len(cover.vertices), np.float32)
    cover.attributes["grass_mask"].data.foreach_get("value", gm)
    gm = gm.reshape(len(ys), len(xs))
    X, Y = _map_xy(GRASS_RES)
    coverage = np.clip(_grid_sample(xs, ys, gm, X.ravel(), Y.ravel()), 0.0, 1.0).reshape(GRASS_RES, GRASS_RES)
    write_png(os.path.join(out, "ground_cover.png"), np.round(coverage * 255.0).astype(np.uint8))
    return coverage


def grass_map(terrain_ob, full, trees, out):
    """grass.png: R = density (GroundCoverBase grass_mask, faded out under tree canopies), G/B = 16-bit height
    of the export terrain (ray cast); returns the height range {heightMin, heightMax}."""
    dens = ground_cover_map(full, out)
    X, Y = _map_xy(GRASS_RES)
    canopy = np.zeros_like(dens)
    texel = 2.0 * HALF / GRASS_RES
    for t in trees:
        if t["kind"] != "tree":
            continue
        x0, y0, r = t["x"], t["y"], t["radius"]
        c0 = int(max(0, (x0 - r + HALF) / texel - 1))
        c1 = int(min(GRASS_RES, (x0 + r + HALF) / texel + 2))
        r0 = int(max(0, (-y0 - r + HALF) / texel - 1))
        r1 = int(min(GRASS_RES, (-y0 + r + HALF) / texel + 2))
        if c0 >= c1 or r0 >= r1:
            continue
        d = np.hypot(X[r0:r1, c0:c1] - x0, Y[r0:r1, c0:c1] - y0) / r
        f = np.clip((CANOPY_GRASS[0] - d) / (CANOPY_GRASS[0] - CANOPY_GRASS[1]), 0.0, 1.0)
        canopy[r0:r1, c0:c1] = np.maximum(canopy[r0:r1, c0:c1], f * f * (3 - 2 * f))
    dens_out = np.clip(dens * (1.0 - canopy), 0.0, 1.0)
    me = terrain_ob.data
    me.calc_loop_triangles()
    co = _world_coords(terrain_ob)
    tri = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    bvh = BVHTree.FromPolygons(co.tolist(), tri.reshape(-1, 3).tolist(), all_triangles=True)
    h = np.empty(GRASS_RES * GRASS_RES)
    down = Vector((0.0, 0.0, -1.0))
    for k, (x, y) in enumerate(zip(X.ravel(), Y.ravel())):
        loc, _, _, _ = bvh.ray_cast(Vector((x, y, 50.0)), down, 200.0)
        h[k] = loc.z if loc is not None else np.nan
    h = h.reshape(GRASS_RES, GRASS_RES)
    h = np.where(np.isnan(h), np.nanmin(h), h)
    lo, hi = float(np.floor(h.min() * 100) / 100), float(np.ceil(h.max() * 100) / 100)
    q = np.round((h - lo) / (hi - lo) * 65535.0).astype(np.int64)
    img = np.stack([np.round(dens_out * 255.0), q >> 8, q & 255], axis=2).astype(np.uint8)
    write_png(os.path.join(out, "grass.png"), img)
    return {"heightMin": lo, "heightMax": hi}


# ---------------------------------------------------------------- trees
def tree_instances():
    """Instances of the Forest collection in three.js coordinates, sorted by object name."""
    forest = bpy.data.collections[FOREST]
    obs = sorted((ob for ob in forest.all_objects if ob.type == "MESH"), key=lambda o: o.name)
    out = []
    for ob in obs:
        loc, rot, scl = ob.matrix_world.decompose()
        r3 = C3 @ np.array(rot.to_matrix()) @ C3.T
        q = Matrix(r3.tolist()).to_quaternion()
        co = _local_coords(ob.data)
        out.append({"name": ob.name, "variant": ob.data.name, "p": to_three(loc), "q": [q.x, q.y, q.z, q.w],
                    "s": float(scl.x), "kind": "bush" if ob.data.name.startswith("Bush") else "tree",
                    "x": float(loc.x), "y": float(loc.y),
                    "radius": float(np.hypot(co[:, 0], co[:, 1]).max() * scl.x),
                    "height": float(co[:, 2].max() * scl.x)})
    return out


def lod_objects(coll):
    lods = build_trees.build_lod_meshes(bpy.data.materials["M_Bark"], bpy.data.materials["M_Leaves"])
    objs = []
    for name, info in sorted(lods.items()):
        ob = bpy.data.objects.new(name, info["mesh"])
        coll.objects.link(ob)
        ob.hide_render = True
        ob["shade"] = "treeLOD"
        ob["variant"] = info["variant"]
        ob["lod"] = info["lod"]
        objs.append(ob)
    return objs, lods


def _frustum_test(cam, aspect, centers, radii, portrait=False):
    """Bounding spheres inside the camera frustum (AUTO sensor fit: the sensor spans the larger side)."""
    m = np.array(cam["matrix"])
    pos = m[:3, 3]
    R = m[:3, :3]
    rel = (centers - pos) @ R                     # camera space: x right, y up, -z forward
    fov = math.radians(cam["hfov"])
    tx = math.tan(fov * 0.5)
    ty = tx / aspect
    if portrait:                                  # the sensor spans the height
        ty, tx = tx, tx * aspect
    depth = -rel[:, 2]
    ok = depth > -radii
    for t, comp in ((tx, rel[:, 0]), (ty, rel[:, 1])):
        n = 1.0 / math.sqrt(1.0 + t * t)
        ok &= (np.abs(comp) - t * depth) * n < radii
    return ok, np.linalg.norm(centers - pos, axis=1)


def lod_plan(instances, lods, cams):
    """LOD switch distances [d01, d12]: the largest d01 (5 m steps, d12 = 2 * d01) that keeps the tree triangles
    of every camera preset (landscape 16:9 and portrait 9:19.5) under LOD_TREE_BUDGET."""
    tri = {}
    for info in lods.values():
        tri.setdefault(info["variant"], [0, 0, 0])[info["lod"]] = info["tris"]
    centers = np.array([[i["x"], i["y"], 0.0] for i in instances])
    centers[:, 2] = [i["height"] * 0.55 for i in instances]
    centers[:, 2] += [common.terrain_height(i["x"], i["y"]) for i in instances]
    radii = np.array([max(i["radius"], i["height"] * 0.55) for i in instances])
    T = np.array([tri[i["variant"]] for i in instances])
    views = []
    for cam in cams.values():
        hide = set(cam["corridor"]["hide"])
        keep = np.array([k not in hide for k in range(len(instances))])
        for aspect, portrait in ((16 / 9, False), (9 / 19.5, True)):
            vis, dist = _frustum_test(cam, aspect, centers, radii, portrait)
            views.append((vis & keep, dist))

    def within_budget(d01, d12):
        for vis, dist in views:
            lod = np.where(dist < d01, 0, np.where(dist < d12, 1, 2))
            if T[vis, lod[vis]].sum() > LOD_TREE_BUDGET:
                return False
        return True

    best = [20, 40]
    for d01 in range(20, 121, 5):
        d12 = 2 * d01                             # L2 leaves are about as large on screen there as L1's at d01
        if within_budget(d01, d12):
            best = [d01, d12]
    return best


# ---------------------------------------------------------------- export prep
def camera_info(name):
    """Sets the GLB extras the viewer reads (lens_mm, sensor_mm, fstop, target) on a camera and returns its
    target / lookAt (three.js coordinates), the corridor trees ('hideNames'; main fills the instance indices
    'hide') and, for the LOD plan, its world matrix and horizontal field of view."""
    cam = bpy.data.objects[name]
    d = cam.data
    m = cam.matrix_world
    pos = m.translation
    fwd = -(m.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    focus = d.dof.focus_object.matrix_world.translation if d.dof.focus_object else pos + fwd * d.dof.focus_distance
    cfg = build_lighting.CAMERAS.get(name, {})
    base = Vector((common.HOUSE_POS.x, common.HOUSE_POS.y, common.HOUSE_GROUND))
    look = base + Vector(cfg["target"]) if "target" in cfg else pos + fwd * float((focus - pos).dot(fwd))
    cam["fstop"] = round(float(d.dof.aperture_fstop), 4)
    cam["lens_mm"] = round(float(d.lens), 4)
    cam["sensor_mm"] = round(float(d.sensor_width), 4)
    cam["target"] = _r(to_three(focus), 4)
    mat = np.eye(4)
    mat[:3, :3] = np.array(m.to_3x3())
    mat[:3, 3] = np.array(pos)
    hfov = 2.0 * math.atan(d.sensor_width * 0.5 / d.lens)
    return {
        "target": _r(to_three(focus), 4),
        "lookAt": _r(to_three(look), 4),
        "corridor": {"hide": [], "hideNames": [n for n in cam.get("hide_objects", "").split(";")
                                               if n in bpy.data.objects]},
        "hfov": _r(math.degrees(hfov), 4), "matrix": mat.tolist(),
    }


def _patch_meshopt_precision():
    import io_scene_gltf2.io.exp.meshopt as mo
    mo.EXP_FILTER_BITS = MESHOPT_EXP_BITS
    mo.QUAT_FILTER_BITS = MESHOPT_QUAT_BITS


@contextlib.contextmanager
def _vertex_color_alpha_unlinked(mat_names):
    """The exporter drops COLOR_0 from a material whose node tree reads only the vertex colour alpha
    (M_Water), so those alpha links are removed for the export; the web water shader uses COLOR_0.a."""
    removed = []
    for name in mat_names:
        mat = bpy.data.materials.get(name)
        if mat is None or mat.node_tree is None:
            continue
        nt = mat.node_tree
        for link in list(nt.links):
            if link.from_node.type == "VERTEX_COLOR" and link.from_socket.name == "Alpha":
                removed.append((nt, link.from_socket, link.to_socket))
                nt.links.remove(link)
    try:
        yield
    finally:
        for nt, a, b in removed:
            nt.links.new(a, b)


def export_glb(path, objs):
    _select(objs)
    with _vertex_color_alpha_unlinked([s.material.name for s in bpy.data.objects[WATER].material_slots]):
        bpy.ops.export_scene.gltf(
            filepath=path, export_format="GLB", use_selection=True, export_yup=True, export_apply=True,
            export_vertex_color="ACTIVE", export_all_vertex_colors=False,
            export_active_vertex_color_when_no_material=True, export_cameras=True, export_extras=True,
            export_gpu_instances=False, export_meshopt_compression_enable=True,
            export_meshopt_extension="EXT_meshopt_compression", export_lights=False, export_materials="EXPORT",
            export_image_format="NONE", export_animations=False, export_skins=False, export_morph=False,
            export_texcoords=True, export_normals=True, export_tangents=False, export_attributes=True)


def merge_objects(name, objs):
    """Join `objs` (after baking) into one object called `name`."""
    objs = [o for o in objs if o is not None]
    if not objs:
        return None
    if len(objs) > 1:
        _select(objs)
        bpy.ops.object.join()
    ob = objs[0]
    ob.name = name
    ob.data.name = name
    return ob


# ---------------------------------------------------------------- light encoding
def zenith_fraction(S, Sz):
    """Z = luminance(S under the zenith-weight sky) / luminance(S): the share of zenith colour in a surface's
    sky light (0.5 where no sky reaches)."""
    s = np.asarray(S)[..., :3] @ LUMA
    z = np.asarray(Sz)[..., :3] @ LUMA
    return np.where(s > 1e-4, np.clip(z / np.maximum(s, 1e-4), 0.0, 1.0), 0.5)


def lamp_scalar(rgb, lamp_color):
    return (np.asarray(rgb)[..., :3] @ LUMA) / float(np.dot(lamp_color, LUMA))


def layer_scales(maps, lit, lamp_color):
    """skyScale / bounceScale = HEADROOM * the PERCENTILE of the terrain texels and lit corners of S / B;
    lampScale = HEADROOM * the LAMP_PERCENTILE of the lamp-lit values (Lp > LAMP_FLOOR), with Lp converted to
    luminance(L) / luminance(lampColor)."""
    out = {}
    for k in ("S", "B"):
        v = np.concatenate([maps[k].reshape(-1, 3)] + [_read_colors(ob.data, k)[:, :3] for ob in lit]).ravel()
        out[k] = HEADROOM * float(np.percentile(v, PERCENTILE))
    lp = np.concatenate([lamp_scalar(maps["Lp"], lamp_color).ravel()]
                        + [lamp_scalar(_read_colors(ob.data, "Lp"), lamp_color) for ob in lit])
    out["Lp"] = HEADROOM * float(np.percentile(lp[lp > LAMP_FLOOR], LAMP_PERCENTILE))
    return out


def encode_lit(ob, scales, lamp_color):
    """Albedo -> active colour (COLOR_0); _SKY = sqrt(S/skyScale) rgb + sqrt(Lp/lampScale) a; _BOUNCE =
    sqrt(B/bounceScale) rgb + zenith fraction Z a (linear). The layer attributes are removed."""
    me = ob.data
    alb = _albedo(ob)
    S = _read_colors(me, "S")[:, :3]
    B = _read_colors(me, "B")[:, :3]
    Lp = lamp_scalar(_read_colors(me, "Lp"), lamp_color)
    a = np.ones((len(alb), 4), np.float32)
    a[:, :3] = alb
    sky = np.ones_like(a)
    sky[:, :3] = np.sqrt(np.clip(S / scales["S"], 0.0, 1.0))
    sky[:, 3] = np.sqrt(np.clip(Lp / scales["Lp"], 0.0, 1.0))
    bo = np.ones_like(a)
    bo[:, :3] = np.sqrt(np.clip(B / scales["B"], 0.0, 1.0))
    bo[:, 3] = zenith_fraction(S, _read_colors(me, "Z"))
    for name in [c.name for c in me.color_attributes if c.name not in ("Col",)]:
        me.color_attributes.remove(me.color_attributes[name])
    for name, data in (("_SKY", sky), ("_BOUNCE", bo), ("Albedo", a)):
        attr = me.color_attributes.new(name, "FLOAT_COLOR", "CORNER")
        attr.data.foreach_set("color", data.ravel())
    _color_attr(me, "Albedo")


def write_terrain_maps(out, maps, scales, lamp_color):
    for key, fname in (("S", "terrain_sky.png"), ("B", "terrain_bounce.png")):
        write_png(os.path.join(out, fname), sqrt_encode(maps[key], scales[key])[::-1])
    lp = lamp_scalar(maps["Lp"], lamp_color)
    write_png(os.path.join(out, "terrain_lamp.png"), sqrt_encode(lp, scales["Lp"])[::-1])
    z = zenith_fraction(_downsample(maps["S"], RES["S"] // RES["Z"]), maps["Z"])
    write_png(os.path.join(out, "terrain_zenith.png"), np.round(z * 255.0).astype(np.uint8)[::-1])


# ---------------------------------------------------------------- main
def optimize_glb(path):
    """Run the web project's optimizer (renames _SKY / _BOUNCE to COLOR_1 / COLOR_2, quantizes normals / colours
    / UVs, meshopt on every stream, updates manifest.files). Skipped with a note when Node or the web dependencies
    are missing."""
    script = os.path.join(WEB_DIR, "scripts", "optimize-glb.mjs")
    if not os.path.isdir(os.path.join(WEB_DIR, "node_modules", "@gltf-transform")):
        return {"skipped": "run 'npm install' in " + WEB_DIR}
    before = os.path.getsize(path)
    res = subprocess.run(["node", script, path], cwd=WEB_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        return {"failed": (res.stderr or res.stdout)[-800:]}
    return {"before": before, "after": os.path.getsize(path), "log": res.stdout.strip()[-400:]}


def main():
    opts = _args()
    t0 = time.time()
    samples, quick = opts["samples"], opts["quick"]
    blur = {k: v * (2.0 if quick else 1.0) for k, v in BLUR.items()}
    out = os.path.abspath(opts["out"])
    os.makedirs(out, exist_ok=True)
    sc = bpy.context.scene
    common.setup_cycles(samples)
    sc.cycles.use_denoising = False
    sc.cycles.use_adaptive_sampling = False
    tod = timeofday.load()
    state = timeofday.apply(tod["defaultHour"], sc)
    lamp_color = np.array(tod["lamps"]["color"])
    lights = BakeLights(sc, state)
    _log("setup", t0, device=sc.cycles.device, samples=samples, quick=quick)

    grass = grass_info()
    cover = bpy.data.objects["Flowers"].users_collection[0]
    flowers = realize_flowers(cover)
    _set_hidden([bpy.data.objects[n] for n in INSTANCERS], True)
    foliage = bpy.data.objects["House_Foliage"]
    if foliage.data.users > 1:
        foliage.data = foliage.data.copy()
    shrubs, foliage_report = split_foliage(foliage, cover)
    _log("ground cover", t0, flowers=_tris(flowers.data), foliage=foliage_report)

    lit = lit_objects() + [flowers]
    assert shrubs in lit
    lit_tris = prepare_lit(lit, list(LAYERS))
    _log("prepare", t0, triangles=sum(lit_tris.values()))
    terrain = bpy.data.objects[TERRAIN]
    terrain_uv(terrain)
    instances = tree_instances()

    # the flowers stay out of the terrain image bakes (they would shade the ground under them)
    maps = bake_layers(lights, LAYERS, terrain, [flowers], lit, samples, RES, blur)
    _log("layer bakes", t0)

    occluders = [ob for ob in lit if ob.name.startswith(("House_", "Rocks_")) and ob.name not in OCCLUDER_SKIP]
    occ = Occluders(occluders + [terrain])
    poked = poke_cavity_faces([ob for ob in lit if ob.name not in THIN], occ)
    rebake = [ob for ob in lit if ob.name in poked]
    if rebake:
        bake_layers(lights, LAYERS, None, (), rebake, samples, RES, blur)
    _log("cavity faces", t0, poked=poked)
    two_sided = bake_back_sides([ob for ob in lit if ob.name in THIN], lights, LAYERS, samples)
    _log("back sides", t0, tris=two_sided)
    lights.restore()

    hidden = fix_hidden_corners(lit, occ, list(LAYERS))
    _log("hidden corners", t0, **{k: sum(v[k] for v in hidden.values()) for k in ("fixed", "unfixed", "tris")})
    weld = weld_corners(lit, list(LAYERS))
    _log("weld", t0, corners=sum(v[0] for v in weld.values()), values=sum(v[1] for v in weld.values()))

    # scales and encodings
    scales = layer_scales(maps, lit, lamp_color)
    write_terrain_maps(out, maps, scales, lamp_color)
    albedo = bake_ground_albedo(terrain, max(16, samples // 32), [flowers])
    write_png(os.path.join(out, "ground_albedo.png"),
              np.round(linear_to_srgb(albedo) * 255.0).astype(np.uint8)[::-1], srgb=True)
    for ob in lit:
        encode_lit(ob, scales, lamp_color)
    _log("maps", t0, scales={k: round(v, 4) for k, v in scales.items()})

    # export geometry
    eterrain = export_terrain(terrain, terrain.users_collection[0])
    grass.update(grass_map(eterrain, terrain, instances, out))
    _log("terrain + grass", t0, terrain=_tris(eterrain.data), heights=(grass["heightMin"], grass["heightMax"]))

    lit_by_name = {ob.name: ob for ob in lit}
    merged = []
    for name, members in MERGE:
        ob = merge_objects(name, [lit_by_name.pop(m, None) for m in members])
        if ob is not None:
            merged.append(ob)
    fl = lit_by_name.pop(FLOWERS_BAKED)
    fl.name = FLOWERS_EXPORT
    fl.data.name = FLOWERS_EXPORT
    final_lit = sorted(merged + list(lit_by_name.values()) + [fl], key=lambda o: o.name)
    for ob in final_lit:
        ob["shade"] = "lit"
    eterrain["shade"] = "terrain"
    bpy.data.objects[WATER]["shade"] = "water"
    bpy.data.objects[SMOKE]["shade"] = "smoke"
    lcoll = bpy.data.collections.new("WebTreeLODs")
    sc.collection.children.link(lcoll)
    lod_obs, lods = lod_objects(lcoll)
    cams = {name: camera_info(name) for name in CAMERAS}
    names = [i["name"] for i in instances]
    for c in cams.values():
        c["corridor"]["hide"] = [names.index(n) for n in c["corridor"]["hideNames"] if n in names]
    lod_distances = lod_plan(instances, lods, cams)
    _log("tree LODs", t0, meshes=len(lod_obs), lodDistances=lod_distances)

    _patch_meshopt_precision()
    glb = os.path.join(out, "scene.glb")
    export_glb(glb, final_lit + [eterrain, bpy.data.objects[WATER], bpy.data.objects[SMOKE]] + lod_obs
               + [bpy.data.objects[c] for c in CAMERAS])
    _log("export", t0, size=os.path.getsize(glb))

    manifest = build_manifest(tod, state, scales, grass, cams, instances, lod_distances)
    files = ("scene.glb", "terrain_sky.png", "terrain_bounce.png", "terrain_lamp.png", "terrain_zenith.png",
             "ground_albedo.png", "ground_cover.png", "grass.png")
    manifest["files"] = {name: os.path.getsize(os.path.join(out, name)) for name in files}
    with open(os.path.join(out, "manifest.json"), "w", newline=LF) as f:
        json.dump(manifest, f, indent=1)
    optimized = optimize_glb(glb)
    _log("optimize", t0, **{k: v for k, v in optimized.items() if k != "log"})
    _log("done", t0, total_bytes=sum(os.path.getsize(os.path.join(out, n)) for n in files + ("manifest.json",)))


def build_manifest(tod, state, scales, grass, cams, instances, lod_distances):
    """Everything the viewer reads except 'files' (main adds the file sizes)."""
    mist = bpy.context.scene.world.mist_settings
    lantern = bpy.data.objects["LanternLight"].matrix_world.translation
    return {
        "version": 2,
        "skyScale": _r(scales["S"], 6),
        "bounceScale": _r(scales["B"], 6),
        "lampScale": _r(scales["Lp"], 6),
        "terrainUV": {"half": HALF},
        "grass": grass,
        "trees": {
            "instances": [{"variant": i["variant"], "p": _r(i["p"], 4), "q": _r(i["q"], 6), "s": _r(i["s"], 5)}
                          for i in instances],
            "lodDistances": lod_distances,
        },
        "timeOfDay": tod,
        "sunDefault": {"elevation": _r(state["sunElevation"], 4)},
        "lamps": {"pointLightPosition": _r(to_three(lantern), 4)},
        "haze": {"start": mist.start, "depth": mist.depth},
        "cameras": {name: {"target": c["target"], "lookAt": c["lookAt"], "corridor": {"hide": c["corridor"]["hide"]}}
                    for name, c in cams.items()},
    }


if __name__ == "__main__":
    main()
