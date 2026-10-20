"""Cottage: half-timbered plank house with a steep shingle roof, stone chimney, dormer, porch roof,
hanging lantern, crooked fence, ivy and flowering shrubs, in a chunky hand-made diorama style.

Everything is built in the house local frame (origin at ground level in the footprint centre, ridge along
local X, front long side facing local -Y, chimney at the -X end, door and porch on the +X gable) and placed
with common.house_matrix(). Pieces are merged into a few objects per material group; colour comes from the
per-corner 'Col' attribute.
"""
import math
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

import common

C = common.color

# ---------------------------------------------------------------- dimensions (house local frame, metres)
HX = common.HOUSE_SIZE[0] / 2.0          # 2.5
HY = common.HOUSE_SIZE[1] / 2.0          # 2.1
T_OUT = 0.12                             # timber outer face, measured from a wall's reference plane
PLANE_X = HX - T_OUT                     # reference plane of the gable walls
PLANE_Y = HY - T_OUT                     # reference plane of the long walls
FOUND_TOP = 0.42
SILL = (FOUND_TOP, FOUND_TOP + 0.16)
MID = (2.42, 2.58)
TOP = (3.55, 3.75)
WALL_TOP = TOP[1]
PITCH = math.radians(55.0)
TAN, COS, SIN = math.tan(PITCH), math.cos(PITCH), math.sin(PITCH)
ROOF_Y0 = HY + 0.02                      # roof deck underside passes through (±ROOF_Y0, WALL_TOP)
RIDGE_Z = WALL_TOP + ROOF_Y0 * TAN       # deck underside at the ridge
EAVE_RUN = HY + 0.36                     # horizontal half run of the roof including the eave overhang
GABLE_X = HX + 0.36                      # roof half length including the gable overhang
DECK_T = 0.06

# depth of each timber class (outer face); different depths avoid coplanar overlapping faces
O_POST, O_BEAM, O_STUD, O_BRACE, O_RAKE = 0.12, 0.115, 0.108, 0.098, 0.126
O_BACK = -0.1

CH_X, CH_Y = -1.72, -1.3                 # chimney centre (front slope, near the -X gable)
CH_HX, CH_HY = 0.4, 0.36
CH_BASE, CH_TOP = 4.1, 7.15

DX, DW, DY_F = 0.45, 0.62, -1.8          # dormer centre x, half width, front face y (low on the slope)
D_PITCH = math.radians(38.0)
D_TAN = math.tan(D_PITCH)

PORCH_YC = -0.2                          # lean-to porch roof spans most of the +X gable
PORCH_HALF = 1.5
PORCH_TOP = 3.92
PORCH_PITCH = math.radians(33.0)
PORCH_RUN = 1.35

LANTERN_POST = (3.3, -1.95)


def roof_under(y):
    return RIDGE_Z - abs(y) * TAN


def deck_top(y):
    return roof_under(y) + DECK_T / COS


DZ_B = deck_top(DY_F)                    # main roof surface at the dormer front
DZ_E = DZ_B + 0.93                       # dormer eave
DR_RUN = DW + 0.02
DZ_R = DZ_E + DR_RUN * D_TAN             # dormer deck underside at its ridge

# ---------------------------------------------------------------- colours
TIMBER = C("timber")
PLANK = C("wall_plank")
PLANK_LIGHT = C("#7e8352")
WIN_FRAME = C("#565c39")
SHUTTER = C("#5a6439")
DOOR = C("#7a6a4c")
CORE = C("#2c2b22")
POST_WOOD = C("#a39577")
FENCE = C("#6a6450")
STEP = C("#6e5b45")
METAL = C("#3d3836")
LATTICE = C("#7c6f72")
LANTERN = C("#8a6f78")
CHIMNEY = common.mix(C("stone_dark"), C("#4d5852"), 0.5)


def timber_col(r):
    c = TIMBER
    if r.random() < 0.3:
        c = common.mix(c, C("#4f4632"), r.uniform(0.2, 0.6))
    return common.jitter(c, r, 0.1)


def plank_col(r, z):
    c = PLANK
    t = r.random()
    if t < 0.25:
        c = common.mix(c, TIMBER, r.uniform(0.15, 0.45))
    elif t < 0.5:
        c = common.mix(c, PLANK_LIGHT, r.uniform(0.2, 0.7))
    c = common.jitter(c, r, 0.07)
    ao = 0.78 + 0.22 * common.smoothstep(0.3, 1.6, z)
    return (c[0] * ao, c[1] * ao, c[2] * ao, 1.0)


def roof_col(r):
    c = C("roof")
    t = r.random()
    if t < 0.3:
        c = common.mix(c, C("roof_dark"), r.uniform(0.25, 0.8))
    elif t < 0.55:
        c = common.mix(c, C("roof_light"), r.uniform(0.25, 0.8))
    return common.jitter(c, r, 0.07)


def stone_col(r, base=None):
    if base is None:
        base = C(r.choice(("stone", "stone", "stone_light", "stone_dark")))
    return common.jitter(common.mix(base, C("stone"), r.uniform(0.0, 0.4)), r, 0.1)


# ---------------------------------------------------------------- geometry helpers
def frame(origin, ax, ay, az):
    """4x4 matrix whose columns are the given axes, translated to origin."""
    m = Matrix.Identity(4)
    for i in range(3):
        m[i][0], m[i][1], m[i][2], m[i][3] = ax[i], ay[i], az[i], origin[i]
    return m


def clean_poly(poly, eps=1e-4):
    out = []
    for p in poly:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


def poly_area(poly):
    n = len(poly)
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))


