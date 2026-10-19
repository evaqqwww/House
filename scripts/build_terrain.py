"""Landscape for the forest-cottage scene: terrain, pond, rocks and ground cover.

build() fills the 'Landscape' collection:
  Terrain      Terrain                       vertex-coloured ground mesh (M_Ground)
  Water        PondWater, LilyPads, Reeds    pond surface, floating pads with flowers, reed/cattail clumps
  Rocks        Rocks_Cliff, Rocks_Boulders, Rocks_Stones
  GroundCover  Grass, Flowers                Geometry Nodes instancers on a copy of the terrain mesh;
               GroundCover_Protos            blade / flower source meshes (excluded from the view layer)
"""
import math
import time

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

import common as C

TAU = math.tau

GRASS_DENSITY = 950.0      # blades per m2 where grass_mask == 1
FLOWER_DENSITY = 3.0       # flower clusters per m2 where flower_mask == 1
GRID_FINE = 0.35           # terrain cell size inside the clearing core
GRID_COARSE = 1.0          # terrain cell size at the outer border
MEADOW_FRESH = "#a2c86c"   # cool fresh green mixed into the meadow so it reads sunlit, not olive
GRASS_TIP = "#c6de80"      # light yellow-green blade tips

# reed clumps: (angle range in radians around the pond centre, clump count)
REED_ARCS = (((2.65, 3.95), 9), ((0.45, 0.95), 4), ((5.15, 5.45), 2))
LILY_CLUSTERS = ((-1.6, 0.9, 9), (1.3, -1.1, 7), (-0.6, -2.0, 6), (1.7, 1.5, 5), (-2.6, -0.8, 4))


# ---------------------------------------------------------------- small helpers
def _rgb(name):
    return np.array(C.color(name)[:3])


def _ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _mixa(a, b, t):
    """Mix colour arrays (..., 3); t broadcasts over the last axis."""
    t = np.asarray(t)
    if t.ndim:
        t = t[..., None]
    return a + (b - a) * t


def _scale(c, f):
    return (c[0] * f, c[1] * f, c[2] * f, c[3] if len(c) > 3 else 1.0)


def _axis(lo, hi, core_lo, core_hi, fine, coarse, ramp=8.0):
    """1-D grid coordinates: `fine` spacing inside [core_lo, core_hi], growing to `coarse` outside."""
    pts = [core_lo]
    x = core_lo
    while x < core_hi - 1e-9:
        x = min(core_hi, x + fine)
        pts.append(x)
    x = core_hi
    while x < hi - 1e-9:
        step = fine + (coarse - fine) * min(1.0, (x - core_hi) / ramp)
        x = min(hi, x + step)
        if hi - x < 0.3 * step:
            x = hi
        pts.append(x)
    left = []
    x = core_lo
    while x > lo + 1e-9:
        step = fine + (coarse - fine) * min(1.0, (core_lo - x) / ramp)
        x = max(lo, x - step)
        if x - lo < 0.3 * step:
            x = lo
        left.append(x)
    return np.array(sorted(left) + pts)


def _exclude_from_view_layers(coll):
    def find(lc):
        if lc.collection == coll:
            return lc
        for ch in lc.children:
            r = find(ch)
            if r is not None:
                return r
        return None

    for vl in bpy.context.scene.view_layers:
        lc = find(vl.layer_collection)
        if lc is not None:
            lc.exclude = True


def _mesh_from_arrays(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts.tolist() if hasattr(verts, "tolist") else verts, [], faces)
    me.update()
    return me


def _set_point_color(me, cols):
    attr = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", np.ascontiguousarray(cols, dtype=np.float32).ravel())
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = me.color_attributes.active_color_index


def _set_point_float(me, name, values):
    attr = me.attributes.new(name, "FLOAT", "POINT")
    attr.data.foreach_set("value", np.ascontiguousarray(values, dtype=np.float32))


# ---------------------------------------------------------------- terrain fields
class Fields:
    """Per-vertex terrain samples on the variable-spacing grid (flattened row-major, rows = y)."""

    def __init__(self):
        xs = _axis(-C.TERRAIN_HALF, C.TERRAIN_HALF, -27.0, 21.0, GRID_FINE, GRID_COARSE)
        ys = _axis(-C.TERRAIN_HALF, C.TERRAIN_HALF, -20.0, 24.0, GRID_FINE, GRID_COARSE)
        self.xs, self.ys = xs, ys
        nx, ny = len(xs), len(ys)
        H = np.empty((ny, nx))
        CSD = np.empty((ny, nx))
        PSD = np.empty((ny, nx))
        N1 = np.empty((ny, nx))
        N2 = np.empty((ny, nx))
        N3 = np.empty((ny, nx))
        N4 = np.empty((ny, nx))
        th, csd, psd, n2 = C.terrain_height, C.clearing_signed_distance, C.pond_signed_distance, C.n2
        for j, y in enumerate(ys):
            y = float(y)
            for i, x in enumerate(xs):
                x = float(x)
                H[j, i] = th(x, y)
                CSD[j, i] = csd(x, y)
                PSD[j, i] = psd(x, y)
                N1[j, i] = n2(x / 9.0, y / 9.0, 21.7)
                N2[j, i] = n2(x / 3.0, y / 3.0, 4.4)
                N3[j, i] = n2(x / 4.2, y / 4.2, 33.3)
                N4[j, i] = n2(x / 1.2, y / 1.2, 12.9)
        dHy, dHx = np.gradient(H, ys, xs)
        X, Y = np.meshgrid(xs, ys)
        self.nx, self.ny = nx, ny
        self.X, self.Y = X.ravel(), Y.ravel()
        self.H, self.CSD, self.PSD = H.ravel(), CSD.ravel(), PSD.ravel()
        self.N1, self.N2, self.N3, self.N4 = N1.ravel(), N2.ravel(), N3.ravel(), N4.ravel()
        self.S = np.hypot(dHx, dHy).ravel()
        # filled by the rock pass
        self.rock_cover = np.zeros_like(self.H)   # 1 where a rock body sits above the ground
        self.rock_band = np.zeros_like(self.H)    # soft halo around cliff rocks
        self.boulder_ring = np.zeros_like(self.H)  # flower boost around meadow boulders

    def faces(self):
        nx, ny = self.nx, self.ny
        j, i = np.meshgrid(np.arange(ny - 1), np.arange(nx - 1), indexing="ij")
        a = (j * nx + i).ravel()
        return np.stack([a, a + 1, a + nx + 1, a + nx], axis=1)


def _ground_colors(F):
    g_light, g_mid, g_dark = _rgb("grass_light"), _rgb("grass_mid"), _rgb("grass_dark")
    leaf_dark, rock_dark = _rgb("leaf_dark"), _rgb("rock_dark")
    dirt, mud = _rgb("dirt"), _rgb("mud")

    fresh = _rgb(MEADOW_FRESH)
    meadow = _mixa(_mixa(g_mid, fresh, 0.6), _mixa(g_light, fresh, 0.45), 0.2 + 0.7 * _ss(-0.45, 0.55, F.N1))
    meadow = _mixa(meadow, g_mid, 0.3 * _ss(0.15, 0.65, F.N2))
    meadow = meadow * (0.98 + 0.04 * F.N4)[:, None]
    forest = _mixa(g_dark, leaf_dark, 0.35) * 0.85
    forest = _mixa(forest, dirt * 0.6, 0.3 * _ss(0.0, 0.6, F.N2))
    c = _mixa(meadow, _mixa(g_mid, g_dark, 0.6), _ss(-3.0, 2.0, F.CSD))
    c = _mixa(c, forest, _ss(0.0, 6.0, F.CSD))

    # bare rock colour on the steep terrace slope between cliff rocks
    rk = np.clip(F.rock_band * _ss(0.6, 1.2, F.S), 0.0, 1.0)
    c = _mixa(c, rock_dark * 0.8, rk * 0.85)

    # dirt / mud ring at the waterline, broken up by noise
    damp = (1.0 - _ss(0.6, 2.0, F.PSD)) * _pond_zone(F)
    c = _mixa(c, _mixa(g_dark, mud, 0.25) * 0.9, 0.55 * damp)
    ring = (1.0 - _ss(0.2, 0.85, F.PSD)) * np.clip(0.65 + 0.45 * F.N2 + 0.2 * F.N4, 0.0, 1.0)
    shore = _mixa(_mixa(dirt, mud, 0.5) * 0.85, mud * 0.7, _ss(0.5, 0.05, F.PSD))
    c = _mixa(c, shore, ring)

    # pond bed, darker with depth (only the pond basin holds water)
    depth = (C.WATER_Z - F.H) * _pond_zone(F)
    bed_deep = mud * 0.32 + np.array([0.0, 0.006, 0.008])
    bed = _mixa(mud * 0.75, bed_deep, _ss(0.0, 0.55, depth))
    c = _mixa(c, bed, _ss(-0.03, 0.06, depth))
    return np.concatenate([c, np.ones((len(c), 1))], axis=1)


def _pond_zone(F):
    """1 inside / near the pond basin, 0 elsewhere (terrain hollows in the forest stay dry)."""
    return 1.0 - _ss(1.0, 2.5, F.PSD)


def _house_local(X, Y):
    c, s = math.cos(-C.HOUSE_ROT_Z), math.sin(-C.HOUSE_ROT_Z)
    dx, dy = X - C.HOUSE_POS.x, Y - C.HOUSE_POS.y
    return c * dx - s * dy, s * dx + c * dy


def _grass_mask(F):
    m = np.ones_like(F.H)
    m *= 1.0 - 0.82 * _ss(0.0, 5.0, F.CSD)          # forest floor: sparse
    m *= 1.0 - 0.6 * _ss(8.0, 18.0, F.CSD)          # deep forest: sparser still
    wet = _ss(C.WATER_Z + 0.05, C.WATER_Z + 0.3, F.H)
    m *= 1.0 - _pond_zone(F) * (1.0 - wet)           # none under the water
    m *= 0.45 + 0.55 * _ss(0.3, 1.0, F.PSD)          # thinner on the muddy shore
    lx, ly = _house_local(F.X, F.Y)
    d = np.maximum(np.abs(lx) - (C.HOUSE_SIZE[0] * 0.5 + 0.3), np.abs(ly) - (C.HOUSE_SIZE[1] * 0.5 + 0.3))
    m *= _ss(0.05, 0.25, d)
    m *= 1.0 - F.rock_cover
    m *= 1.0 - 0.9 * np.clip(F.rock_band * _ss(0.6, 1.2, F.S), 0.0, 1.0)
    return np.clip(m, 0.0, 1.0)


def _flower_mask(F, grass_mask):
    # broad, loosely clustered zones at low density: separate little sprays dotted over the meadow, as in
    # the reference, rather than dense carpets (which read as brown smudges from the main camera)
    f = _ss(-0.15, 0.3, F.N3) * np.clip(0.5 + 0.7 * F.N2 + 0.3 * F.N4, 0.15, 1.0)
    f = np.maximum(f, F.boulder_ring)
    f *= _ss(-0.5, -3.0, F.CSD)                         # meadow only
    f *= _ss(0.45, 0.85, grass_mask)                    # nothing in pond / rocks / house
    f *= _ss(3.4, 4.8, np.hypot(F.X - C.HOUSE_POS.x, F.Y - C.HOUSE_POS.y))
    f *= _ss(1.0, 2.0, F.PSD)
    return np.clip(f, 0.0, 1.0)


# ---------------------------------------------------------------- rocks
ROCK_TONES = (("rock", 0.5), ("rock_dark", 0.25), ("rock_light", 0.25))
ROCK_WARM = "#b8976c"      # sandstone tint mixed into every rock so the band reads warm beige, not grey


def _pick_tone(rng):
    r = rng.random()
    name = "rock"
    for n, w in ROCK_TONES:
        if r < w:
            name = n
            break
        r -= w
    return C.mix(C.color(name), C.color(ROCK_WARM), rng.uniform(0.4, 0.7))


def _cut(pts, n, d):
    """Clip points into the half-space p.n <= d by projecting outside points onto the plane."""
    n = n.normalized()
    for p in pts:
        e = p.dot(n) - d
        if e > 0.0:
            p.x, p.y, p.z = p.x - n.x * e, p.y - n.y * e, p.z - n.z * e


def _dir(theta, phi):
    return Vector((math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi), math.cos(theta)))


def _carve(pts, rng, style):
    """Random planar cuts: slanted tops and broken vertical sides, so blocks read as split rock, not boxes."""
    U = rng.uniform
    if style == "slab":          # cliff column: one slanted top, one or two split sides
        _cut(pts, _dir(U(0.25, 0.6), U(0, TAU)), U(0.55, 0.85))
        for _ in range(rng.choice((1, 2, 2))):
            _cut(pts, _dir(U(1.35, 1.75), U(0, TAU)), U(0.55, 0.85))
    elif style == "shard":       # meadow boulder: leaning wedge / pointed top
        phi = U(0, TAU)
        _cut(pts, _dir(U(0.6, 0.95), phi), U(0.15, 0.45))
        if rng.random() < 0.6:
            _cut(pts, _dir(U(0.5, 0.9), phi + math.pi + U(-0.6, 0.6)), U(0.4, 0.7))
        _cut(pts, _dir(U(1.3, 1.8), U(0, TAU)), U(0.5, 0.8))
    elif style == "stone":       # small pebble-ish stone: a few soft cuts
        for _ in range(rng.choice((2, 3))):
            _cut(pts, _dir(U(0.4, 1.6), U(0, TAU)), U(0.55, 0.85))
    # re-normalise height to -1..1 so `top` targets stay exact after the cuts
    zs = [p.z for p in pts]
    lo, hi = min(zs), max(zs)
    for p in pts:
        p.z = (p.z - lo) / max(hi - lo, 1e-6) * 2.0 - 1.0


def _rock_points(rng, chamfer, taper, top_clip=1.0, jitter=0.06, shear=0.12, style="block"):
    """Unit box (-1..1) with every corner chipped off by an irregular amount, carved by a few random planes,
    slightly sheared, tapered and jittered. Its convex hull is a chunky hand-chiselled rock."""
    pts = []
    for cx in (-1.0, 1.0):
        for cy in (-1.0, 1.0):
            for cz in (-1.0, 1.0):
                corner = (cx, cy, cz)
                amt = [rng.uniform(0.04, chamfer) for _ in range(3)]
                if rng.random() < 0.3:
                    amt[rng.randrange(3)] = rng.uniform(chamfer, chamfer * 2.0)
                for k in range(3):
                    p = list(corner)
                    p[k] -= corner[k] * 2.0 * min(amt[k], 0.48)
                    pts.append(Vector([c + rng.uniform(-jitter, jitter) for c in p]))
    _carve(pts, rng, style)
    sx, sy = rng.uniform(-shear, shear), rng.uniform(-shear, shear)
    for p in pts:
        p.z = min(p.z, top_clip)
        f = 1.0 - taper * (p.z + 1.0) * 0.5
        p.x = p.x * f + sx * p.z
        p.y = p.y * f + sy * p.z
    return pts


def _hull(points):
    bm = bmesh.new()
    for p in points:
        bm.verts.new(p)
    res = bmesh.ops.convex_hull(bm, input=list(bm.verts), use_existing_faces=False)
    junk = list({g for g in res["geom_interior"] + res["geom_unused"] if isinstance(g, bmesh.types.BMVert)})
    if junk:
        bmesh.ops.delete(bm, geom=junk, context="VERTS")
    bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(5.0), verts=list(bm.verts), edges=list(bm.edges))
    bm.verts.index_update()
    verts = [tuple(v.co) for v in bm.verts]
    faces = [tuple(v.index for v in f.verts) for f in bm.faces]
    bm.free()
    return verts, faces