def clip_half(poly, a, b, c):
    """Keep the part of a convex polygon where a*x + b*y <= c."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp = a * p[0] + b * p[1] - c
        fq = a * q[0] + b * q[1] - c
        if fp <= 0:
            out.append(p)
        if (fp < 0 < fq) or (fq < 0 < fp):
            t = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def clip_convex(poly, region):
    if poly_area(region) < 0:
        region = region[::-1]
    for i in range(len(region)):
        p, q = region[i], region[(i + 1) % len(region)]
        ex, ey = q[0] - p[0], q[1] - p[1]
        poly = clip_half(poly, ey, -ex, ey * p[0] - ex * p[1])
        if len(poly) < 3:
            return []
    return poly


def rect(a0, a1, z0, z1):
    return [(a0, z0), (a1, z0), (a1, z1), (a0, z1)]


def prism(mb, poly, z0, z1, matrix, col, mat=0):
    """Extrude a convex polygon (x, y) between local z0..z1, then transform by matrix."""
    poly = clean_poly(poly)
    if len(poly) < 3:
        return False
    area = poly_area(poly)
    if abs(area) < 2e-4:
        return False
    if area < 0:
        poly = poly[::-1]
    n = len(poly)
    verts = [(x, y, z0) for x, y in poly] + [(x, y, z1) for x, y in poly]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    mb.geometry(verts, faces, matrix=matrix, col=col, mat=mat)
    return True


def beam(mb, p, q, w, h, col, extend=0.0, mat=0, twist=0.0):
    """Box of section w x h running from p to q (local frame)."""
    p, q = Vector(p), Vector(q)
    d = q - p
    length = d.length + 2 * extend
    z = d.normalized()
    hint = Vector((0, 0, 1)) if abs(z.z) < 0.95 else Vector((1, 0, 0))
    x = hint.cross(z).normalized()
    y = z.cross(x)
    rot = Matrix((x, y, z)).transposed()
    if twist:
        rot = rot @ Matrix.Rotation(twist, 3, "Z")
    mb.box((p + q) / 2, (w, h, length), rot.to_euler("XYZ"), col, mat)


def pyramid(mb, center, half, height, col, matrix=None, mat=0):
    cx, cy, cz = center
    verts = [(cx - half, cy - half, cz), (cx + half, cy - half, cz), (cx + half, cy + half, cz),
             (cx - half, cy + half, cz), (cx, cy, cz + height)]
    faces = [(3, 2, 1, 0), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    mb.geometry(verts, faces, matrix=matrix, col=col, mat=mat)


_ICO = {}


def ico_template(subdiv):
    if subdiv not in _ICO:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=1.0)
        verts = [tuple(v.co) for v in bm.verts]
        bm.verts.index_update()
        faces = [tuple(v.index for v in f.verts) for f in bm.faces]
        bm.free()
        _ICO[subdiv] = (verts, faces)
    return _ICO[subdiv]


def ground_z(x, y):
    """Local z of the terrain at local (x, y)."""
    w = common.house_matrix() @ Vector((x, y, 0.0))
    return common.terrain_height(w.x, w.y) - common.HOUSE_GROUND


# ---------------------------------------------------------------- walls
SIDES = {
    "front": dict(O=(0, -1, 0), A=(1, 0, 0), plane=PLANE_Y, inner=HX - 0.24, gable=False,
                  studs=[-1.55, -1.0, 0.0, 1.25],
                  braces=[(0, -2.26, -1.625, 1), (0, 1.325, 2.26, -1), (1, -2.26, -1.625, -1),
                          (1, 1.325, 2.26, 1)],
                  windows=[dict(a0=-0.86, a1=-0.14, z0=1.36, z1=2.34, shutters=True, mullion=True)],
                  door=None),
    "back": dict(O=(0, 1, 0), A=(-1, 0, 0), plane=PLANE_Y, inner=HX - 0.24, gable=False,
                 studs=[-1.4, -0.35, 0.65, 1.5],
                 braces=[(0, -2.26, -1.475, 1), (0, 1.575, 2.26, -1)],
                 windows=[dict(a0=-0.2, a1=0.5, z0=1.36, z1=2.3, shutters=False, mullion=True)],
                 door=None),
    "east": dict(O=(1, 0, 0), A=(0, 1, 0), plane=PLANE_X, inner=HY - 0.24, gable=True,
                 studs=[-1.3, -0.8, 0.5, 1.25],
                 braces=[(0, -1.86, -1.375, 1), (0, 1.325, 1.86, -1)],
                 windows=[dict(a0=-0.27, a1=0.27, z0=4.5, z1=5.15, shutters=False, mullion=False)],
                 door=dict(a0=-0.65, a1=0.35)),
    "west": dict(O=(-1, 0, 0), A=(0, -1, 0), plane=PLANE_X, inner=HY - 0.24, gable=True,
                 studs=[-1.25, -0.35, 0.35, 1.25],
                 braces=[(0, -1.86, -1.325, 1), (0, 1.325, 1.86, -1), (1, -1.86, -1.325, -1),
                         (1, 1.325, 1.86, 1)],
                 windows=[dict(a0=-0.22, a1=0.22, z0=1.4, z1=2.3, shutters=False, mullion=False),
                          dict(a0=-0.27, a1=0.27, z0=4.5, z1=5.15, shutters=False, mullion=False)],
                 door=None),
}


def wall_frame(sd):
    O = Vector(sd["O"])
    return frame(O * sd["plane"], Vector(sd["A"]), Vector((0, 0, 1)), O)


def timber(mb, W, a0, a1, z0, z1, outer, r, col=None):
    o = outer + r.uniform(-0.003, 0.003)
    lean = r.uniform(-0.006, 0.006) if (z1 - z0) > (a1 - a0) else 0.0     # hand-built posts are never plumb
    mb.box(((a0 + a1) / 2, (z0 + z1) / 2, (o + O_BACK) / 2), (a1 - a0, z1 - z0, o - O_BACK), (0, 0, lean),
           col or timber_col(r), matrix=W)


def diag_timber(mb, W, p, q, width, outer, r, extend=0.06):
    da, dz = q[0] - p[0], q[1] - p[1]
    length = math.hypot(da, dz) + 2 * extend
    ang = math.atan2(dz, da)
    o = outer + r.uniform(-0.003, 0.003)
    mb.box(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (o + O_BACK) / 2), (length, width, o - O_BACK - 0.02),
           (0, 0, ang + r.uniform(-0.012, 0.012)), timber_col(r), matrix=W)


def plank_fill(mb, W, region, holes, r, z_from, z_to, row_h=0.19, overlap=0.035, thick=0.034, col_fn=None):
    """Horizontal overlapping clapboards inside a convex region (a, z) of a wall frame, skipping holes."""
    n = 0
    z = z_from
    while z < z_to - 0.03:
        h = row_h * r.uniform(0.88, 1.12)
        zb, zt = z - overlap, min(z + h, z_to)
        pieces = []
        piece = clip_convex(rect(-50, 50, zb, zt), region)
        if piece:
            pieces.append(piece)
        for (h0, h1, g0, g1) in holes:
            if zt > g0 + 0.01 and zb < g1 - 0.01:
                split = []
                for p in pieces:
                    left = clip_half(p, 1, 0, h0)
                    right = clip_half(p, -1, 0, -h1)
                    mid = clip_half(clip_half(p, -1, 0, -h0), 1, 0, h1)
                    below = clip_half(mid, 0, 1, g0) if len(mid) >= 3 else []
                    above = clip_half(mid, 0, -1, -g1) if len(mid) >= 3 else []
                    split += [x for x in (left, right, below, above) if len(x) >= 3]
                pieces = split
        for p in pieces:
            p = clean_poly(p)
            if len(p) < 3:
                continue
            xs = [v[0] for v in p]
            zs = [v[1] for v in p]
            if max(xs) - min(xs) < 0.06 or max(zs) - min(zs) < 0.025:
                continue
            t = thick * r.uniform(0.85, 1.15)
            H = zt - zb
            phi = math.atan2(t, H) + r.uniform(-0.012, 0.012)
            zc = (zb + zt) / 2
            oc = (H / 2) * math.sin(phi) + (t / 2) * math.cos(phi) + r.uniform(-0.003, 0.005)
            M = W @ Matrix.Translation((0, zc, oc)) @ Matrix.Rotation(-phi, 4, "X") @ Matrix.Translation((0, -zc, 0))
            col = col_fn(r, zc) if col_fn else plank_col(r, zc)
            if prism(mb, p, -t / 2, t / 2, M, col):
                n += 1
        z += h
    return n


def lattice(mb, W, a0, a1, z0, z1, o, step=0.15, k=1.45, bar=0.018):
    am, zm = (a0 + a1) / 2, (z0 + z1) / 2
    col = LATTICE
    count = 0
    for fam, oo in ((1, o), (-1, o + 0.006)):
        c0 = zm - fam * k * am
        for j in range(-12, 13):
            c = c0 + j * step * k
            # line z = fam*k*a + c
            lo = (z0 - c) / (fam * k)
            hi = (z1 - c) / (fam * k)
            lo, hi = min(lo, hi), max(lo, hi)
            s, e = max(a0, lo), min(a1, hi)
            if e - s < 0.01:
                continue
            p = (s, fam * k * s + c)
            q = (e, fam * k * e + c)
            ln = math.hypot(q[0] - p[0], q[1] - p[1])
            ang = math.atan2(q[1] - p[1], q[0] - p[0])
            mb.box(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, oo), (ln, bar, 0.014), (0, 0, ang), col, matrix=W)
            count += 1
    return count


def shutter(P, W, hinge_a, side, z0, height, width, r):
    gamma = -side * 0.32
    M = W @ Matrix.Translation((hinge_a, z0, 0.14)) @ Matrix.Rotation(gamma, 4, "Y")
    n = 3
    pw = width / n
    col = common.jitter(SHUTTER, r, 0.05)
    for i in range(n):
        x = side * (i + 0.5) * pw
        P["planks"].box((x, height / 2 + r.uniform(-0.012, 0.012), 0), (pw - 0.014, height * r.uniform(0.98, 1.0), 0.035),
                        (0, 0, r.uniform(-0.01, 0.01)), common.jitter(col, r, 0.06), matrix=M)
    bc = common.mix(col, TIMBER, 0.5)
    for yy in (0.18 * height, 0.82 * height):
        P["planks"].box((side * width / 2, yy, 0.033), (width - 0.05, 0.085, 0.03), (0, 0, 0), common.jitter(bc, r, 0.05),
                        matrix=M)
        P["metal"].box((side * 0.12, yy, 0.052), (0.24, 0.035, 0.012), (0, 0, 0), METAL, matrix=M)
    p = (side * 0.04, 0.18 * height + 0.04)
    q = (side * (width - 0.04), 0.82 * height - 0.04)
    ln = math.hypot(q[0] - p[0], q[1] - p[1])
    P["planks"].box(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, 0.03), (ln, 0.075, 0.026),
                    (0, 0, math.atan2(q[1] - p[1], q[0] - p[0])), common.jitter(bc, r, 0.05), matrix=M)
    return n + 3


def window(P, W, w, r, counts, fw=0.07):
    a0, a1, z0, z1 = w["a0"], w["a1"], w["z0"], w["z1"]
    of, ob = 0.095, -0.085
    fc = lambda: common.jitter(WIN_FRAME, r, 0.06)  # noqa: E731
    fr = P["frame"]
    for a in (a0 - fw / 2, a1 + fw / 2):
        fr.box((a, (z0 + z1) / 2, (of + ob) / 2), (fw, z1 - z0 + 2 * fw, of - ob), (0, 0, 0), fc(), matrix=W)
    for z in (z0 - fw / 2, z1 + fw / 2):
        fr.box(((a0 + a1) / 2, z, (of + ob) / 2 - 0.004), (a1 - a0, fw, of - ob), (0, 0, 0), fc(), matrix=W)
    fr.box(((a0 + a1) / 2, z0 - fw - 0.03, 0.065), (a1 - a0 + 2 * fw + 0.16, 0.065, 0.31),
           (r.uniform(-0.03, 0.0), 0, r.uniform(-0.01, 0.01)), fc(), matrix=W)
    P["glass"].box(((a0 + a1) / 2, (z0 + z1) / 2, -0.058), (a1 - a0 + 0.02, z1 - z0 + 0.02, 0.02), (0, 0, 0),
                   C("glass"), matrix=W)
    counts["lattice_bars"] = counts.get("lattice_bars", 0) + lattice(P["metal"], W, a0, a1, z0, z1, -0.04)
    if w.get("mullion"):
        fr.box(((a0 + a1) / 2, (z0 + z1) / 2, -0.015), (0.05, z1 - z0, 0.07), (0, 0, 0), fc(), matrix=W)
    if w.get("shutters"):
        sw = (a1 - a0) / 2 + fw * 0.6
        counts["shutter_pieces"] = counts.get("shutter_pieces", 0) + \
            shutter(P, W, a0 - fw, -1, z0 - fw, z1 - z0 + 2 * fw, sw, r) + \
            shutter(P, W, a1 + fw, 1, z0 - fw, z1 - z0 + 2 * fw, sw, r)
    counts["windows"] = counts.get("windows", 0) + 1
    return (a0 - fw - 0.005, a1 + fw + 0.005, z0 - fw - 0.005, z1 + fw + 0.005)


def door(P, W, d, r, counts, fw=0.075):
    a0, a1 = d["a0"], d["a1"]
    zb = FOUND_TOP
    R = (a1 - a0) / 2
    ac = (a0 + a1) / 2
    zs = zb + 1.44
    region = [(a0, zb), (a1, zb), (a1, zs)]
    for i in range(1, 12):
        th = math.pi * i / 12
        region.append((ac + R * math.cos(th), zs + R * math.sin(th)))
    region.append((a0, zs))
    nb = 5
    bw = (a1 - a0) / nb
    for i in range(nb):
        b0, b1 = a0 + i * bw + 0.006, a0 + (i + 1) * bw - 0.006
        poly = clip_convex(rect(b0, b1, zb - 1, zs + 1), region)
        o0 = -0.075 + r.uniform(-0.004, 0.004)
        prism(P["planks"], poly, o0, o0 + 0.05, W, common.jitter(DOOR, r, 0.08))
    # iron straps, handle
    for z in (zb + 0.32, zb + 1.22):
        P["metal"].box((ac - 0.05, z, -0.02), (a1 - a0 - 0.14, 0.055, 0.014), (0, 0, 0), METAL, matrix=W)
        P["metal"].box((a0 + 0.06, z, -0.017), (0.08, 0.09, 0.016), (0, 0, 0), METAL, matrix=W)
    P["metal"].box((a1 - 0.14, zb + 0.98, -0.015), (0.06, 0.12, 0.02), (0, 0, 0), METAL, matrix=W)
    P["metal"].box((a1 - 0.14, zb + 0.93, 0.01), (0.025, 0.1, 0.025), (0.3, 0, 0), METAL, matrix=W)
    # frame: jambs and an arch of wedge pieces
    for a in (a0 - fw / 2, a1 + fw / 2):
        P["frame"].box((a, (zb + zs) / 2, (O_POST + O_BACK) / 2 - 0.01), (fw, zs - zb, O_POST - 0.02 - O_BACK), (0, 0, 0),
                       timber_col(r), matrix=W)
    Rq = R + fw
    segs = 8
    for i in range(segs):
        pts = []
        for th in (math.pi * i / segs, math.pi * (i + 1) / segs):
            c, s = math.cos(th), math.sin(th)
            t = min(Rq / abs(c) if abs(c) > 1e-6 else 1e9, Rq / s if s > 1e-6 else 1e9)
            pts.append(((ac + R * c, zs + R * s), (ac + t * c, zs + t * s)))
        poly = [pts[0][0], pts[1][0], pts[1][1], pts[0][1]]
        o1 = O_POST - 0.01 + r.uniform(-0.006, 0.006)
        prism(P["frame"], poly, O_BACK, o1, W, timber_col(r))
    # threshold
    P["frame"].box((ac, zb - 0.015, 0.05), (a1 - a0 + 2 * fw, 0.05, 0.3), (0, 0, 0), timber_col(r), matrix=W)
    counts["door_pieces"] = nb + segs + 7
    return (a0 - fw, a1 + fw, zb - 0.2, zs + Rq)


def build_walls(P, r, counts):
    counts.setdefault("planks", 0)
    counts.setdefault("timbers", 0)
    fr = P["frame"]
    # corner posts
    for sx in (-1, 1):
        for sy in (-1, 1):
            fr.box((sx * (HX - 0.12), sy * (HY - 0.12), (FOUND_TOP + WALL_TOP) / 2), (0.24, 0.24, WALL_TOP - FOUND_TOP),
                   (0, 0, r.uniform(-0.004, 0.004)), timber_col(r))
            counts["timbers"] += 1
    for name, sd in SIDES.items():
        W = wall_frame(sd)
        inner = sd["inner"]
        holes = []
        dhole = None
        if sd["door"]:
            dhole = door(P, W, sd["door"], r, counts)
            holes.append(dhole)
        for w in sd["windows"]:
            holes.append(window(P, W, w, r, counts))
        # horizontal beams
        if dhole:
            spans = [(-inner, dhole[0]), (dhole[1], inner)]
        else:
            spans = [(-inner, inner)]
        for a0, a1 in spans:
            timber(fr, W, a0, a1, SILL[0], SILL[1], O_BEAM, r)
        timber(fr, W, -inner, inner, TOP[0], TOP[1], O_BEAM, r)
        studs = sorted(sd["studs"])
        edges = [-inner]
        for s in studs:
            edges += [s - 0.075, s + 0.075]
        edges.append(inner)
        clear = [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]
        for a0, a1 in clear:
            timber(fr, W, a0, a1, MID[0], MID[1], O_BEAM - 0.004, r)
        for s in studs:
            timber(fr, W, s - 0.075, s + 0.075, SILL[1], TOP[0], O_STUD, r)
        counts["timbers"] += 2 + len(clear) + len(studs)
        bands = [(SILL[1], MID[0]), (MID[1], TOP[0])]
        for band, a0, a1, d in sd["braces"]:
            z0, z1 = bands[band]
            p, q = ((a0, z0), (a1, z1)) if d > 0 else ((a0, z1), (a1, z0))
            diag_timber(fr, W, p, q, 0.13, O_BRACE, r)
            counts["timbers"] += 1
        # clapboard infill
        for z0, z1 in bands:
            for a0, a1 in clear:
                region = rect(a0 - 0.035, a1 + 0.035, z0 - 0.035, z1 + 0.035)
                counts["planks"] += plank_fill(P["planks"], W, region, holes, r, z0, z1 + 0.03)
        if sd["gable"]:
            build_gable(P, W, sd, r, counts)


def build_gable(P, W, sd, r, counts):
    fr = P["frame"]
    rise = RIDGE_Z - WALL_TOP
    length = math.hypot(ROOF_Y0, rise)
    for side, outer in ((-1, O_RAKE), (1, O_RAKE + 0.006)):
        # line from (side*ROOF_Y0, WALL_TOP) to (0, RIDGE_Z); the timber hangs below it
        d = Vector((-side * ROOF_Y0, rise)).normalized()
        perp = Vector((d.y, -d.x)) if side < 0 else Vector((-d.y, d.x))
        mid = Vector((side * ROOF_Y0 / 2, (WALL_TOP + RIDGE_Z) / 2)) + perp * 0.095
        ang = math.atan2(d.y, d.x)
        fr.box((mid.x, mid.y, (outer + O_BACK) / 2), (length - 0.15, 0.19, outer - O_BACK), (0, 0, ang),
               timber_col(r), matrix=W)
    counts["timbers"] += 2
    rake_drop = 0.19 / COS
    gw = [w for w in sd["windows"] if w["z0"] > WALL_TOP]
    tri = [(-ROOF_Y0, WALL_TOP), (ROOF_Y0, WALL_TOP), (0.0, RIDGE_Z)]
    gholes = []
    if gw:
        w = gw[0]
        pa = w["a1"] + 0.07 + 0.075
        for s in (-1, 1):
            a = s * pa
            ztop = RIDGE_Z - (abs(a) - 0.07) * TAN - rake_drop + 0.05
            timber(fr, W, a - 0.07, a + 0.07, WALL_TOP, ztop, O_STUD, r)
        timber(fr, W, -pa + 0.07, pa - 0.07, w["z0"] - 0.07 - 0.14, w["z0"] - 0.07, O_BEAM, r)
        timber(fr, W, -pa + 0.07, pa - 0.07, w["z1"] + 0.07, w["z1"] + 0.07 + 0.14, O_BEAM, r)
        gholes.append((-pa + 0.07, pa - 0.07, w["z0"] - 0.21, w["z1"] + 0.21))
        counts["timbers"] += 4
        # struts toward the eaves
        for s in (-1, 1):
            p = (s * (pa + 0.07), WALL_TOP + 0.85)
            q = (s * 1.45, WALL_TOP)
            diag_timber(fr, W, q, p, 0.12, O_BRACE, r, extend=0.03)
    counts["planks"] += plank_fill(P["planks"], W, tri, gholes, r, WALL_TOP, RIDGE_Z)


def build_core(P):
    """Dark interior filler so no gap ever shows the sky through the house."""
    y = PLANE_Y - 0.09              # behind the recessed glass and door planks
    prof = [(-y, 0.15), (y, 0.15), (y, roof_under(y) + 0.03), (0.0, RIDGE_Z + 0.03), (-y, roof_under(y) + 0.03)]
    M = frame((0, 0, 0), Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0)))
    prism(P["frame"], prof, -(PLANE_X - 0.09), PLANE_X - 0.09, M, CORE)


# ---------------------------------------------------------------- foundation
def build_foundation(P, r, counts):
    mb = P["stone"]
    n = 0
    for name, sd in SIDES.items():
        W = wall_frame(sd)
        if sd["gable"]:
            lo, hi = -(PLANE_Y - 0.13), PLANE_Y - 0.13
        else:
            lo, hi = -(HX + 0.12), HX + 0.12
        for z0, z1 in ((-0.2, 0.13), (0.11, FOUND_TOP + 0.02)):
            a = lo - r.uniform(0.0, 0.25)
            while a < hi:
                ln = r.uniform(0.3, 0.6)
                s, e = max(a, lo), min(a + ln, hi)
                a += ln
                if e - s < 0.12:
                    continue
                gap = r.uniform(0.02, 0.04)
                hz = (z1 - z0) * r.uniform(0.86, 1.0)
                depth = r.uniform(0.32, 0.4)
                oc = 0.24 - depth / 2 + r.uniform(-0.025, 0.015)
                mb.box(((s + e) / 2, z0 + hz / 2 + r.uniform(-0.01, 0.01), oc), (e - s - gap, hz, depth),
                       (r.uniform(-0.04, 0.04), r.uniform(-0.04, 0.04), r.uniform(-0.05, 0.05)), stone_col(r), matrix=W)
                n += 1
    mb.box((0, 0, (-0.2 + FOUND_TOP) / 2), (2 * PLANE_X + 0.1, 2 * PLANE_Y + 0.1, FOUND_TOP + 0.2), (0, 0, 0),
           common.jitter(C("stone_dark"), r, 0.0))
    counts["foundation_stones"] = n


# ---------------------------------------------------------------- roofs
def shingles(mb, M, u0, u1, s0, s1, r, width=0.42, expo=0.27, length=0.62, thick=0.05, base_h=DECK_T,
             overhang=0.05, skip=None, mat=0):
    """Staggered rows of individual shingles on the plane of frame M (u across, s down-slope, h normal)."""
    n = 0
    k = 0
    seed = r.uniform(0.0, 100.0)
    s_butt = s1 + overhang
    while s_butt > s0 + 0.08:
        s_top = max(s0 - 0.015, s_butt - length)
        u = u0 - (0.5 * (k % 2) + r.uniform(0.0, 0.35)) * width
        while u < u1:
            w = width * r.uniform(0.85, 1.15)
            a, b = max(u, u0), min(u + w, u1)
            u += w
            if b - a < 0.09:
                continue
            gap = r.uniform(0.012, 0.03)
            wc = b - a - gap
            uc = (a + b) / 2
            sb = s_butt + 0.04 * common.n2(uc * 0.8 + seed, k * 0.55, 3.7) + r.uniform(-0.025, 0.02)
            Lc = sb - s_top
            sc = sb - Lc / 2
            if skip is not None and skip(uc, sc, wc, Lc):
                continue
            th = thick * r.uniform(0.85, 1.15)
            odd = r.random() < 0.07          # the odd slipped shingle
            lift = th * 1.7 + r.uniform(0.0, 0.015) + (0.015 if odd else 0.0)
            alpha = math.atan2(lift, Lc)
            yaw = r.uniform(-0.07, 0.07) + (r.choice((-1, 1)) * r.uniform(0.04, 0.08) if odd else 0.0)
            center = (uc, sc, base_h + th / 2 + lift / 2 + r.uniform(0.0, 0.006))
            rot = (alpha + r.uniform(-0.02, 0.02), r.uniform(-0.03, 0.03), yaw)
            mb.box(center, (wc, Lc, th), rot, roof_col(r), mat, matrix=M)
            n += 1
        s_butt -= expo * r.uniform(0.95, 1.05)
        k += 1
    return n


def ridge_tiles(mb, R, x0, x1, tan_p, r, hb, t=0.05, leg=0.32, width=0.32, fill_from=None):
    """Chunky chevron cap tiles straddling a ridge, closed over the apex.

    R: frame with x along the ridge, y across it, z up, origin on the ridge line of the deck underside; both
    slopes fall away at tan_p. hb = clearance of the tile underside above the slope planes."""
    p = math.atan(tan_p)
    c, s = math.cos(p), math.sin(p)
    a, ai = (hb + t) / c, hb / c                       # outer and inner apex heights
    M = R @ frame(Vector((0, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0)))
    n = 0
    x = x0
    while x < x1 - 0.05:
        w = min(width * r.uniform(0.88, 1.12), x1 - x + 0.02)
        poly = [(0.0, a)]
        for sd in (-1, 1):
            lg = leg * r.uniform(0.9, 1.1)
            yo, zo = lg * c, a - lg * s
            yi, zi = yo - t * s, zo - t * c
            pts = [(sd * yo, zo), (sd * yi, zi)]
            poly += pts if sd < 0 else pts[::-1]
            if sd < 0:
                poly.append((0.0, ai))
        xc, zc = x + w / 2, a - leg * s / 2
        J = (Matrix.Translation((0, zc, xc))
             @ Matrix.Rotation(r.uniform(-0.035, 0.035), 4, "Z")      # roll about the ridge
             @ Matrix.Rotation(r.uniform(-0.03, 0.03), 4, "X")
             @ Matrix.Translation((0, -zc + r.uniform(0.0, 0.012), -xc)))
        col = common.jitter(common.mix(C("roof_dark"), C("roof"), r.uniform(0.25, 0.6)), r, 0.05)
        if prism(mb, poly, x + 0.006, x + w - 0.006, M @ J, col):
            n += 1
        x += w
    # solid core under the tiles so the joints between them never show daylight
    yi = leg * 0.88 * c - t * s
    zi = a - leg * 0.88 * s - t * c - 0.012
    core = [(0.0, ai - 0.012), (-yi, zi), (-yi, 0.0), (yi, 0.0), (yi, zi)]
    prism(mb, core, x0 if fill_from is None else fill_from, x1 - 0.02, M, deck_col(r))
    return n


def slope_frame(origin, S, N):
    S, N = Vector(S).normalized(), Vector(N).normalized()
    return frame(Vector(origin), S.cross(N), S, N)


def deck_col(r):
    return common.jitter(common.mix(C("roof_dark"), CORE, 0.35), r, 0.0)


def build_main_roof(P, r, counts):
    roof = P["roof"]
    s1 = EAVE_RUN / COS
    n = 0
    yv_min = (DZ_E - RIDGE_Z - DECK_T / COS) / TAN   # valley y at the dormer eave

    def skip_front(uc, sc, wc, Lc):
        x0, x1 = -uc - wc / 2, -uc + wc / 2
        y_top, y_butt = -(sc - Lc / 2) * COS, -(sc + Lc / 2) * COS
        if (CH_X - CH_HX + 0.06 < x0 and x1 < CH_X + CH_HX - 0.06
                and CH_Y - CH_HY + 0.06 < y_butt and y_top < CH_Y + CH_HY - 0.06):
            return True
        if (DX - DW + 0.15 < x0 and x1 < DX + DW - 0.15 and DY_F + 0.15 < y_butt and y_top < yv_min - 0.1):
            return True
        return False

    for sign in (-1, 1):
        S = Vector((0, sign * COS, -SIN))
        N = Vector((0, sign * SIN, COS))
        M = slope_frame((0, 0, RIDGE_Z), S, N)
        roof.box((0, (s1 - 0.06) / 2, DECK_T / 2), (2 * GABLE_X, s1 + 0.06, DECK_T), (0, 0, 0), deck_col(r), matrix=M)
        n += shingles(roof, M, -GABLE_X, GABLE_X, 0.0, s1, r, skip=skip_front if sign < 0 else None)
        for su in (-1, 1):
            P["frame"].box((su * (GABLE_X + 0.03), (s1 - 0.04) / 2, -0.005), (0.06, s1 + 0.06, 0.19), (0, 0, 0),
                           timber_col(r), matrix=M)
        # rafter tails under the eave
        for x in [-HX + 0.12 + i * (2 * HX - 0.24) / 6 for i in range(7)]:
            P["frame"].box((x, s1 - 0.25, -0.07), (0.12, 0.42, 0.14), (0, 0, 0), timber_col(r), matrix=M)
    # ridge: a filler beam and chevron cap tiles
    roof.box((0, 0, RIDGE_Z + DECK_T / COS - 0.09), (2 * GABLE_X + 0.02, 0.22, 0.22), (math.radians(45), 0, 0),
             deck_col(r))
    R = frame(Vector((0, 0, RIDGE_Z)), Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
    n_cap = ridge_tiles(roof, R, -GABLE_X - 0.04, GABLE_X + 0.04, TAN, r, hb=DECK_T + 0.15)
    counts["shingles"] = counts.get("shingles", 0) + n
    counts["ridge_caps"] = counts.get("ridge_caps", 0) + n_cap


def build_porch(P, r, counts):
    S = Vector((math.cos(PORCH_PITCH), 0, -math.sin(PORCH_PITCH)))
    N = Vector((math.sin(PORCH_PITCH), 0, math.cos(PORCH_PITCH)))
    M = slope_frame((PLANE_X - 0.02, PORCH_YC, PORCH_TOP), S, N)
    s1 = PORCH_RUN / math.cos(PORCH_PITCH)
    hu = PORCH_HALF
    P["roof"].box((0, s1 / 2, DECK_T / 2), (2 * hu, s1, DECK_T), (0, 0, 0), deck_col(r), matrix=M)
    n = shingles(P["roof"], M, -hu, hu, 0.0, s1, r, width=0.34, expo=0.23, length=0.5, thick=0.045)
    fr = P["frame"]
    for su in (-1, 1):
        fr.box((su * (hu + 0.025), s1 / 2, -0.01), (0.05, s1 + 0.08, 0.22), (0, 0, 0), timber_col(r), matrix=M)
    fr.box((0, 0.07, DECK_T + 0.13), (2 * hu + 0.08, 0.16, 0.08), (0, 0, 0), timber_col(r), matrix=M)
    for su in (-1, 1):
        u = su * (hu - 0.15)
        fr.box((u, s1 / 2 - 0.02, -0.075), (0.11, s1 - 0.02, 0.15), (0, 0, 0), timber_col(r), matrix=M)
        y = PORCH_YC - u
        xe = PLANE_X + 0.78
        ze = PORCH_TOP - (xe - PLANE_X) * math.tan(PORCH_PITCH) - 0.15 / math.cos(PORCH_PITCH) + 0.03
        beam(fr, (PLANE_X + 0.1, y, 2.55), (xe, y, ze), 0.1, 0.1, timber_col(r), extend=0.04)
    counts["shingles"] = counts.get("shingles", 0) + n


# ---------------------------------------------------------------- dormer
def build_dormer(P, r, counts):
    fr = P["frame"]
    Wd = frame((DX, DY_F + T_OUT, 0), Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0)))
    zb = DZ_B - 0.08
    # corner posts and beams
    for a0, a1 in ((-DW, -DW + 0.13), (DW - 0.13, DW)):
        timber(fr, Wd, a0, a1, zb, DZ_E, O_POST, r)
    inner = DW - 0.13
    timber(fr, Wd, -inner, inner, zb + 0.02, DZ_B + 0.08, O_BEAM, r)
    timber(fr, Wd, -inner, inner, DZ_E - 0.1, DZ_E, O_BEAM, r)
    w = dict(a0=-0.33, a1=0.33, z0=DZ_B + 0.2, z1=DZ_E - 0.17, shutters=False, mullion=True)
    hole = window(P, Wd, w, r, counts, fw=0.06)
    region = rect(-inner - 0.03, inner + 0.03, DZ_B + 0.08 - 0.03, DZ_E - 0.1 + 0.03)
    counts["planks"] += plank_fill(P["planks"], Wd, region, [hole], r, DZ_B + 0.08, DZ_E - 0.08, row_h=0.15)
    # dormer gable
    tri = [(-DR_RUN, DZ_E), (DR_RUN, DZ_E), (0.0, DZ_R)]
    counts["planks"] += plank_fill(P["planks"], Wd, tri, [], r, DZ_E, DZ_R, row_h=0.15)
    # cheeks: clapboards on triangles that sit on the main roof
    y_c = (DZ_E + 0.09 - RIDGE_Z - DECK_T / COS) / TAN
    for s in (-1, 1):
        O = Vector((s, 0, 0))
        A = Vector((0, 0, 1)).cross(O)
        Wc = frame((DX + s * (DW - T_OUT), 0, 0), A, Vector((0, 0, 1)), O)
        y0 = DY_F + 0.1
        pts_y = [(y0, deck_top(y0) - 0.04), (y_c, DZ_E + 0.05), (y0, DZ_E + 0.05)]
        poly = [(A.y * y, z) for y, z in pts_y]
        counts["planks"] += plank_fill(P["planks"], Wc, poly, [], r, deck_top(y0) - 0.04, DZ_E + 0.05, row_h=0.15)
    # core
    prof = [(DX - DW + 0.13, DZ_B - 0.5), (DX + DW - 0.13, DZ_B - 0.5), (DX + DW - 0.13, DZ_E + 0.04),
            (DX, DZ_R - 0.02), (DX - DW + 0.13, DZ_E + 0.04)]
    Mc = frame((0, 0, 0), Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0)))
    prism(fr, prof, 0.2, -(DY_F + T_OUT + 0.09), Mc, CORE)
    # roof
    over_f = 0.12
    y_front, y_back = DY_F - over_f, -0.25
    s1 = (DR_RUN + 0.13) / math.cos(D_PITCH)
    n = 0
    for s in (-1, 1):
        S = Vector((s * math.cos(D_PITCH), 0, -math.sin(D_PITCH)))
        N = Vector((s * math.sin(D_PITCH), 0, math.cos(D_PITCH)))
        M = slope_frame((DX, 0, DZ_R), S, N)
        Uy = S.cross(N).y
        u0, u1 = sorted((Uy * y_front, Uy * y_back))
        P["roof"].box(((u0 + u1) / 2, (s1 - 0.05) / 2, DECK_T / 2), (u1 - u0, s1 + 0.05, DECK_T), (0, 0, 0),
                      deck_col(r), matrix=M)
        n += shingles(P["roof"], M, u0, u1, 0.0, s1, r, width=0.3, expo=0.19, length=0.44, thick=0.038)
        uf = Uy * y_front
        fr.box((uf - Uy * 0.022, (s1 - 0.03) / 2, 0.0), (0.045, s1 + 0.03, 0.11), (0, 0, 0), timber_col(r), matrix=M)
    # chevron cap tiles from the dormer front back to where its ridge sinks into the main roof
    hb, tc = DECK_T + 0.11, 0.045
    apex = DZ_R + (hb + tc) / math.cos(D_PITCH)
    y_meet = (RIDGE_Z + DECK_T / COS + 0.05 - apex) / TAN
    R = frame(Vector((DX, 0, DZ_R)), Vector((0, 1, 0)), Vector((-1, 0, 0)), Vector((0, 0, 1)))
    y_fill = DY_F + 0.1                  # fillers start behind the dormer gable planks
    caps = ridge_tiles(P["roof"], R, y_front - 0.03, -y_meet + 0.12, D_TAN, r, hb=hb, t=tc, leg=0.24, width=0.26,
                       fill_from=y_fill)
    P["roof"].box((DX, (y_fill + y_back) / 2, DZ_R + DECK_T / math.cos(D_PITCH) - 0.02), (0.16, y_back - y_fill, 0.16),
                  (0, math.radians(45), 0), deck_col(r))
    counts["shingles"] = counts.get("shingles", 0) + n
    counts["ridge_caps"] = counts.get("ridge_caps", 0) + caps
    # flower box under the window
    bz = DZ_B + 0.04
    by = DY_F - 0.15
    P["props"].box((DX, by, bz), (0.74, 0.2, 0.17), (0, 0, r.uniform(-0.02, 0.02)), common.jitter(C("trunk"), r, 0.05))
    for x in (-0.3, 0.3):
        P["props"].box((DX + x, by + 0.05, bz - 0.12), (0.05, 0.16, 0.12), (0.5, 0, 0), timber_col(r))
    flowerbox_plants(P, Vector((DX, by, bz + 0.085)), r, counts)


# ---------------------------------------------------------------- chimney and smoke
def build_chimney(P, r, counts):
    mb = P["chimney"]
    n = 0
    z = CH_BASE
    k = 0
    dep = 0.17
    while z < CH_TOP - 0.05:
        h = min(0.21 * r.uniform(0.9, 1.1), CH_TOP - z)
        for side in range(4):
            along_x = side in (0, 2)
            full = (k + (0 if along_x else 1)) % 2 == 0
            half_len = CH_HX if along_x else CH_HY
            span = 2 * half_len if full else 2 * half_len - 2 * dep
            a = -span / 2 - r.uniform(0.0, 0.15)
            while a < span / 2 - 0.02:
                ln = r.uniform(0.22, 0.4)
                s_, e_ = max(a, -span / 2), min(a + ln, span / 2)
                a += ln
                if e_ - s_ < 0.1:
                    continue
                c_along = (s_ + e_) / 2
                out = r.uniform(-0.012, 0.012)
                if side == 0:      # front (-y)
                    cen = (CH_X + c_along, CH_Y - CH_HY + dep / 2 - out, z + h / 2)
                    size = (e_ - s_ - 0.025, dep, h - 0.025)
                elif side == 2:    # back
                    cen = (CH_X + c_along, CH_Y + CH_HY - dep / 2 + out, z + h / 2)
                    size = (e_ - s_ - 0.025, dep, h - 0.025)
                elif side == 1:    # +x
                    cen = (CH_X + CH_HX - dep / 2 + out, CH_Y + c_along, z + h / 2)
                    size = (dep, e_ - s_ - 0.025, h - 0.025)
                else:
                    cen = (CH_X - CH_HX + dep / 2 - out, CH_Y + c_along, z + h / 2)
                    size = (dep, e_ - s_ - 0.025, h - 0.025)
                col = common.jitter(common.mix(CHIMNEY, C(r.choice(("stone_dark", "stone", "#4f5a52"))),
                                               r.uniform(0.0, 0.45)), r, 0.1)
                mb.box(cen, size, (r.uniform(-0.03, 0.03), r.uniform(-0.03, 0.03), r.uniform(-0.035, 0.035)), col)
                n += 1
        z += h
        k += 1
    mb.box((CH_X, CH_Y, (CH_BASE + CH_TOP) / 2), (2 * CH_HX - 0.12, 2 * CH_HY - 0.12, CH_TOP - CH_BASE), (0, 0, 0),
           common.mix(CHIMNEY, CORE, 0.5))
    # cap slab and top course
    mb.box((CH_X, CH_Y, CH_TOP + 0.055), (2 * CH_HX + 0.16, 2 * CH_HY + 0.16, 0.11), (0, 0, r.uniform(-0.03, 0.03)),
           common.jitter(common.mix(CHIMNEY, C("stone_light"), 0.3), r, 0.04))
    mb.box((CH_X, CH_Y, CH_TOP + 0.17), (2 * CH_HX - 0.02, 2 * CH_HY - 0.02, 0.13), (0, 0, r.uniform(-0.03, 0.03)),
           common.jitter(CHIMNEY, r, 0.04))
    mb.box((CH_X, CH_Y, CH_TOP + 0.235), (2 * CH_HX - 0.26, 2 * CH_HY - 0.26, 0.02), (0, 0, 0), (0.01, 0.01, 0.01, 1))
    counts["chimney_stones"] = n + 3


def build_smoke(P, r):
    """A plume rising ~3.8 m in gentle S-bends, drifting slightly downwind and widening from the flue; the web
    viewer sets it in motion (web/src/materials/smoke.js)."""
    start = Vector((CH_X + 0.03, CH_Y, CH_TOP + 0.12))
    height = 3.8
    ctrl = [start - Vector((0, 0, 0.3)), start.copy()]
    n_ctrl = 9
    phase = r.uniform(0.0, 2 * math.pi)
    for i in range(1, n_ctrl + 1):
        t = i / n_ctrl
        amp = 0.03 + 0.1 * t
        sway = math.sin(2 * math.pi * 1.6 * t + phase)
        ctrl.append(Vector((start.x + 0.4 * t * t + sway * amp * r.uniform(0.8, 1.0),
                            start.y - 0.15 * t + math.cos(2 * math.pi * 0.9 * t + phase) * amp * 0.45,
                            start.z + height * t + r.uniform(-0.04, 0.04))))
    ctrl.append(ctrl[-1] + (ctrl[-1] - ctrl[-2]))
    pts = []
    sub = 9
    for i in range(1, len(ctrl) - 2):
        p0, p1, p2, p3 = ctrl[i - 1], ctrl[i], ctrl[i + 1], ctrl[i + 2]
        for k in range(sub):
            t = k / sub
            t2, t3 = t * t, t * t * t
            pts.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    pts.append(ctrl[-2])
    rings, seg = len(pts) - 1, 10
    verts, faces = [], []
    for i, p in enumerate(pts):
        t = i / rings
        tan = (pts[min(i + 1, rings)] - pts[max(i - 1, 0)]).normalized()
        n1 = tan.cross(Vector((1, 0, 0))).normalized()
        n2 = tan.cross(n1)
        rad = (0.07 + 0.15 * t ** 0.8) * min(1.0, (1.0 - t) * 6.0) + 0.003
        for k in range(seg):
            a = 2 * math.pi * k / seg
            verts.append(p + (n1 * math.cos(a) + n2 * math.sin(a)) * rad)
    for i in range(rings):
        for k in range(seg):
            a = i * seg + k
            b = i * seg + (k + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    P["smoke"].geometry(verts, faces, col=(1, 1, 1, 1))


# ---------------------------------------------------------------- lantern, steps, fence
def build_lantern(P, r, counts):
    """Crooked post with a short arm toward the door and a knee brace; the lantern hangs from the arm."""
    px, py = LANTERN_POST
    pc = common.jitter(POST_WOOD, r, 0.05)
    pr = P["props"]
    post = [Vector((px, py, -0.15)), Vector((px + 0.035, py - 0.02, 0.85)), Vector((px - 0.025, py + 0.02, 1.75)),
            Vector((px + 0.015, py - 0.01, 2.82))]
    for a, b in zip(post[:-1], post[1:]):
        beam(pr, a, b, 0.13, 0.12, common.jitter(pc, r, 0.04), extend=0.04, twist=r.uniform(-0.15, 0.15))
    arm0, arm1 = Vector((px + 0.01, py - 0.08, 2.64)), Vector((px - 0.01, py + 0.8, 2.6))
    beam(pr, arm0, arm1, 0.1, 0.11, common.jitter(pc, r, 0.05), twist=r.uniform(-0.08, 0.08))
    beam(pr, Vector((px, py + 0.02, 2.12)), Vector((px, py + 0.44, 2.6)), 0.08, 0.08, common.jitter(pc, r, 0.05),
         extend=0.03, twist=r.uniform(-0.1, 0.1))
    pr.box(post[-1] + Vector((0, 0, 0.03)), (0.16, 0.16, 0.05), (r.uniform(-0.1, 0.1), r.uniform(-0.1, 0.1), 0.3),
           common.jitter(pc, r, 0.05))
    hx, hy, hz = px, py + 0.66, 2.58
    m = P["metal"]
    for i in range(3):
        m.box((hx, hy, hz - 0.05 - i * 0.055), (0.015, 0.04 if i % 2 else 0.015, 0.06), (0, 0, 0), METAL)
    zt = hz - 0.22
    lc = LANTERN
    pyramid(m, (hx, hy, zt), 0.15, 0.13, lc)
    m.box((hx, hy, zt + 0.15), (0.04, 0.04, 0.06), (0, 0, 0), lc)
    m.box((hx, hy, zt - 0.015), (0.28, 0.28, 0.035), (0, 0, 0), lc)
    P["glow"].box((hx, hy, zt - 0.17), (0.17, 0.17, 0.27), (0, 0, 0), C("lantern_glow"))
    for sx in (-1, 1):
        for sy in (-1, 1):
            m.box((hx + sx * 0.095, hy + sy * 0.095, zt - 0.17), (0.035, 0.035, 0.3), (0, 0, 0), lc)
    for ax in ((1, 0), (0, 1)):
        m.box((hx, hy, zt - 0.17), (0.19 if ax[0] else 0.02, 0.19 if ax[1] else 0.02, 0.02), (0, 0, 0), lc)
    m.box((hx, hy, zt - 0.33), (0.24, 0.24, 0.04), (0, 0, 0), lc)
    pyramid(m, (hx, hy, zt - 0.35), 0.08, -0.08, lc)
    counts["lantern_pieces"] = len(post) + 2 + 14


def build_steps(P, r, counts):
    W = wall_frame(SIDES["east"])
    a0, a1 = -0.82, 0.52
    for (o0, o1, top) in ((0.12, 0.52, FOUND_TOP - 0.14), (0.52, 0.92, FOUND_TOP - 0.28)):
        P["props"].box(((a0 + a1) / 2, (top - 0.05 - 0.2) / 2, (o0 + o1) / 2), (a1 - a0 - 0.08, top - 0.05 + 0.2, o1 - o0 - 0.06),
                       (0, 0, 0), common.jitter(common.mix(STEP, CORE, 0.3), r, 0.05), matrix=W)
        for j in range(2):
            pw = (o1 - o0) / 2
            oc = o0 + (j + 0.5) * pw
            P["props"].box(((a0 + a1) / 2 + r.uniform(-0.03, 0.03), top - 0.025, oc), (a1 - a0 + r.uniform(-0.05, 0.03), 0.055, pw - 0.015),
                           (0, r.uniform(-0.02, 0.02), r.uniform(-0.015, 0.015)), common.jitter(STEP, r, 0.08), matrix=W)
    counts["step_pieces"] = 6


def build_fence(P, r, counts):
    posts = [(-4.25, -2.2), (-4.15, -3.35), (-3.05, -3.6), (-1.95, -3.45), (-1.0, -3.55)]
    tops = []
    for i, (x, y) in enumerate(posts):
        g = ground_z(x, y)
        tilt = Vector((r.uniform(-0.08, 0.08), r.uniform(-0.08, 0.08), 1.0)).normalized()
        h = r.uniform(0.95, 1.1)
        base = Vector((x, y, g - 0.25))
        top = base + tilt * (h + 0.25)
        beam(P["props"], base, top, 0.12, 0.12, common.jitter(FENCE, r, 0.08), twist=r.uniform(-0.3, 0.3))
        P["props"].box(top + Vector((0, 0, 0.03)), (0.15, 0.15, 0.05), (r.uniform(-0.2, 0.2), r.uniform(-0.2, 0.2), r.uniform(0, 1)),
                       common.jitter(FENCE, r, 0.08))
        tops.append((base, tilt, h))
    n = len(posts) * 2
    for i in range(len(posts) - 1):
        (b0, t0, h0), (b1, t1, h1) = tops[i], tops[i + 1]
        for k, frac in enumerate((0.42, 0.8)):
            if i == 2 and k == 0:
                continue        # a missing rail
            p = b0 + t0 * (0.25 + h0 * frac) + Vector((0, 0, r.uniform(-0.04, 0.04)))
            q = b1 + t1 * (0.25 + h1 * frac) + Vector((0, 0, r.uniform(-0.04, 0.04)))
            off = Vector((0, 0, 0))
            d = (q - p).normalized()
            side = Vector((d.y, -d.x, 0)).normalized() * 0.075
            off = side if i > 0 else -side
            beam(P["props"], p + off, q + off, 0.055, 0.11, common.jitter(FENCE, r, 0.1), extend=0.1,
                 twist=r.uniform(-0.05, 0.05))
            n += 1
    counts["fence_pieces"] = n


# ---------------------------------------------------------------- plants
def leaf(mb, base, axis, normal, length, width, col, mat=0):
    axis = axis.normalized()
    side = normal.cross(axis).normalized()
    nrm = axis.cross(side).normalized()
    v = [base, base + axis * length * 0.45 + side * width / 2 + nrm * 0.01 * length / 0.12,
         base + axis * length, base + axis * length * 0.45 - side * width / 2 + nrm * 0.01 * length / 0.12]
    mb.geometry([tuple(p) for p in v], [(0, 1, 2), (0, 2, 3)], col=col, mat=mat)


def leaf_col(r, t):
    c = common.mix(C("leaf_dark"), C("leaf_mid"), t)
    if r.random() < 0.3:
        c = common.mix(c, C("leaf_teal"), r.uniform(0.2, 0.6))
    if t > 0.7 and r.random() < 0.5:
        c = common.mix(c, C("leaf_light"), r.uniform(0.2, 0.6))
    return common.jitter(c, r, 0.08)


def random_dir(r, zmin=-1.0):
    while True:
        v = Vector((r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(-1, 1)))
        if 0.05 < v.length <= 1.0:
            v.normalize()
            if v.z >= zmin:
                return v


def flower(mb, pos, col, r, size=0.035, mat=1):
    verts, faces = ico_template(1)
    s = size * r.uniform(0.8, 1.2)
    mb.geometry([tuple(pos + Vector(v) * s * Vector((1, 1, 0.75))) for v in verts], faces,
                col=common.jitter(col, r, 0.08), mat=mat)


def shrub(P, center, radius, height, r, flower_cols, counts):
    mb = P["foliage"]
    verts, faces = ico_template(2)
    nb = r.randint(3, 4)                 # several lumps per shrub so it never reads as a ball
    nl = nf = 0
    seed = r.uniform(0, 100)
    for b in range(nb):
        rad = radius * r.uniform(0.5, 0.74) * (1.0 if b == 0 else r.uniform(0.75, 0.95))
        ang = 2 * math.pi * (b / nb) + r.uniform(-0.5, 0.5)
        dist = 0.0 if b == 0 else radius * r.uniform(0.35, 0.6)
        off = Vector((math.cos(ang) * dist, math.sin(ang) * dist, 0))
        sc = Vector((rad, rad * r.uniform(0.85, 1.1), rad * height * 0.95))
        bc = center + off + Vector((0, 0, sc.z * r.uniform(0.55, 0.7)))
        vs, cs = [], []
        def bump(d):
            return 1.0 + 0.22 * common.n2(d.x * 1.5 + seed, d.y * 1.5, d.z * 1.5 + b)

        for v in verts:
            d = Vector(v)
            p = bc + Vector((d.x * sc.x, d.y * sc.y, d.z * sc.z)) * bump(d)
            vs.append(tuple(p))
            t = 0.5 + 0.5 * d.z
            cs.append(common.jitter(common.mix(common.mix(C("leaf_dark"), C("#2f5532"), 0.4), C("leaf_mid"), t * 0.9), r, 0.05))
        mb.geometry(vs, faces, vert_cols=cs)
        for i in range(int(110 * rad / 0.35) + 30):
            d = random_dir(r, -0.35)
            p = bc + Vector((d.x * sc.x, d.y * sc.y, d.z * sc.z)) * bump(d) * r.uniform(0.93, 1.08)
            t1 = d.cross(random_dir(r)).normalized()
            axis = (t1 * 0.7 + d * 0.72).normalized()
            ln = r.uniform(0.12, 0.17)
            leaf(mb, p - axis * ln * 0.25, axis, d, ln, ln * 0.55, leaf_col(r, 0.4 + 0.6 * max(0.0, d.z)))
            nl += 1
        for i in range(int(14 * rad / 0.35) + 5):
            d = random_dir(r, 0.0)
            p = bc + Vector((d.x * sc.x, d.y * sc.y, d.z * sc.z)) * bump(d) * 1.05
            col = r.choice(flower_cols)
            for j in range(r.randint(1, 3)):
                flower(mb, p + Vector((r.uniform(-0.04, 0.04), r.uniform(-0.04, 0.04), r.uniform(0.0, 0.03))), col, r)
                nf += 1
    counts["leaves"] = counts.get("leaves", 0) + nl
    counts["flowers"] = counts.get("flowers", 0) + nf


def lupine(P, base, height, col, r, counts):
    """Flower spike: thin stem, a cone of small blossoms, palmate leaves at the foot."""
    mb = P["foliage"]
    lean = Vector((r.uniform(-0.08, 0.08), r.uniform(-0.08, 0.08), 1.0)).normalized()
    top = base + lean * height
    mb.box((base + top) / 2, (0.022, 0.022, height), lean.to_track_quat("Z", "Y").to_euler(),
           common.jitter(C("leaf_dark"), r, 0.05))
    n = 0
    rows = 11
    for k in range(rows):
        t = k / (rows - 1)
        c = base + lean * height * (0.42 + 0.58 * t)
        rad = 0.065 * (1.0 - t) + 0.018
        tip = common.mix(col, C("flower_white"), 0.45 * t)
        for j in range(3):
            a = 2 * math.pi * (j / 3 + 0.17 * k)
            flower(P["foliage"], c + Vector((math.cos(a) * rad, math.sin(a) * rad, 0)), tip, r, size=0.032)
            n += 1
    for i in range(7):
        a = 2 * math.pi * i / 7 + r.uniform(-0.2, 0.2)
        d = Vector((math.cos(a), math.sin(a), r.uniform(0.15, 0.5)))
        leaf(mb, base + Vector((0, 0, 0.12)), d, Vector((0, 0, 1)), r.uniform(0.15, 0.2), 0.07, leaf_col(r, 0.5))
    counts["flowers"] = counts.get("flowers", 0) + n
    counts["leaves"] = counts.get("leaves", 0) + 7


def flowerbox_plants(P, top, r, counts):
    mb = P["foliage"]
    nl = nf = 0
    for i in range(30):
        p = top + Vector((r.uniform(-0.32, 0.32), r.uniform(-0.07, 0.07), 0.0))
        d = Vector((r.uniform(-0.6, 0.6), r.uniform(-0.8, 0.2), 1.0)).normalized()
        leaf(mb, p, d, Vector((0, -1, 0.3)), r.uniform(0.1, 0.15), 0.06, leaf_col(r, 0.6))
        nl += 1
    for i in range(16):
        p = top + Vector((r.uniform(-0.32, 0.32), r.uniform(-0.08, 0.06), r.uniform(0.05, 0.14)))
        col = C("flower_purple") if r.random() < 0.55 else C("flower_yellow")
        flower(mb, p, col, r, size=0.04)
        nf += 1
    counts["leaves"] = counts.get("leaves", 0) + nl
    counts["flowers"] = counts.get("flowers", 0) + nf


def ivy(P, W, a_start, height, r, counts, n_leaves=170, spread=0.45):
    mb = P["foliage"]
    stems = []
    for b in range(3):
        a, z = a_start + r.uniform(-0.15, 0.15), 0.3
        if b > 0:
            a, z = stems[0][r.randint(4, max(5, len(stems[0]) - 3))]
        ang = math.pi / 2 + r.uniform(-0.3, 0.3) + (0.6 if b == 1 else -0.6 if b == 2 else 0)
        pts = [(a, z)]
        top = height * (1.0 if b == 0 else r.uniform(0.6, 0.85))
        while z < top and abs(a - a_start) < spread:
            ang += r.uniform(-0.4, 0.4)
            ang = min(max(ang, 0.7), 2.4)
            a += math.cos(ang) * 0.08
            z += math.sin(ang) * 0.08
            pts.append((a, z))
        stems.append(pts)
    sc = common.jitter(C("#5b5a35"), r, 0.05)
    for pts in stems:
        for (a0, z0), (a1, z1) in zip(pts[:-1], pts[1:]):
            mb.box(((a0 + a1) / 2, (z0 + z1) / 2, 0.135), (math.hypot(a1 - a0, z1 - z0) + 0.01, 0.018, 0.018),
                   (0, 0, math.atan2(z1 - z0, a1 - a0)), sc, matrix=W)
    allp = [p for s in stems for p in s]
    for i in range(n_leaves):
        a, z = r.choice(allp)
        base = Vector((a + r.uniform(-0.06, 0.06), z + r.uniform(-0.06, 0.06), 0.14 + r.uniform(0.0, 0.03)))
        ang = r.uniform(0, 2 * math.pi)
        axis = Vector((math.cos(ang), math.sin(ang), r.uniform(0.3, 0.8)))
        ln = r.uniform(0.1, 0.15)
        t = min(1.0, z / height)
        col = leaf_col(r, 0.2 + 0.6 * t)
        lb = W @ base
        la = (W.to_3x3() @ axis).normalized()
        ln_n = W.to_3x3() @ Vector((r.uniform(-0.3, 0.3), r.uniform(-0.3, 0.3), 1.0))
        leaf(mb, lb, la, ln_n, ln, ln * 0.8, col)
    counts["leaves"] = counts.get("leaves", 0) + n_leaves


def build_plants(P, r, counts):
    purple, white, pink = C("flower_purple"), C("flower_white"), C("flower_pink")
    spots = [
        ((-2.75, -2.55), 0.5, 1.1, [purple, white]),
        ((-1.75, -2.6), 0.42, 1.0, [pink, white]),
        ((0.55, -2.55), 0.4, 1.0, [purple]),
        ((1.75, -2.6), 0.48, 1.1, [white, pink]),
        ((2.95, -2.35), 0.42, 1.2, [purple, white]),
        ((3.0, 1.35), 0.5, 1.1, [pink, white]),
        ((2.95, 2.4), 0.38, 1.0, [purple]),
        ((-2.95, 0.6), 0.45, 1.0, [white, purple]),
        ((-2.85, 2.45), 0.5, 1.1, [pink]),
        ((0.2, 2.6), 0.42, 1.0, [white]),
        ((-3.6, -3.1), 0.36, 1.0, [purple, white]),
    ]
    for (x, y), rad, hr, cols in spots:
        shrub(P, Vector((x, y, ground_z(x, y) - 0.05)), rad * 1.32, hr, r, cols, counts)
    for (x, y), h, col in (((0.95, -2.45), 0.85, purple), ((1.15, -2.62), 0.7, purple), ((0.8, -2.68), 0.62, pink),
                           ((2.82, -1.2), 0.8, purple), ((2.95, -1.42), 0.66, white),
                           ((-3.45, -2.55), 0.75, purple)):
        lupine(P, Vector((x, y, ground_z(x, y) - 0.02)), h, col, r, counts)
    ivy(P, wall_frame(SIDES["front"]), -1.85, 3.2, r, counts, n_leaves=300)
    ivy(P, wall_frame(SIDES["east"]), 1.45, 2.5, r, counts, n_leaves=200, spread=0.35)
    counts["shrubs"] = len(spots)


# ---------------------------------------------------------------- assembly
def make_materials():
    glow = C("lantern_glow")
    return {
        "wood": common.material("M_House_Wood", use_vcol=True, roughness=0.82),
        "roof": common.material("M_House_Roof", use_vcol=True, roughness=0.88),
        "stone": common.material("M_House_Stone", use_vcol=True, roughness=0.92),
        "glass": common.material("M_House_Glass", base=C("glass"), roughness=0.16, specular=0.8),
        "metal": common.material("M_House_Metal", use_vcol=True, roughness=0.55, metallic=0.2),
        "lantern": common.material("M_House_Lantern", base=glow, roughness=0.35, emission=glow,
                                   emission_strength=2.5),
        "foliage": common.material("M_House_Foliage", use_vcol=True, roughness=0.7, translucency=0.3),
        "flowers": common.material("M_House_Flowers", use_vcol=True, roughness=0.6),
        "smoke": common.material("M_House_Smoke", base=(0.92, 0.92, 0.9, 1.0), roughness=1.0,
                                 emission=(1.0, 1.0, 1.0, 1.0), emission_strength=0.25),
    }


# part key -> (object name, material keys, bevel (width, segments) or None, recalc normals)
OBJECTS = [
    ("stone", "House_Foundation", ["stone"], (0.04, 2), True),
    ("chimney", "House_Chimney", ["stone"], (0.03, 2), True),
    ("frame", "House_Timber", ["wood"], (0.018, 2), True),
    ("planks", "House_Planks", ["wood"], (0.009, 2), True),
    ("roof", "House_Roof", ["roof"], (0.016, 2), True),
    ("props", "House_Props", ["wood"], (0.016, 2), True),
    ("glass", "House_Glass", ["glass"], None, True),
    ("metal", "House_Metal", ["metal"], None, True),
    ("glow", "House_LanternGlow", ["lantern"], None, True),
    ("foliage", "House_Foliage", ["foliage", "flowers"], None, False),
    ("smoke", "House_Smoke", ["smoke"], None, True),
]


def build():
    t0 = time.time()
    coll = common.collection("House")
    r = common.rng(1207)
    mats = make_materials()
    P = {key: common.MeshBuilder() for key, *_ in OBJECTS}
    counts = {}
    build_core(P)
    build_foundation(P, r, counts)
    build_walls(P, r, counts)
    build_main_roof(P, r, counts)
    build_porch(P, r, counts)
    build_dormer(P, r, counts)
    build_chimney(P, r, counts)
    build_smoke(P, r)
    build_lantern(P, r, counts)
    build_steps(P, r, counts)
    build_fence(P, r, counts)
    build_plants(P, r, counts)

    hm = common.house_matrix()
    objects = {}
    for key, name, mkeys, bevel, recalc in OBJECTS:
        mb = P[key]
        if recalc:
            bmesh.ops.recalc_face_normals(mb.bm, faces=list(mb.bm.faces))
        ob = mb.to_object(name, coll, [mats[k] for k in mkeys], smooth=True)
        ob.matrix_world = hm
        if bevel:
            common.add_bevel(ob, width=bevel[0], segments=bevel[1])
        objects[name] = ob
    bpy.context.view_layer.update()
    tris = {name: common.triangle_count(ob) for name, ob in objects.items()}
    return {
        "collection": coll.name,
        "objects": list(objects),
        "triangles": tris,
        "total_triangles": sum(tris.values()),
        "instances": counts,
        "total_instances": sum(counts.values()),
        "build_s": round(time.time() - t0, 2),
        "notes": "House local frame = common.house_matrix(); all objects share that transform.",
    }