def _footprint_min_height(x, y, hx, hy, rot):
    c, s = math.cos(rot), math.sin(rot)
    hmin = 1e9
    for u in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for v in (-1.0, -0.5, 0.0, 0.5, 1.0):
            lx, ly = u * hx, v * hy
            hmin = min(hmin, C.terrain_height(x + c * lx - s * ly, y + s * lx + c * ly))
    return hmin


def _add_rock(mb, rng, x, y, size, rot, tilt=(0.0, 0.0), bury=0.2, top=None, chamfer=0.3, taper=0.15,
              top_clip=1.0, moss=0.0, base=None, shade=0.85, kind="rock", style="block", crevice=None):
    """Faceted convex rock planted in the terrain. If `top` is given, the height is chosen so the rock
    reaches that world Z while its bottom stays `bury` below the lowest ground under its footprint.
    `crevice` (a world angle) darkens faces turned along it: the joints between neighbouring cliff slabs."""
    sx, sy, sz = size
    hmin = _footprint_min_height(x, y, sx * 0.5, sy * 0.5, rot)
    bottom = hmin - bury
    if top is not None:
        sz = max(0.4, top - bottom)
    unit = _rock_points(rng, chamfer, taper, top_clip, style=style)
    pts = [Vector((p.x * sx * 0.5, p.y * sy * 0.5, p.z * sz * 0.5)) for p in unit]
    R = Matrix.Rotation(rot, 4, "Z") @ Euler((tilt[0], tilt[1], 0.0), "XYZ").to_matrix().to_4x4()
    zs = [(R @ p).z for p in pts]
    z = bottom - min(zs)
    M = Matrix.Translation((x, y, z)) @ R
    verts, faces = _hull(pts)
    base = C.jitter(_scale(base if base is not None else _pick_tone(rng), shade), rng, 0.07)
    light, dark = C.color("rock_light"), C.color("rock_dark")
    moss_col = C.mix(C.color("grass_mid"), C.color("grass_light"), 0.3)
    cdir = Vector((math.cos(crevice), math.sin(crevice), 0.0)) if crevice is not None else None
    bv, fs = mb.geometry(verts, faces, matrix=M, col=base)
    # contact shade: darker where the rock meets the ground, so it reads planted rather than placed
    contact = {}
    for v in bv:
        g = C.terrain_height(v.co.x, v.co.y)
        contact[v] = 0.6 + 0.42 * C.smoothstep(g - 0.05, g + min(0.9, 0.45 * sz), v.co.z)
    for f in fs:
        f.normal_update()
        n = f.normal
        nz = n.z
        col = base
        if nz > 0.5:
            col = C.mix(col, _scale(light, shade), 0.35 * (nz - 0.5) / 0.5)
            if moss > 0.0 and nz > 0.82:
                col = C.mix(col, moss_col, moss)
        elif nz < -0.2:
            col = C.mix(col, _scale(dark, 0.8), 0.5)
        if cdir is not None and abs(nz) < 0.55 and abs(n.dot(cdir)) > 0.55:
            col = C.mix(col, _scale(dark, 0.55), 0.55)
        col = C.jitter(col, rng, 0.07)
        for loop in f.loops:
            k = contact[loop.vert]
            loop[mb.col] = (col[0] * k, col[1] * k, col[2] * k, 1.0)
    return {"x": x, "y": y, "rot": rot, "hx": sx * 0.5, "hy": sy * 0.5, "top": z + max(zs), "kind": kind}


def _soften(ob, width, levels=1):
    """Bevel (one segment, as a support loop) + one Catmull-Clark level: flat faces, pillowy edges."""
    bev = ob.modifiers.new("Bevel", "BEVEL")
    bev.width = width
    bev.segments = 1
    bev.limit_method = "ANGLE"
    bev.angle_limit = math.radians(25.0)
    sub = ob.modifiers.new("Soften", "SUBSURF")
    sub.levels = levels
    sub.render_levels = levels
    return sub


def _cliff(mb_big, mb_small, rng):
    """Rock band along the terrace edge: a layered wall behind the pond, broken segments elsewhere."""
    U = rng.uniform
    rocks = []
    x = -36.0
    while x < 30.0:
        e = C.terrace_edge_y(x)
        tangent = math.atan((C.terrace_edge_y(x + 0.2) - C.terrace_edge_y(x - 0.2)) / 0.4)
        wall = -21.0 < x < -1.5
        forest = C.clearing_signed_distance(x, e) > 2.5
        seg = C.n2(x / 4.5, 0.5, 71.0)
        if not (wall or seg > (0.0 if not forest else 0.2)):
            x += U(0.8, 2.0)
            continue
        w = U(1.1, 2.0)
        h_up = C.terrain_height(x, e + 1.3)
        # back row: tall split columns whose tops sit around the upper meadow's lip (a few stand proud)
        if wall:
            top = h_up + (U(0.45, 1.0) if rng.random() < 0.18 else U(-0.35, 0.3))
        else:
            top = h_up + U(-0.8, 0.2)
        rocks.append(_add_rock(mb_big, rng, x, e - 0.15 + U(-0.25, 0.2), (w, U(1.7, 2.4), 2.0),
                               tangent + U(-0.25, 0.25), (U(-0.2, -0.05), U(-0.1, 0.1)), bury=0.35, top=top,
                               chamfer=U(0.12, 0.28), taper=U(0.05, 0.2),
                               moss=0.45 if rng.random() < 0.4 else 0.0, shade=0.95, kind="cliff",
                               style="slab", crevice=tangent))
        # front row: lower leaning slabs stepping down to the meadow / water
        if (wall and rng.random() < 0.6) or rng.random() < 0.3:
            fx = x + w * 0.45 * U(0.6, 1.2)
            fe = C.terrace_edge_y(fx)
            ftop = C.terrain_height(fx, fe - 1.3) + U(0.7, 1.6)
            fw = U(0.9, 1.7)
            rocks.append(_add_rock(mb_big, rng, fx, fe - 1.0 + U(-0.2, 0.2), (fw, U(1.1, 1.6), 1.0),
                                   tangent + U(-0.45, 0.45), (U(-0.15, 0.05), U(-0.12, 0.12)), bury=0.3,
                                   top=ftop, chamfer=U(0.18, 0.34), taper=U(0.1, 0.25),
                                   moss=0.35 if rng.random() < 0.3 else 0.0, shade=0.9, kind="cliff",
                                   style="slab", crevice=tangent))
        # talus at the foot of the wall
        if rng.random() < (0.55 if wall else 0.35):
            s = U(0.45, 1.0)
            tx, ty = x + U(-0.8, 0.8), e - 1.7 - U(0.0, 0.8)
            rocks.append(_add_rock(mb_small, rng, tx, ty, (s * U(1.0, 1.5), s, s * U(0.6, 0.95)), U(0, TAU),
                                   (U(-0.2, 0.2), U(-0.2, 0.2)), bury=s * 0.25, chamfer=U(0.3, 0.45),
                                   taper=0.15, shade=0.86, kind="talus", style="stone"))
        # occasional cap stone on the upper rim
        if rng.random() < 0.25:
            s = U(0.5, 1.0)
            rocks.append(_add_rock(mb_small, rng, x + U(-0.5, 0.5), e + 1.5 + U(0.0, 0.8), (s * 1.3, s, s * 0.7),
                                   U(0, TAU), (0.0, 0.0), bury=s * 0.35, chamfer=U(0.3, 0.45),
                                   moss=0.4, shade=0.85, kind="cap", style="stone"))
        x += w * (U(0.55, 0.75) if wall else U(0.6, 0.85))
    return rocks


def _meadow_boulders(mb, rng):
    """A few clusters of chunky boulders in the meadow (a big one plus small companions)."""
    U = rng.uniform
    rocks = []
    anchors = [(6.5, 4.0), (9.0, 0.5), (4.5, -6.0), (-6.0, -6.5), (2.5, 7.5), (11.0, 6.5), (-13.0, -5.0)]
    for (ax, ay) in anchors:
        ax += U(-1.0, 1.0)
        ay += U(-1.0, 1.0)
        n = rng.choice((1, 2, 2, 3))
        for i in range(n):
            if i == 0:
                s = U(0.8, 1.35)
                px, py = ax, ay
            else:
                s = U(0.35, 0.7)
                a = U(0, TAU)
                px, py = ax + math.cos(a) * U(0.9, 1.5), ay + math.sin(a) * U(0.9, 1.5)
            if C.house_distance(px, py) < 5.0 or C.pond_signed_distance(px, py) < 1.5:
                continue
            main = i == 0
            rocks.append(_add_rock(mb, rng, px, py,
                                   (s * U(1.1, 1.6), s * U(0.7, 1.0), s * (U(0.9, 1.3) if main else U(0.6, 0.9))),
                                   U(0, TAU), (U(-0.3, 0.3), U(-0.3, 0.3)), bury=s * 0.35,
                                   chamfer=U(0.2, 0.4), taper=U(0.1, 0.35),
                                   moss=0.3 if rng.random() < 0.3 else 0.0, shade=0.95, kind="boulder",
                                   style="shard" if main or rng.random() < 0.5 else "stone"))
    return rocks


def _in_arcs(angle, arcs):
    a = angle % TAU
    return any(lo <= a <= hi for (lo, hi), _ in arcs)


def _pond_stones(mb, rng):
    U = rng.uniform
    rocks = []
    tries = 0
    grey = C.mix(C.color("rock_dark"), C.color("stone_light"), 0.3)
    while len(rocks) < 34 and tries < 400:
        tries += 1
        ang = U(0, TAU)
        if _in_arcs(ang, REED_ARCS) and rng.random() < 0.75:
            continue
        r = C.pond_radius_at(ang) + U(-0.3, 0.55)
        px, py = C.POND_CENTER.x + math.cos(ang) * r, C.POND_CENTER.y + math.sin(ang) * r
        if py > C.terrace_edge_y(px) - 1.9:     # the cliff already lines the north shore
            continue
        for i in range(rng.choice((1, 1, 2, 3))):
            s = U(0.25, 0.6) if i == 0 else U(0.15, 0.35)
            ox = px + (U(-0.45, 0.45) if i else 0.0)
            oy = py + (U(-0.45, 0.45) if i else 0.0)
            wet = C.terrain_height(ox, oy) < C.WATER_Z + 0.05
            base = C.mix(grey, C.color(ROCK_WARM), U(0.2, 0.7))
            rocks.append(_add_rock(mb, rng, ox, oy, (s * U(1.0, 1.4), s, s * U(0.5, 0.8)), U(0, TAU),
                                   (U(-0.2, 0.2), U(-0.2, 0.2)), bury=s * 0.25, chamfer=U(0.35, 0.48),
                                   taper=0.2, base=base, shade=0.7 if wet else 0.88,
                                   kind="stone", style="stone"))
    return rocks


def _apply_rock_masks(F, rocks):
    for r in rocks:
        c, s = math.cos(-r["rot"]), math.sin(-r["rot"])
        reach = max(r["hx"], r["hy"]) * 1.8 + 0.6
        sel = np.nonzero((np.abs(F.X - r["x"]) < reach) & (np.abs(F.Y - r["y"]) < reach))[0]
        if sel.size == 0:
            continue
        dx, dy = F.X[sel] - r["x"], F.Y[sel] - r["y"]
        lx, ly = c * dx - s * dy, s * dx + c * dy
        dn = np.hypot(lx / r["hx"], ly / r["hy"])
        above = (F.H[sel] < r["top"] - 0.05).astype(float)
        cover = (1.0 - _ss(0.82, 1.08, dn)) * above
        F.rock_cover[sel] = np.maximum(F.rock_cover[sel], cover)
        if r["kind"] in ("cliff", "talus"):
            F.rock_band[sel] = np.maximum(F.rock_band[sel], (1.0 - _ss(1.0, 1.6, dn)) * above)
        if r["kind"] == "boulder":
            ring = _ss(2.6, 1.2, dn) * (1.0 - _ss(0.9, 1.1, dn)) * 0.9
            F.boulder_ring[sel] = np.maximum(F.boulder_ring[sel], ring)


# ---------------------------------------------------------------- water, lily pads, reeds
def _water_material():
    mat = bpy.data.materials.get("M_Water") or bpy.data.materials.new("M_Water")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)
    bsdf = N.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    vc = N.new("ShaderNodeVertexColor")
    vc.layer_name = "Col"
    vc.location = (-300, 150)
    L.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(vc.outputs["Alpha"], bsdf.inputs["Alpha"])
    bsdf.inputs["Roughness"].default_value = 0.06
    bsdf.inputs["IOR"].default_value = 1.33
    bsdf.inputs["Specular IOR Level"].default_value = 1.0
    tc = N.new("ShaderNodeTexCoord")
    tc.location = (-700, -200)
    noise = N.new("ShaderNodeTexNoise")
    noise.location = (-450, -200)
    noise.inputs["Scale"].default_value = 1.4
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.55
    L.new(tc.outputs["Object"], noise.inputs["Vector"])
    bump = N.new("ShaderNodeBump")
    bump.location = (-150, -200)
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.02
    L.new(noise.outputs["Factor"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    L.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    mat.diffuse_color = C.color("water_deep")
    return mat


def _water(coll):
    n_ang = 128
    fracs = (0.2, 0.4, 0.6, 0.75, 0.87, 0.95, 1.0)
    extra = (0.35, 0.7, 1.0)
    verts = [(C.POND_CENTER.x, C.POND_CENTER.y, C.WATER_Z)]
    rings = len(fracs) + len(extra)
    for k in range(n_ang):
        a = TAU * k / n_ang
        R = C.pond_radius_at(a)
        radii = [R * f for f in fracs] + [R + e for e in extra]
        for r in radii:
            verts.append((C.POND_CENTER.x + math.cos(a) * r, C.POND_CENTER.y + math.sin(a) * r, C.WATER_Z))
    faces = []

    def vid(k, r):
        return 1 + (k % n_ang) * rings + r

    for k in range(n_ang):
        faces.append((0, vid(k, 0), vid(k + 1, 0)))
        for r in range(rings - 1):
            faces.append((vid(k, r), vid(k, r + 1), vid(k + 1, r + 1), vid(k + 1, r)))
    shallow, deep = np.array(C.color("water_shallow")), np.array(C.color("water_deep"))
    cols = []
    for (x, y, _) in verts:
        depth = C.WATER_Z - C.terrain_height(x, y)
        t = C.smoothstep(0.05, 0.6, depth)
        c = (shallow + (deep - shallow) * t) * 0.6
        c[3] = C.lerp(0.45, 0.94, C.smoothstep(0.0, 0.45, depth))
        cols.append(c)
    me = _mesh_from_arrays("PondWater", verts, faces)
    _set_point_color(me, np.array(cols))
    me.materials.append(_water_material())
    me.shade_smooth()
    ob = bpy.data.objects.new("PondWater", me)
    coll.objects.link(ob)
    return ob


def _lily_pad(mb, rng, x, y, r, rot, col, lift=0.0):
    n = 14
    notch = rng.uniform(0.35, 0.6)
    z = C.WATER_Z + 0.012 + lift
    th = 0.014
    top = [(0.0, 0.0, 0.006)]
    for i in range(n + 1):
        a = notch * 0.5 + (TAU - notch) * i / n
        rr = r * (1.0 + rng.uniform(-0.04, 0.04))
        top.append((math.cos(a) * rr, math.sin(a) * rr, 0.0))
    verts = top + [(v[0], v[1], v[2] - th) for v in top]
    m = len(top)
    faces = [(0, i, i + 1) for i in range(1, n + 1)]
    side = [(i, i + 1) for i in range(1, n + 1)] + [(0, 1), (n + 1, 0)]
    side_faces = [(b, a, a + m, b + m) for a, b in side]
    M = Matrix.Translation((x, y, z)) @ Matrix.Rotation(rot, 4, "Z")
    mb.geometry(verts, faces, matrix=M, col=col)
    mb.geometry(verts, side_faces, matrix=M, col=_scale(col, 0.6))


def _lily_flower(mb, rng, x, y, col, raise_z=0.0):
    z = C.WATER_Z + 0.02 + raise_z
    n = 7
    rot = rng.uniform(0, TAU)
    for layer, (length, lift, width) in enumerate(((0.11, 0.45, 0.05), (0.08, 0.95, 0.04))):
        for i in range(n):
            a = rot + TAU * (i + 0.5 * layer) / n
            ca, sa = math.cos(a), math.sin(a)
            tip = (ca * length * math.cos(lift), sa * length * math.cos(lift), length * math.sin(lift))
            px, py = -sa * width * 0.5, ca * width * 0.5
            mid = (ca * length * 0.45 * math.cos(lift), sa * length * 0.45 * math.cos(lift), length * 0.45 * math.sin(lift))
            verts = [(0.0, 0.0, 0.0), (mid[0] + px, mid[1] + py, mid[2]), tip, (mid[0] - px, mid[1] - py, mid[2])]
            c = col if layer == 0 else C.mix(col, C.color("flower_white"), 0.5)
            mb.geometry(verts, [(0, 1, 2, 3)], matrix=Matrix.Translation((x, y, z + 0.004 * layer)),
                        col=C.jitter(c, rng, 0.05))
    mb.box((x, y, z + 0.03), (0.045, 0.045, 0.035), (0.0, 0.0, rot), col=C.color("flower_yellow"))


def _lily_pads(coll, rng, mat):
    mb = C.MeshBuilder()
    placed = []
    pad_cols = [C.color("leaf_mid"), C.color("leaf_teal"), C.color("grass_mid"), C.mix(C.color("leaf_mid"), C.color("leaf_dark"), 0.5)]
    n_flowers = 0
    for (ox, oy, n) in LILY_CLUSTERS:
        cx, cy = C.POND_CENTER.x + ox, C.POND_CENTER.y + oy
        tries = 0
        made = 0
        # pads crowd together in overlapping rafts of mixed sizes (big leaves with small new ones between)
        n = int(n * 1.6)
        while made < n and tries < 120:
            tries += 1
            r = 0.1 + 0.24 * rng.random() ** 1.6
            a = rng.uniform(0, TAU)
            d = 1.15 * rng.random() ** 0.8
            x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
            if C.pond_signed_distance(x, y) > -0.5 - r:
                continue
            if any(math.hypot(x - px, y - py) < (r + pr) * 0.68 for px, py, pr in placed):
                continue
            placed.append((x, y, r))
            col = C.jitter(C.mix(pad_cols[rng.randrange(4)], C.color("grass_light"), rng.uniform(0.0, 0.2)), rng, 0.08)
            _lily_pad(mb, rng, x, y, r, rng.uniform(0, TAU), col, lift=0.004 * (len(placed) % 4))
            made += 1
            if rng.random() < 0.22:
                fc = C.color("flower_pink") if rng.random() < 0.5 else C.color("flower_white")
                _lily_flower(mb, rng, x + rng.uniform(-0.04, 0.04), y + rng.uniform(-0.04, 0.04), fc,
                             raise_z=0.004 * (len(placed) % 4))
                n_flowers += 1
    ob = mb.to_object("LilyPads", coll, [mat], smooth=False)
    return ob, len(placed), n_flowers


def _reed_blade(mb, bx, by, bz, height, lean_dir, lean, width, root_col, tip_col):
    segs = 4
    ca, sa = math.cos(lean_dir), math.sin(lean_dir)
    px, py = -sa, ca
    verts, cols = [], []
    for k in range(segs + 1):
        t = k / segs
        off = lean * height * t * t
        cx, cy, cz = bx + ca * off, by + sa * off, bz + height * t * (1.0 - 0.1 * lean * t)
        col = C.mix(root_col, tip_col, t)
        if k < segs:
            hw = width * 0.5 * (1.0 - 0.75 * t)
            verts += [(cx - px * hw, cy - py * hw, cz), (cx + px * hw, cy + py * hw, cz)]
            cols += [col, col]
        else:
            verts.append((cx, cy, cz))
            cols.append(col)
    faces = [(2 * k, 2 * k + 1, 2 * k + 3, 2 * k + 2) for k in range(segs - 1)]
    faces.append((2 * (segs - 1), 2 * (segs - 1) + 1, 2 * segs))
    mb.geometry(verts, faces, vert_cols=cols)


def _prism(mb, p0, p1, r0, r1, sides, col, rot=0.0):
    """Closed n-sided prism / frustum between two points (axis assumed near vertical)."""
    verts = []
    for (p, r) in ((p0, r0), (p1, r1)):
        for i in range(sides):
            a = rot + TAU * i / sides
            verts.append((p[0] + math.cos(a) * r, p[1] + math.sin(a) * r, p[2]))
    faces = [(i, (i + 1) % sides, sides + (i + 1) % sides, sides + i) for i in range(sides)]
    faces.append(tuple(range(sides - 1, -1, -1)))
    faces.append(tuple(sides + i for i in range(sides)))
    mb.geometry(verts, faces, col=col)


def _reeds(coll, rng, mat):
    mb = C.MeshBuilder()
    clumps = 0
    root_c = C.mix(C.color("leaf_dark"), C.color("mud"), 0.3)
    for (lo, hi), count in REED_ARCS:
        for i in range(count):
            ang = lo + (hi - lo) * (i + rng.uniform(0.1, 0.9)) / count
            r = C.pond_radius_at(ang) + rng.uniform(-0.6, 0.15)
            x, y = C.POND_CENTER.x + math.cos(ang) * r, C.POND_CENTER.y + math.sin(ang) * r
            ground = C.terrain_height(x, y)
            water_off = max(0.0, C.WATER_Z - ground)
            scale = rng.uniform(0.75, 1.15)
            tip_c = C.jitter(C.mix(C.color("grass_mid"), C.color("leaf_light"), rng.uniform(0.1, 0.5)), rng, 0.08)
            for _ in range(rng.randint(8, 15)):
                a = rng.uniform(0, TAU)
                d = rng.uniform(0.0, 0.16)
                bx, by = x + math.cos(a) * d, y + math.sin(a) * d
                h = scale * rng.uniform(0.55, 1.1) + water_off
                _reed_blade(mb, bx, by, ground - 0.05, h, a + rng.uniform(-0.5, 0.5), rng.uniform(0.1, 0.4),
                            rng.uniform(0.035, 0.055), root_c, tip_c)
            for _ in range(rng.randint(1, 3)):
                a = rng.uniform(0, TAU)
                d = rng.uniform(0.0, 0.1)
                bx, by = x + math.cos(a) * d, y + math.sin(a) * d
                top = ground + water_off + scale * rng.uniform(0.95, 1.3)
                stalk_c = C.jitter(C.mix(C.color("grass_mid"), C.color("leaf_dark"), 0.4), rng, 0.06)
                _prism(mb, (bx, by, ground - 0.05), (bx, by, top), 0.009, 0.007, 4, stalk_c)
                head_c = C.jitter(C.color("#6e4a2e"), rng, 0.1)
                _prism(mb, (bx, by, top - 0.24), (bx, by, top - 0.07), 0.034, 0.032, 6, head_c, rng.uniform(0, 1))
                _prism(mb, (bx, by, top - 0.07), (bx, by, top + 0.05), 0.006, 0.003, 4, C.color("#b8a77a"))
            clumps += 1
    ob = mb.to_object("Reeds", coll, [mat], smooth=False)
    return ob, clumps


# ---------------------------------------------------------------- ground cover prototypes
def _blade(coll, name, height, width, bend, twist, mat):
    ts = (0.0, 0.38, 0.72, 1.0)
    ws = (1.0, 0.82, 0.5, 0.0)
    verts, tv = [], []
    for k, t in enumerate(ts):
        z = height * t * (1.0 - 0.18 * bend * t)
        yo = bend * height * t * t
        if k < 3:
            a = twist * t
            hw = width * ws[k] * 0.5
            dx, dy = hw * math.cos(a), hw * math.sin(a)
            verts += [(-dx, yo - dy, z), (dx, yo + dy, z)]
            tv += [t, t]
        else:
            verts.append((0.0, yo, z))
            tv.append(t)
    faces = [(0, 1, 3, 2), (2, 3, 5, 4), (4, 5, 6)]
    me = _mesh_from_arrays(name, verts, faces)
    _set_point_float(me, "blade_t", tv)
    me.materials.append(mat)
    me.shade_smooth()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _flower_head(mb, rng, cx, cy, cz, n, length, width, lift, petal_col, centre_col, centre=0.022):
    rot = rng.uniform(0, TAU)
    for i in range(n):
        a = rot + TAU * i / n
        ca, sa = math.cos(a), math.sin(a)
        cl = math.cos(lift)
        tip = (ca * length * cl, sa * length * cl, length * math.sin(lift))
        mid = (ca * length * 0.5 * cl, sa * length * 0.5 * cl, length * 0.5 * math.sin(lift))
        px, py = -sa * width * 0.5, ca * width * 0.5
        verts = [(0.0, 0.0, 0.0), (mid[0] + px, mid[1] + py, mid[2]), tip, (mid[0] - px, mid[1] - py, mid[2])]
        mb.geometry(verts, [(0, 1, 2, 3)], matrix=Matrix.Translation((cx, cy, cz)), col=C.jitter(petal_col, rng, 0.05))
    mb.box((cx, cy, cz + centre * 0.35), (centre, centre, centre * 0.8), (0.0, 0.0, rot), col=centre_col)


def _flower_protos(coll, mat):
    rng = C.rng(901)
    stem_c = C.mix(C.color("grass_mid"), C.color("leaf_dark"), 0.35)
    leaf_c = C.color("grass_mid")
    yellow = C.mix(C.color("flower_yellow"), C.color("#fff35a"), 0.7)   # reads golden, not orange, in warm sun
    white, purple = C.color("flower_white"), C.color("flower_purple")
    orange = C.color("#d9a21e")
    protos = []

    def spread(n, radius, h0, h1):
        """n stem positions in a loose small cluster; heads sit just above the grass tips."""
        out = []
        for k in range(n):
            a = TAU * k / n + rng.uniform(-0.5, 0.5)
            d = 0.0 if k == 0 else radius * rng.uniform(0.45, 1.0)
            out.append((math.cos(a) * d, math.sin(a) * d, rng.uniform(h0, h1)))
        return out

    # 0: yellow buttercup cluster (six heads spread over ~0.35 m, read as a dotted yellow spray)
    mb = C.MeshBuilder()
    for (ox, oy, h) in spread(6, 0.17, 0.33, 0.45):
        _prism(mb, (ox, oy, -0.02), (ox, oy, h), 0.007, 0.006, 4, stem_c)
        _flower_head(mb, rng, ox, oy, h, 5, 0.085, 0.075, 0.12, yellow, orange, centre=0.024)
    for k in range(3):
        a = TAU * k / 3 + 0.4
        mb.geometry([(0, 0, 0.01), (math.cos(a + 0.3) * 0.06, math.sin(a + 0.3) * 0.06, 0.06),
                     (math.cos(a) * 0.13, math.sin(a) * 0.13, 0.09), (math.cos(a - 0.3) * 0.06, math.sin(a - 0.3) * 0.06, 0.06)],
                    [(0, 1, 2, 3)], col=leaf_c)
    protos.append(mb.to_object("FlowerProto_0_Yellow", coll, [mat]))

    # 1: white daisy cluster
    mb = C.MeshBuilder()
    for (ox, oy, h) in spread(4, 0.14, 0.32, 0.44):
        _prism(mb, (ox, oy, -0.02), (ox, oy, h), 0.007, 0.006, 4, stem_c)
        _flower_head(mb, rng, ox, oy, h, 9, 0.062, 0.028, 0.1, white, yellow, centre=0.03)
    protos.append(mb.to_object("FlowerProto_1_White", coll, [mat]))

    # 2: purple spike
    mb = C.MeshBuilder()
    _prism(mb, (0.0, 0.0, -0.02), (0.0, 0.0, 0.62), 0.009, 0.007, 4, stem_c)
    for k in range(7):
        z = 0.38 + k * 0.04
        s = 0.06 - k * 0.006
        col = C.jitter(C.mix(purple, C.color("#c7a8f0"), k / 9.0), rng, 0.05)
        mb.box((0.0, 0.0, z), (s, s, 0.035), (0.0, 0.0, k * 0.5), col=col)
    for k in range(4):
        a = TAU * k / 4
        mb.geometry([(0, 0, 0.02), (math.cos(a + 0.25) * 0.06, math.sin(a + 0.25) * 0.06, 0.08),
                     (math.cos(a) * 0.14, math.sin(a) * 0.14, 0.1), (math.cos(a - 0.25) * 0.06, math.sin(a - 0.25) * 0.06, 0.08)],
                    [(0, 1, 2, 3)], col=leaf_c)
    protos.append(mb.to_object("FlowerProto_2_Purple", coll, [mat]))
    return protos


def _grass_material():
    mat = bpy.data.materials.get("M_Grass") or bpy.data.materials.new("M_Grass")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    def math_node(op, a=None, b=None, loc=(0, 0)):
        n = N.new("ShaderNodeMath")
        n.operation = op
        n.location = loc
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    def mix_rgb(a, b, fac, loc=(0, 0)):
        n = N.new("ShaderNodeMix")
        n.data_type = "RGBA"
        n.location = loc
        for sock, v in ((n.inputs[6], a), (n.inputs[7], b), (n.inputs[0], fac)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            elif isinstance(v, tuple):
                sock.default_value = v
            else:
                L.new(v, sock)
        return n.outputs[2]

    ground = N.new("ShaderNodeAttribute")
    ground.attribute_type = "INSTANCER"
    ground.attribute_name = "ground_col"
    ground.location = (-1200, 200)
    tattr = N.new("ShaderNodeAttribute")
    tattr.attribute_type = "GEOMETRY"
    tattr.attribute_name = "blade_t"
    tattr.location = (-1200, -100)
    info = N.new("ShaderNodeObjectInfo")
    info.location = (-1200, -350)
    t = tattr.outputs["Factor"]
    rnd = info.outputs["Random"]

    # low-contrast gradient and small per-blade jitter: the meadow should read as a soft carpet whose
    # variation is large-scale (from the ground colour), not as per-blade speckle
    root = mix_rgb(ground.outputs["Color"], C.color("grass_dark"), 0.35, (-900, 300))
    tip = mix_rgb(ground.outputs["Color"], C.color(GRASS_TIP), 0.38, (-900, 50))
    t1 = math_node("MULTIPLY", t, 2.0, (-900, -150))
    c1 = mix_rgb(root, ground.outputs["Color"], math_node("MINIMUM", t1, 1.0, (-750, -150)), (-550, 250))
    t2 = math_node("POWER", t, 1.4, (-750, -300))
    c2 = mix_rgb(c1, tip, t2, (-350, 150))

    hue = math_node("MULTIPLY_ADD", rnd, 0.04, (-750, -450))
    hue.node.inputs[2].default_value = 0.48
    r2 = math_node("FRACT", math_node("MULTIPLY", rnd, 17.31, (-900, -600)), None, (-750, -600))
    val = math_node("MULTIPLY_ADD", r2, 0.1, (-550, -600))
    val.node.inputs[2].default_value = 0.84
    r3 = math_node("FRACT", math_node("MULTIPLY", rnd, 71.7, (-900, -750)), None, (-750, -750))
    sat = math_node("MULTIPLY_ADD", r3, 0.1, (-550, -750))
    sat.node.inputs[2].default_value = 0.86
    hsv = N.new("ShaderNodeHueSaturation")
    hsv.location = (-150, 0)
    L.new(hue, hsv.inputs["Hue"])
    L.new(sat, hsv.inputs["Saturation"])
    L.new(val, hsv.inputs["Value"])
    L.new(c2, hsv.inputs["Color"])

    # Thin-leaf shading: a matte diffuse lobe ADDED to an equally strong translucent lobe, both on the true
    # blade normal. Cycles lets reflected light reach only the side of a blade the light is on and
    # transmitted light only the far side, so whichever side the camera sees, the blade answers |N.L|: no
    # dark back faces, no per-blade speckle and no dark/pale swathes where the combed blades turn one side to
    # the camera. The sky reaches both lobes too, so the carpet is lit about as brightly as open ground.
    bsdf = N.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (100, 100)
    bsdf.inputs["Roughness"].default_value = 0.9
    bsdf.inputs["Specular IOR Level"].default_value = 0.0   # matte: no grazing-angle sheen on the blades
    L.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
    tr = N.new("ShaderNodeBsdfTranslucent")
    tr.location = (100, -200)
    L.new(mix_rgb(hsv.outputs["Color"], C.color("#d6e27a"), 0.15, (-100, -250)), tr.inputs["Color"])
    add = N.new("ShaderNodeAddShader")
    add.location = (350, 0)
    L.new(bsdf.outputs["BSDF"], add.inputs[0])
    L.new(tr.outputs["BSDF"], add.inputs[1])
    out = N.new("ShaderNodeOutputMaterial")
    out.location = (550, 0)
    L.new(add.outputs["Shader"], out.inputs["Surface"])
    mat.diffuse_color = C.color("grass_mid")
    return mat


# ---------------------------------------------------------------- geometry nodes
class _GN:
    def __init__(self, name):
        ng = bpy.data.node_groups.get(name)
        if ng is not None:
            bpy.data.node_groups.remove(ng)
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        self.ng = ng
        self.gin = ng.nodes.new("NodeGroupInput")
        self.gout = ng.nodes.new("NodeGroupOutput")

    def node(self, kind, **props):
        n = self.ng.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def link(self, a, b):
        self.ng.links.new(a, b)

    def named(self, name, data_type="FLOAT"):
        n = self.node("GeometryNodeInputNamedAttribute", data_type=data_type)
        n.inputs["Name"].default_value = name
        return n.outputs["Attribute"]

    def math(self, op, a, b=None):
        n = self.node("ShaderNodeMath", operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                self.link(v, n.inputs[i])
        return n.outputs[0]

    def rand(self, data_type, lo, hi, seed):
        n = self.node("FunctionNodeRandomValue", data_type=data_type)
        n.inputs[0].default_value = lo
        n.inputs[1].default_value = hi
        n.inputs["Seed"].default_value = seed
        return n.outputs[0]

    def map_range(self, v, a, b, c, d, clamp=True):
        n = self.node("ShaderNodeMapRange")
        n.clamp = clamp
        self.link(v, n.inputs[0])
        n.inputs[1].default_value, n.inputs[2].default_value = a, b
        n.inputs[3].default_value, n.inputs[4].default_value = c, d
        return n.outputs[0]

    def scatter(self, mask_name, density, seed):
        dist = self.node("GeometryNodeDistributePointsOnFaces", distribute_method="RANDOM")
        self.link(self.gin.outputs[0], dist.inputs["Mesh"])
        self.link(self.math("MULTIPLY", self.named(mask_name), density), dist.inputs["Density"])
        dist.inputs["Seed"].default_value = seed
        store = self.node("GeometryNodeStoreNamedAttribute", data_type="FLOAT_COLOR", domain="POINT")
        store.inputs["Name"].default_value = "ground_col"
        self.link(dist.outputs["Points"], store.inputs["Geometry"])
        self.link(self.named("Col", "FLOAT_COLOR"), store.inputs["Value"])
        return store.outputs["Geometry"]

    def instance(self, points, proto_coll, index, rotation, scale):
        ci = self.node("GeometryNodeCollectionInfo", transform_space="ORIGINAL")
        ci.inputs["Collection"].default_value = proto_coll
        ci.inputs["Separate Children"].default_value = True
        ci.inputs["Reset Children"].default_value = True
        iop = self.node("GeometryNodeInstanceOnPoints")
        self.link(points, iop.inputs["Points"])
        self.link(ci.outputs[0], iop.inputs["Instance"])
        iop.inputs["Pick Instance"].default_value = True
        self.link(index, iop.inputs["Instance Index"])
        self.link(rotation, iop.inputs["Rotation"])
        self.link(scale, iop.inputs["Scale"])
        self.link(iop.outputs[0], self.gout.inputs[0])

    def euler(self, vec):
        n = self.node("FunctionNodeEulerToRotation")
        self.link(vec, n.inputs[0])
        return n.outputs[0]

    def combine(self, x, y, z):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.link(v, n.inputs[i])
        return n.outputs[0]

    def noise(self, scale, seed_w=0.0):
        n = self.node("ShaderNodeTexNoise")
        n.noise_dimensions = "4D"
        n.inputs["Scale"].default_value = scale
        n.inputs["W"].default_value = seed_w
        n.inputs["Detail"].default_value = 1.5
        return n.outputs["Factor"]


def _grass_gn(proto_coll, n_protos):
    g = _GN("GN_LandscapeGrass")
    pts = g.scatter("grass_mask", GRASS_DENSITY, 17)
    idx = g.rand("INT", 0, n_protos - 1, 3)
    # combed meadow: blades lean along a slowly turning direction field (+ jitter), so the grass reads in
    # soft brush-stroke swathes instead of uniform random fuzz. Blades bend towards local +Y; a negative
    # X tilt leans them further that way.
    heading = g.map_range(g.noise(0.05, 3.3), 0.3, 0.7, 0.0, TAU * 1.5, clamp=False)
    rz = g.math("ADD", heading, g.rand("FLOAT", -1.5, 1.5, 7))
    rx = g.rand("FLOAT", -0.25, 0.12, 5)
    ry = g.rand("FLOAT", -0.18, 0.18, 6)
    rot = g.euler(g.combine(rx, ry, rz))
    s = g.rand("FLOAT", 0.78, 1.15, 9)
    s = g.math("MULTIPLY", s, g.map_range(g.noise(0.22, 1.7), 0.3, 0.7, 0.78, 1.15))
    s = g.math("MULTIPLY", s, g.map_range(g.named("grass_mask"), 0.0, 1.0, 0.7, 1.0))
    sz = g.math("MULTIPLY", s, g.rand("FLOAT", 0.85, 1.12, 13))
    g.instance(pts, proto_coll, idx, rot, g.combine(s, s, sz))
    return g.ng


def _flower_gn(proto_coll):
    g = _GN("GN_LandscapeFlowers")
    pts = g.scatter("flower_mask", FLOWER_DENSITY, 29)
    r = g.rand("FLOAT", 0.0, 1.0, 31)
    idx = g.math("ADD", g.math("GREATER_THAN", r, 0.74), g.math("GREATER_THAN", r, 0.91))
    rot = g.euler(g.rand("FLOAT_VECTOR", (-0.16, -0.16, 0.0), (0.16, 0.16, TAU), 37))
    s = g.rand("FLOAT", 0.8, 1.2, 41)
    g.instance(pts, proto_coll, idx, rot, g.combine(s, s, s))
    return g.ng


def _instance_stats(ob):
    """(instance count, instanced triangles) of a Geometry Nodes instancer, evaluated."""
    deps = bpy.context.evaluated_depsgraph_get()
    gs = ob.evaluated_get(deps).evaluated_geometry()
    pc = gs.instances_pointcloud()
    refs = gs.instance_references()
    count = len(pc.points) if pc is not None else 0
    if count == 0:
        return 0, 0
    idx = np.zeros(count, dtype=np.int32)
    pc.attributes[".reference_index"].data.foreach_get("value", idx)
    per_ref = []
    for ref in refs:
        data = getattr(ref, "data", None)
        per_ref.append(sum(len(p.vertices) - 2 for p in data.polygons) if isinstance(data, bpy.types.Mesh) else 0)
    return count, int(np.bincount(idx, minlength=len(refs)).dot(np.array(per_ref)))


# ---------------------------------------------------------------- build
def build():
    t0 = time.time()
    root = C.collection("Landscape")
    c_terrain = C.collection("Terrain", parent=root)
    c_water = C.collection("Water", parent=root)
    c_rocks = C.collection("Rocks", parent=root)
    c_cover = C.collection("GroundCover", parent=root)
    c_protos = C.collection("GroundCover_Protos", parent=c_cover)
    c_blades = C.collection("GroundCover_Blades", parent=c_protos)
    c_flowers = C.collection("GroundCover_FlowerHeads", parent=c_protos)

    m_ground = C.material("M_Ground", use_vcol=True, roughness=0.95, specular=0.3)
    m_rock = C.material("M_Rock", use_vcol=True, roughness=0.88, specular=0.35)
    m_lily = C.material("M_LilyPad", use_vcol=True, roughness=0.42, specular=0.5, translucency=0.1)
    m_reed = C.material("M_Reeds", use_vcol=True, roughness=0.7, specular=0.35, translucency=0.25)
    m_flower = C.material("M_Flower", use_vcol=True, roughness=0.6, specular=0.3, translucency=0.2)
    m_grass = _grass_material()

    # fields + rocks (rocks feed the masks)
    F = Fields()
    rng = C.rng(1201)
    mb_cliff, mb_talus = C.MeshBuilder(), C.MeshBuilder()
    cliff = _cliff(mb_cliff, mb_talus, rng)
    mb_boulder = C.MeshBuilder()
    boulders = _meadow_boulders(mb_boulder, C.rng(1301))
    stones = _pond_stones(mb_talus, C.rng(1401))
    _apply_rock_masks(F, cliff + boulders + stones)

    ob_cliff = mb_cliff.to_object("Rocks_Cliff", c_rocks, [m_rock], smooth=True)
    _soften(ob_cliff, 0.22)
    ob_boulders = mb_boulder.to_object("Rocks_Boulders", c_rocks, [m_rock], smooth=True)
    _soften(ob_boulders, 0.12)
    ob_stones = mb_talus.to_object("Rocks_Stones", c_rocks, [m_rock], smooth=True)
    _soften(ob_stones, 0.08)

    # terrain
    faces = F.faces()
    verts = np.stack([F.X, F.Y, F.H], axis=1)
    me = _mesh_from_arrays("Terrain", verts, faces.tolist())
    ground_cols = _ground_colors(F)
    gm = _grass_mask(F)
    fm = _flower_mask(F, gm)
    # the blades get thin-leaf lighting (brighter than flat ground), so the ground under the grass is lifted a
    # little to match: gaps where the grass thins (shore, rock edges, forest margin) do not read as dark holes
    terrain_cols = ground_cols.copy()
    terrain_cols[:, :3] *= (1.0 + 0.12 * gm)[:, None]
    _set_point_color(me, terrain_cols)
    me.materials.append(m_ground)
    me.shade_smooth()
    ob_terrain = bpy.data.objects.new("Terrain", me)
    c_terrain.objects.link(ob_terrain)

    # water, pads, reeds
    ob_water = _water(c_water)
    ob_lily, n_pads, n_lily_flowers = _lily_pads(c_water, C.rng(1501), m_lily)
    ob_reeds, n_clumps = _reeds(c_water, C.rng(1601), m_reed)

    # ground cover
    blade_specs = ((0.30, 0.058, 0.4, 0.3), (0.26, 0.064, 0.25, -0.4), (0.34, 0.052, 0.55, 0.6),
                   (0.24, 0.066, 0.2, 0.0), (0.31, 0.056, 0.48, -0.7))
    blades = [_blade(c_blades, f"GrassBlade_{i}", *spec, m_grass) for i, spec in enumerate(blade_specs)]
    _flower_protos(c_flowers, m_flower)
    _exclude_from_view_layers(c_protos)

    cover_me = me.copy()
    cover_me.name = "GroundCoverBase"
    cover_me.color_attributes["Col"].data.foreach_set("color", ground_cols.astype(np.float32).ravel())
    _set_point_float(cover_me, "grass_mask", gm)
    _set_point_float(cover_me, "flower_mask", fm)
    ob_grass = bpy.data.objects.new("Grass", cover_me)
    c_cover.objects.link(ob_grass)
    ob_grass.modifiers.new("GrassScatter", "NODES").node_group = _grass_gn(c_blades, len(blades))
    # Blades do not cast shadows (they still receive them): soft carpet look, root darkening comes
    # from the colour gradient instead of noisy blade-on-blade shadowing.
    ob_grass.visible_shadow = False
    ob_flowers = bpy.data.objects.new("Flowers", cover_me)
    c_cover.objects.link(ob_flowers)
    ob_flowers.modifiers.new("FlowerScatter", "NODES").node_group = _flower_gn(c_flowers)

    t_py = time.time() - t0
    tris = {ob.name: C.triangle_count(ob) for ob in (ob_terrain, ob_water, ob_lily, ob_reeds,
                                                       ob_cliff, ob_boulders, ob_stones)}
    grass_n, grass_tris = _instance_stats(ob_grass)
    flower_n, flower_tris = _instance_stats(ob_flowers)
    return {
        "collection": "Landscape",
        "objects": [ob_terrain.name, ob_water.name, ob_lily.name, ob_reeds.name, ob_cliff.name,
                    ob_boulders.name, ob_stones.name, ob_grass.name, ob_flowers.name],
        "triangles_mesh": tris,
        "triangles_mesh_total": sum(tris.values()),
        "instances": {"grass_blades": grass_n, "flowers": flower_n},
        "triangles_instanced": {"grass": grass_tris, "flowers": flower_tris},
        "counts": {"cliff_rocks": len(cliff), "boulders": len(boulders), "pond_stones": len(stones),
                   "lily_pads": n_pads, "lily_flowers": n_lily_flowers, "reed_clumps": n_clumps,
                   "terrain_verts": len(F.H)},
        "build_s_python": round(t_py, 2),
        "build_s_total": round(time.time() - t0, 2),
        "notes": "Grass/Flowers are GN instancers on a copy of the terrain mesh (attributes grass_mask, "
                 "flower_mask, Col); blade/flower sources live in the excluded GroundCover_Protos collection.",
    }
