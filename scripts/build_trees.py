"""Trees, bushes and the forest around the clearing (collection 'Forest').

Each variant is one mesh with material slots M_Bark and M_Leaves: a curved tapered trunk with branches,
a shell of small leaf polygons laid over a smooth union of ellipsoid clumps roughened by two octaves of
leaf-cluster lumps, and a dark inner core that fills the gaps between leaves. Leaves carry custom normals
from the canopy field (each clump shades as a soft ball, the leaf clusters as small bumps on it) and a
'Col' gradient (warm light yellow-green on top/outside and on cluster tops, deep blue-green underneath, in
the creases between clumps and inside). Each variant is copied into colour tints; placed objects are
linked duplicates of those meshes. Bushes sit in small groups under the canopy rim of the forest-edge
trees and on the far shore of the pond.

build_lod_meshes() makes the light level-of-detail meshes of every variant and tint for the web viewer
(<variant>_L0/_L1/_L2, see LOD_SPECS) from the same canopy field and trunk skeleton; build() does not use
them.
"""
import math

import bmesh
import bpy
import numpy as np

import common

COLLECTION = "Forest"
SEED = 4242

# Canopy blobs are (x, y, z, rx, ry, rz) in metres with the trunk base at the origin; the first blob is
# the main volume, the others are softer bumps on it.
TREES = {
    "Round": dict(
        blobs=[(0.0, 0.0, 4.9, 2.5, 2.5, 2.2), (0.3, 0.2, 6.5, 1.9, 1.9, 1.6),
               (1.9, 0.5, 5.3, 1.6, 1.6, 1.45), (-1.7, -0.8, 5.6, 1.7, 1.7, 1.5),
               (0.4, -1.9, 4.6, 1.55, 1.55, 1.35), (-0.9, 1.7, 4.7, 1.6, 1.6, 1.4),
               (1.5, -0.9, 5.9, 1.3, 1.3, 1.15)],
        trunk_r=0.34, bend=0.35, leaf=0.38, branches=3, seed=101),
    "Tall": dict(
        blobs=[(0.0, 0.0, 6.6, 2.5, 2.5, 3.6), (1.3, -0.7, 5.0, 1.75, 1.75, 1.55),
               (-1.3, 0.9, 5.4, 1.7, 1.7, 1.5), (1.2, 1.0, 7.4, 1.55, 1.55, 1.4),
               (-1.2, -0.9, 7.9, 1.55, 1.55, 1.4), (0.4, -0.3, 9.2, 1.5, 1.5, 1.3)],
        trunk_r=0.33, bend=0.45, leaf=0.36, branches=3, seed=202),
    "Broad": dict(
        blobs=[(0.0, 0.0, 5.7, 3.0, 3.0, 2.3), (2.3, 0.6, 5.3, 1.9, 1.9, 1.65),
               (-2.1, -1.0, 5.5, 2.0, 2.0, 1.75), (0.7, -2.3, 5.1, 1.8, 1.8, 1.55),
               (-0.8, 2.2, 5.3, 1.85, 1.85, 1.6), (0.3, 0.1, 7.4, 2.3, 2.3, 1.8),
               (-1.7, -0.8, 6.7, 1.6, 1.6, 1.4)],
        trunk_r=0.42, bend=0.30, leaf=0.40, branches=4, seed=303),
    "Small": dict(
        blobs=[(0.0, 0.0, 3.9, 2.2, 2.2, 1.6), (1.2, 0.5, 4.4, 1.45, 1.45, 1.25),
               (-1.3, -0.5, 4.2, 1.4, 1.4, 1.2), (0.4, -1.4, 3.6, 1.35, 1.35, 1.1),
               (-0.5, 1.3, 3.7, 1.3, 1.3, 1.1), (0.2, 0.2, 5.0, 1.4, 1.4, 1.15)],
        trunk_r=0.27, bend=0.30, leaf=0.33, branches=2, seed=404),
}
BUSHES = {
    "Low": dict(
        blobs=[(0.0, 0.0, 0.42, 0.75, 0.75, 0.52), (0.5, 0.2, 0.55, 0.5, 0.5, 0.42),
               (-0.42, -0.28, 0.5, 0.5, 0.5, 0.4), (0.0, 0.45, 0.62, 0.42, 0.42, 0.36)],
        leaf=0.28, seed=505),
    "Tall": dict(
        blobs=[(0.0, 0.0, 0.74, 1.0, 1.0, 0.72), (0.55, 0.3, 0.95, 0.72, 0.72, 0.52),
               (-0.6, -0.2, 0.88, 0.75, 0.75, 0.55), (0.1, -0.65, 0.66, 0.7, 0.7, 0.5),
               (-0.2, 0.6, 0.95, 0.6, 0.6, 0.48)],
        leaf=0.29, seed=606),
}
LEAF_COVERAGE = 2.1          # how many leaves overlap any point of the outer shell, on average
LEAF_INNER = 0.18            # extra leaves in a darker layer below the shell
TRI_BUDGET = 33000

# Leaf gradient stops (sRGB): deep blue-green, dark, mid, warm light.
TINTS = {
    "green": ("#2c5651", "#467451", "#88b15c", "#c8da7a"),
    "teal": ("#2f5752", "#47705f", "#79a27c", "#b8d18c"),
    "yellow": ("#3a5c46", "#5a7e4a", "#9dbb5a", "#d9de80"),
}
TINT_STOPS = (0.0, 0.3, 0.64, 1.0)
TREE_TINTS = ("green", "teal", "yellow")
BUSH_TINTS = ("green", "yellow")

# Leaf template (u along the leaf axis, v across, h along the normal): pointed ellipse as two quads
# folded along the midrib.
LEAF_U = np.array([-0.42, -0.17, 0.2, 0.58, 0.2, -0.17])
LEAF_V = np.array([0.0, 0.5, 0.44, 0.0, -0.44, -0.5])
LEAF_H = np.array([0.0, -0.05, -0.05, 0.03, -0.05, -0.05])
LEAF_DT = np.array([-0.08, -0.03, 0.0, 0.05, 0.0, -0.03])   # base darker, tip lighter
LEAF_AREA = 0.31                                            # area / length^2 at width = 0.5 * length

FOREST_SPACING = (4.5, 6.0)
MAX_OBJECTS = 450


# ---------------------------------------------------------------- small numpy helpers
def _normalize(v):
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ellipsoid_area(r):
    p = 1.6075
    a, b, c = r[..., 0] ** p, r[..., 1] ** p, r[..., 2] ** p
    return 4.0 * math.pi * ((a * b + a * c + b * c) / 3.0) ** (1.0 / p)


def _random_rotation(nrng):
    w, x, y, z = _normalize(nrng.normal(size=4))
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _fib_sphere(n, nrng, jitter=0.3):
    k = np.arange(n) + 0.5
    z = 1.0 - 2.0 * k / n
    r = np.sqrt(np.clip(1.0 - z * z, 0.0, 1.0))
    th = math.pi * (1.0 + 5 ** 0.5) * k + nrng.uniform(0.0, 2.0 * math.pi)
    u = np.stack([r * np.cos(th), r * np.sin(th), z], 1) @ _random_rotation(nrng).T
    u += nrng.normal(0.0, jitter * math.sqrt(4.0 * math.pi / max(n, 1)), u.shape)
    return _normalize(u)


class _Lumps:
    """Cheap smooth pseudo-noise (sum of random plane waves), roughly in [-1, 1]."""

    def __init__(self, nrng, freq, waves=7):
        self.d = _normalize(nrng.normal(size=(waves, 3))) * (freq * nrng.uniform(0.7, 1.3, (waves, 1)))
        self.ph = nrng.uniform(0.0, 2.0 * math.pi, waves)

    def __call__(self, p):
        return np.sin(p @ self.d.T + self.ph).sum(1) / math.sqrt(len(self.ph))

    def grad(self, p):
        return (np.cos(p @ self.d.T + self.ph) @ self.d) / math.sqrt(len(self.ph))


def _tint_colors(t, tint):
    stops = np.array([common.color(h)[:3] for h in TINTS[tint]])
    t = np.clip(t, 0.0, 1.0)
    rgb = np.stack([np.interp(t, TINT_STOPS, stops[:, i]) for i in range(3)], 1)
    return np.concatenate([rgb, np.ones((len(t), 1))], 1)


# ---------------------------------------------------------------- canopy field
class Canopy:
    """Smooth union of ellipsoid blobs with a lumpy surface: projection, normals and colour value."""

    def __init__(self, blobs, nrng, blend=6.0, normal_blend=4.5):
        b = np.array(blobs, float)
        self.C, self.R = b[:, :3], b[:, 3:]
        self.rbar = self.R.mean(1)
        self.k = blend
        self.kn = normal_blend
        lo, hi = (self.C - self.R).min(0), (self.C + self.R).max(0)
        self.center = (lo + hi) * 0.5
        self.half = (hi - lo) * 0.5
        self.zmin, self.zmax = lo[2], hi[2]
        # two octaves of leaf-cluster lumps (sprigs ~1.5 m and ~0.7 m across on a tree), scaled with size
        sz = float(np.clip(self.rbar.max() / 2.3, 0.45, 1.3))
        self.octaves = [(_Lumps(nrng, 2.0 / sz), 0.2 * sz), (_Lumps(nrng, 4.4 / sz), 0.07 * sz)]
        self.lump_amp = sum(a for _, a in self.octaves)

    def implicit(self, p):
        """Per-blob normalised radius (M, K); < 1 inside a blob."""
        return np.linalg.norm((p[:, None, :] - self.C[None]) / self.R[None], axis=2)

    def _dist(self, p):
        d = (p[:, None, :] - self.C[None]) / self.R[None]
        f = np.linalg.norm(d, axis=2)
        return (f - 1.0) * self.rbar[None], _normalize(d / self.R[None])

    def sdf(self, p, k=None):
        """Approximate signed distance to the smooth union (metres), with the grad direction."""
        k = k or self.k
        d, g = self._dist(p)
        m = d.min(1, keepdims=True)
        e = np.exp(-k * (d - m))
        s = m[:, 0] - np.log(e.sum(1)) / k
        n = _normalize((e[..., None] * g).sum(1))
        return s, n

    def crease(self, p):
        """1 in the valleys where two clumps meet, 0 on a clump's open surface."""
        d, _ = self._dist(p)
        if d.shape[1] < 2:
            return np.zeros(len(p))
        d2 = np.sort(d, axis=1)
        return np.exp(-np.abs(d2[:, 1] - d2[:, 0]) / 0.35)

    def level(self, p):
        return sum(a * lm(p) for lm, a in self.octaves)

    def level_grad(self, p):
        return sum(a * lm.grad(p) for lm, a in self.octaves)

    def project(self, p, offset=0.0, iters=4):
        """Move points onto the lumpy surface shifted by `offset` (negative = inside)."""
        for _ in range(iters):
            s, n = self.sdf(p)
            p = p - (s - self.level(p) - offset)[:, None] * n
        return p

    def surface_normal(self, p):
        """Normal of the lumpy surface (the leaves lie on it)."""
        _, n = self.sdf(p)
        g = self.level_grad(p)
        g -= n * (g * n).sum(1, keepdims=True)
        return _normalize(n - g)

    def normals(self, p, w_glob=0.15, w_up=0.1, w_lump=0.45):
        """Shading normals: soft per-clump balls, a little of the whole crown and of the leaf clusters."""
        _, n = self.sdf(p, self.kn)
        n_glob = _normalize((p - self.center) / self.half ** 2)
        g = self.level_grad(p)
        g -= n * (g * n).sum(1, keepdims=True)
        return _normalize((1.0 - w_glob) * n + w_glob * n_glob - w_lump * g + np.array([0.0, 0.0, w_up]))

    def shade(self, p, n):
        """Gradient value: 0 = deep underneath/inside, 1 = sunlit top."""
        hz = np.clip((p[:, 2] - self.zmin) / max(self.zmax - self.zmin, 1e-3), 0.0, 1.0)
        s, _ = self.sdf(p)
        lv = self.level(p)
        depth = np.clip(-(s - lv) / 0.5, 0.0, 1.0)
        bump = np.clip(lv / (0.8 * self.lump_amp), -1.0, 1.0)      # cluster tops light, valleys dark
        return (0.2 + 0.36 * hz + 0.38 * (0.5 + 0.5 * n[:, 2]) - 0.4 * depth
                - 0.22 * self.crease(p) + 0.09 * bump)


# ---------------------------------------------------------------- geometry parts
class Part:
    """Raw geometry: verts (N,3), faces (list of index tuples), per-face material, per-vertex normal,
    and either a gradient value t (leaves, tinted later) or a fixed colour (bark)."""

    def __init__(self, verts, faces, mat, normals, t=None, cols=None):
        self.verts = np.asarray(verts, float)
        self.faces = faces
        self.mat = mat
        self.normals = np.asarray(normals, float)
        n = len(self.verts)
        self.t = np.full(n, np.nan) if t is None else np.asarray(t, float)
        self.cols = np.zeros((n, 4)) if cols is None else np.asarray(cols, float)


def _leaf_shell(canopy, leaf_len, nrng, max_leaves, mat=1):
    """Leaf polygons over the visible union surface plus a sparser, deeper layer."""
    C, R = canopy.C, canopy.R
    K = len(C)
    areas = _ellipsoid_area(R)

    def samples(i, n, inner):
        u = _fib_sphere(n, nrng)
        p = C[i] + u * R[i]
        f = canopy.implicit(p)
        f[:, i] = 9.0
        keep = f.min(1) > (0.9 if inner else 0.97)
        nb = _normalize(u / R[i])
        keep &= nrng.random(n) < 0.45 + 0.55 * _smoothstep(-0.75, -0.1, nb[:, 2])
        return p[keep]

    vis = np.array([len(samples(i, 800, False)) / 800.0 for i in range(K)])
    vis_area = float((areas * vis).sum())
    n_outer = LEAF_COVERAGE * vis_area / (leaf_len ** 2 * LEAF_AREA)
    n_outer = min(n_outer, max_leaves / (1.0 + LEAF_INNER))
    density = n_outer / vis_area

    outer = [samples(i, int(density * areas[i]) + 1, False) for i in range(K)]
    inner = [samples(i, int(density * areas[i] * LEAF_INNER) + 1, True) for i in range(K)]
    P_out = canopy.project(np.concatenate(outer))
    P_in = np.concatenate(inner)
    P_in = canopy.project(P_in, offset=-nrng.uniform(0.15, 0.4, len(P_in)))
    P = np.concatenate([P_out, P_in])
    L = len(P)
    NB = canopy.surface_normal(P)

    # leaf frames: axis down the surface with some yaw, tip tilted outwards, slight roll
    d = np.array([0.0, 0.0, -1.0]) + NB * NB[:, 2:3]
    bad = np.linalg.norm(d, axis=1) < 0.3
    rnd = nrng.normal(size=(L, 3))
    rnd = rnd - NB * (rnd * NB).sum(1, keepdims=True)
    d[bad] = rnd[bad]
    d = _normalize(d)
    psi = nrng.uniform(-0.9, 0.9, (L, 1))
    a = np.cos(psi) * d + np.sin(psi) * np.cross(NB, d)
    tau = 0.05 + 0.62 * nrng.random((L, 1)) ** 2
    a = _normalize(np.cos(tau) * a + np.sin(tau) * NB)
    nn = _normalize(NB - a * (NB * a).sum(1, keepdims=True))
    rho = nrng.uniform(-0.3, 0.3, (L, 1))
    nn = _normalize(np.cos(rho) * nn + np.sin(rho) * np.cross(a, nn))
    b = np.cross(a, nn)

    ln = leaf_len * nrng.uniform(0.86, 1.12, (L, 1))
    wd = ln * nrng.uniform(0.46, 0.58, (L, 1))
    c = P - 0.02 * NB
    V = (c[:, None, :]
         + (LEAF_U[None, :, None] * ln[:, None, :]) * a[:, None, :]
         + (LEAF_V[None, :, None] * wd[:, None, :]) * b[:, None, :]
         + (LEAF_H[None, :, None] * ln[:, None, :]) * nn[:, None, :]).reshape(-1, 3)

    field_n = canopy.normals(V)
    vn = _normalize(0.88 * field_n + 0.12 * np.repeat(nn, 6, axis=0))
    t = canopy.shade(V, field_n)
    t += np.repeat(nrng.uniform(-0.06, 0.06, L), 6) + np.tile(LEAF_DT, L)

    base = np.arange(L) * 6
    faces = np.stack([np.stack([base, base + 1, base + 2, base + 3], 1),
                      np.stack([base, base + 3, base + 4, base + 5], 1)], 1).reshape(-1, 4)
    return Part(V, [tuple(f) for f in faces.tolist()], [mat] * len(faces), vn, t=t)


_ICO = None


def _icosphere():
    global _ICO
    if _ICO is None:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
        verts = np.array([v.co[:] for v in bm.verts])
        faces = [tuple(v.index for v in f.verts) for f in bm.faces]
        bm.free()
        _ICO = (verts, faces)
    return _ICO


def _core(canopy, depth=0.42, mat=1):
    """Dark inner hull following the leaf shell, so gaps between leaves show depth instead of sky."""
    uv, uf = _icosphere()
    verts, faces = [], []
    off = 0
    for i in range(len(canopy.C)):
        p = canopy.C[i] + uv * canopy.R[i]
        f = canopy.implicit(p)
        f[:, i] = 9.0
        hidden = f.min(1) < 0.85
        faces += [tuple(off + j for j in tri) for tri in uf if not all(hidden[j] for j in tri)]
        verts.append(p)
        off += len(p)
    V = canopy.project(np.concatenate(verts), offset=-depth)
    n = canopy.normals(V)
    t = canopy.shade(V, n) + 0.16
    return Part(V, faces, [mat] * len(faces), n, t=t)


def _tube(path, radii, sides, nrng, ridge=0.08, flare=None, twist=2.5):
    """Tapered tube along a polyline with parallel-transport frames; open ends."""
    path = np.asarray(path, float)
    n = len(path)
    T = _normalize(np.gradient(path, axis=0))
    ref = np.array([0.0, 0.0, 1.0]) if abs(T[0, 2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    Nv = _normalize(np.cross(T[0], ref))
    phase = nrng.uniform(0.0, 2.0 * math.pi)
    verts, normals = [], []
    for i in range(n):
        if i > 0:
            Nv = _normalize(Nv - T[i] * Nv.dot(T[i]))
        B = np.cross(T[i], Nv)
        z = path[i, 2]
        for k in range(sides):
            ang = 2.0 * math.pi * k / sides
            r = radii[i] * (1.0 + ridge * math.cos(3.0 * ang + twist * z + phase))
            r *= 1.0 + nrng.uniform(-0.04, 0.04)
            if flare is not None:
                fl = flare[i]
                r *= 1.0 + fl * 0.8 * max(0.0, math.cos(2.0 * (ang - phase))) ** 2 + fl * 0.35
            dirv = math.cos(ang) * Nv + math.sin(ang) * B
            verts.append(path[i] + r * dirv)
            normals.append(dirv)
    faces = []
    for i in range(n - 1):
        for k in range(sides):
            k2 = (k + 1) % sides
            faces.append((i * sides + k, i * sides + k2, (i + 1) * sides + k2, (i + 1) * sides + k))
    return np.array(verts), faces, np.array(normals)


def _bark_colors(verts, nrng):
    dark, mid = np.array(common.color("trunk_dark")), np.array(common.color("trunk"))
    light = mid * np.array([1.6, 1.5, 1.38, 1.0])
    z = verts[:, 2]
    cols = dark + (mid * 1.12 - dark) * _smoothstep(-0.2, 0.9, z)[:, None]
    cols = cols + (light - cols) * (_smoothstep(0.6, 2.5, z) * 0.7)[:, None]
    cols[:, :3] *= nrng.uniform(0.93, 1.07, (len(verts), 1))
    cols[:, 3] = 1.0
    return cols


def _trunk(spec, nrng, skeleton=None):
    """Trunk and branches as one bark Part. `skeleton` (a dict), when given, receives the trunk path, radii
    and flare and the branch paths and radii (used by the LOD meshes)."""
    blobs = np.array(spec["blobs"], float)
    main = blobs[0]
    r0 = spec["trunk_r"]
    base = np.array([0.0, 0.0, -0.45])
    top = np.array([main[0] * 0.5, main[1] * 0.5, main[2] + 0.1 * main[5]])
    ang = nrng.uniform(0.0, 2.0 * math.pi)
    bend_dir = np.array([math.cos(ang), math.sin(ang), 0.0])
    side_dir = np.array([-math.sin(ang), math.cos(ang), 0.0])
    n_rings = max(10, int((top[2] - base[2]) / 0.42))
    ts = np.linspace(0.0, 1.0, n_rings) ** 1.3
    bend = spec["bend"]
    path = (base + (top - base) * ts[:, None]
            + bend_dir * (bend * np.sin(math.pi * ts))[:, None]
            + side_dir * (0.35 * bend * np.sin(2.0 * math.pi * ts))[:, None])
    radii = r0 * (0.4 + 0.6 * (1.0 - ts) ** 1.3)
    flare = np.exp(-np.clip(path[:, 2], 0.0, None) / 0.4) * (path[:, 2] < 1.4)
    V, F, N = _tube(path, radii, 8, nrng, flare=flare)
    verts, faces, normals = [V], list(F), [N]
    off = len(V)
    if skeleton is not None:
        skeleton.update(path=path, radii=radii, flare=flare, branches=[])

    # branches from the upper trunk towards off-centre bumps
    order = sorted(range(1, len(blobs)), key=lambda i: -math.hypot(blobs[i][0], blobs[i][1]))
    for bi in order[:spec["branches"]]:
        j = int(nrng.uniform(0.55, 0.8) * (n_rings - 1))
        start = path[j]
        end = start + 0.8 * (blobs[bi][:3] - start)
        mid = (start + end) * 0.5 + np.array([0.0, 0.0, 0.25 * np.linalg.norm(end - start)])
        s = np.linspace(0.0, 1.0, 6)[:, None]
        bpath = (1 - s) ** 2 * start + 2 * (1 - s) * s * mid + s ** 2 * end
        br = radii[j] * 0.6 * (1.0 - 0.8 * s[:, 0])
        if skeleton is not None:
            skeleton["branches"].append((bpath, br))
        V, F, N = _tube(bpath, br, 6, nrng, ridge=0.05)
        verts.append(V)
        normals.append(N)
        faces += [tuple(off + x for x in f) for f in F]
        off += len(V)
    V = np.concatenate(verts)
    return Part(V, faces, [0] * len(faces), np.concatenate(normals), cols=_bark_colors(V, nrng))


# ---------------------------------------------------------------- variant meshes
def _merge(parts):
    verts, faces, mats, normals, ts, cols = [], [], [], [], [], []
    off = 0
    for p in parts:
        verts.append(p.verts)
        faces += [tuple(off + i for i in f) for f in p.faces]
        mats += p.mat
        normals.append(p.normals)
        ts.append(p.t)
        cols.append(p.cols)
        off += len(p.verts)
    return (np.concatenate(verts), faces, np.array(mats, np.int32), np.concatenate(normals),
            np.concatenate(ts), np.concatenate(cols))


def _new_mesh(name, data, materials, tint):
    old = bpy.data.meshes.get(name)
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    verts, faces, mats, normals, t, cols = data
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts.tolist(), [], faces)
    me.update()
    for m in materials:
        me.materials.append(m)
    me.polygons.foreach_set("material_index", mats)
    me.shade_smooth()
    me.normals_split_custom_set_from_vertices(normals.tolist())
    attr = me.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    me.color_attributes.active_color = attr
    me.color_attributes.render_color_index = me.color_attributes.active_color_index
    vc = cols.copy()
    leafy = ~np.isnan(t)
    vc[leafy] = _tint_colors(t[leafy], tint)
    li = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", li)
    attr.data.foreach_set("color", vc[li].astype(np.float32).ravel())
    return me


def _mesh_tris(me):
    lt = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def _variant(spec, nrng, with_trunk):
    canopy = Canopy(spec["blobs"], nrng)
    mat = 1 if with_trunk else 0
    parts = [_trunk(spec, nrng)] if with_trunk else []
    parts.append(_core(canopy, depth=0.42 if with_trunk else 0.2, mat=mat))
    used = sum(len(f) - 2 for p in parts for f in p.faces)
    parts.append(_leaf_shell(canopy, spec["leaf"], nrng, (TRI_BUDGET - used) // 4, mat=mat))
    return _merge(parts)


def build_variant_meshes(mat_bark, mat_leaves):
    """Return {mesh_name: info} for every tree/bush variant and tint."""
    out = {}
    jobs = [("Tree", n, s, TREE_TINTS, [mat_bark, mat_leaves]) for n, s in TREES.items()]
    jobs += [("Bush", n, s, BUSH_TINTS, [mat_leaves]) for n, s in BUSHES.items()]
    for kind, vname, spec, tints, mats in jobs:
        data = _variant(spec, np.random.default_rng(spec["seed"]), kind == "Tree")
        height = float(data[0][:, 2].max())
        radius = float(np.hypot(data[0][:, 0], data[0][:, 1]).max())
        for tint in tints:
            me = _new_mesh(f"{kind}{vname}_{tint}", data, mats, tint)
            out[me.name] = dict(kind=kind.lower(), variant=vname, tint=tint, mesh=me, height=height,
                                radius=radius, tris=_mesh_tris(me))
    return out


# ---------------------------------------------------------------- level-of-detail meshes (web viewer)
# Light meshes of every variant for real-time display: a closed hull wrapped onto the lumpy canopy surface
# (slightly inside it) carries the soft volume shading, fewer and larger leaves over it break the silhouette,
# and a trunk with fewer sides. Same canopy field (blobs, lumps), canopy normals, colour gradient and trunk
# skeleton as the full variant. The Blender scene itself keeps the full meshes.
#   tree / bush    triangle budget
#   hull_type      'radial' (one geodesic sphere wrapped from the canopy centre) or 'blobs' (one per blob)
#   hull           geodesic frequency (20 f^2 triangles per sphere); depth = hull offset inside the surface (m)
#   coverage       leaf area / visible canopy area (the full variant has 2.1 plus an inner layer)
#   leaf           'diamond' (2 triangles, folded along the midrib) or 'tri' (1 triangle, tip outwards)
#   tilt           max lift of the leaf tips off the surface (rad; the full variant has 0.67)
#   rings / sides  trunk rings and sides; branches = sides of the branches (0 = none); top = how far the trunk
#                  reaches from the canopy bottom to the trunk top
LOD_SPECS = (
    dict(tree=3000, bush=1200, hull_type="radial", hull=4, hull_bush=3, depth=0.3, coverage=1.35,
         leaf="diamond", tilt=0.4, rings=7, sides=6, branches=4, top=1.0),
    dict(tree=1000, bush=400, hull_type="radial", hull=3, hull_bush=2, depth=0.22, coverage=1.2,
         leaf="diamond", tilt=0.3, rings=4, sides=5, branches=0, top=0.55),
    dict(tree=300, bush=120, hull_type="blobs", hull=1, hull_bush=1, depth=0.12, coverage=0.6,
         leaf="tri", tilt=0.2, rings=3, sides=4, branches=0, top=0.25),
)
# leaf templates (u along the leaf axis, v across in units of the width, h along the normal) and area / length^2
LEAF_SHAPES = {
    "diamond": dict(u=np.array([-0.42, 0.08, 0.58, 0.08]), v=np.array([0.0, 0.5, 0.0, -0.5]),
                    h=np.array([0.0, -0.05, 0.03, -0.05]), dt=np.array([-0.08, 0.0, 0.05, 0.0]),
                    faces=((0, 1, 2), (0, 2, 3)), width=0.52),
    "tri": dict(u=np.array([-0.32, 0.68, -0.32]), v=np.array([0.5, 0.0, -0.5]), h=np.array([-0.03, 0.03, -0.03]),
                dt=np.array([-0.05, 0.05, -0.05]), faces=((0, 1, 2),), width=0.62),
}
for _s in LEAF_SHAPES.values():
    _s["area"] = 0.5 * _s["width"]          # both shapes span one length and one width


def _geodesic(f):
    """Unit geodesic sphere: every icosahedron face split into f^2 triangles (outward winding)."""
    g = (1.0 + 5 ** 0.5) / 2.0
    ico = np.array([(-1, g, 0), (1, g, 0), (-1, -g, 0), (1, -g, 0), (0, -1, g), (0, 1, g), (0, -1, -g), (0, 1, -g),
                    (g, 0, -1), (g, 0, 1), (-g, 0, -1), (-g, 0, 1)], float)
    tris = ((0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
            (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11),
            (6, 2, 10), (8, 6, 7), (9, 8, 1))
    verts, index, faces = [], {}, []

    def vid(p):
        p = p / np.linalg.norm(p)
        key = tuple(np.round(p, 6))
        if key not in index:
            index[key] = len(verts)
            verts.append(p)
        return index[key]

    for a, b, c in tris:
        A, B, C = ico[a], ico[b], ico[c]
        grid = {(i, j): vid(A + (B - A) * i / f + (C - A) * j / f) for i in range(f + 1) for j in range(f + 1 - i)}
        for i in range(f):
            for j in range(f - i):
                faces.append((grid[i, j], grid[i + 1, j], grid[i, j + 1]))
                if i + j < f - 1:
                    faces.append((grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]))
    return np.array(verts), faces


def _hull(canopy, f, offset, mat):
    """Closed hull: a geodesic sphere around the canopy centre whose vertices are moved along their rays onto
    the outermost crossing of the lumpy canopy surface shifted by `offset` (negative = inside)."""
    U, faces = _geodesic(f)
    c = canopy.center

    def outside(t):
        p = c + U * t[:, None]
        s, _ = canopy.sdf(p)
        return s - canopy.level(p) - offset > 0.0

    ts = np.linspace(float(np.linalg.norm(canopy.half)) * 1.5 + 1.0, 0.0, 200)
    out = np.stack([outside(np.full(len(U), t)) for t in ts], axis=1)
    k = np.clip(np.argmin(out, axis=1), 1, len(ts) - 1)        # first sample inside, coming from outside
    lo, hi = ts[k - 1], ts[k]
    for _ in range(14):
        mid = 0.5 * (lo + hi)
        o = outside(mid)
        lo, hi = np.where(o, mid, lo), np.where(o, hi, mid)
    V = c + U * (0.5 * (lo + hi))[:, None]
    n = canopy.normals(V)
    return Part(V, faces, [mat] * len(faces), n, t=canopy.shade(V, n) + 0.16)


def _blob_hull(canopy, f, offset, mat):
    """Lobed hull: a geodesic sphere per canopy blob moved onto the lumpy surface shifted by `offset`, faces
    buried in other blobs dropped (keeps the clump silhouette at a few triangles per blob)."""
    U, tri = _geodesic(f)
    verts, faces = [], []
    off = 0
    for i in range(len(canopy.C)):
        p = canopy.C[i] + U * canopy.R[i]
        g = canopy.implicit(p)
        g[:, i] = 9.0
        hidden = g.min(1) < 0.8
        faces += [tuple(off + j for j in t) for t in tri if not all(hidden[j] for j in t)]
        verts.append(p)
        off += len(p)
    V = canopy.project(np.concatenate(verts), offset=offset)
    n = canopy.normals(V)
    return Part(V, faces, [mat] * len(faces), n, t=canopy.shade(V, n) + 0.16)


def _lod_leaves(canopy, n_leaves, coverage, shape, nrng, mat, tilt=0.35):
    """`n_leaves` leaves of one shape laid over the visible canopy surface, sized for the given coverage; tips
    lifted off the surface by up to `tilt` radians."""
    sh = LEAF_SHAPES[shape]
    C, R = canopy.C, canopy.R
    K = len(C)
    areas = _ellipsoid_area(R)

    def samples(i, n):
        u = _fib_sphere(n, nrng)
        p = C[i] + u * R[i]
        f = canopy.implicit(p)
        f[:, i] = 9.0
        keep = f.min(1) > 0.97
        nb = _normalize(u / R[i])
        keep &= nrng.random(n) < 0.45 + 0.55 * _smoothstep(-0.75, -0.1, nb[:, 2])
        return p[keep]

    vis = np.array([len(samples(i, 800)) / 800.0 for i in range(K)])
    vis_area = float((areas * vis).sum())
    density = n_leaves / vis_area
    P = np.concatenate([samples(i, int(round(density * areas[i])) + 1) for i in range(K)])
    if len(P) > n_leaves:
        P = P[nrng.permutation(len(P))[:n_leaves]]
    P = canopy.project(P)
    L = len(P)
    leaf_len = math.sqrt(coverage * vis_area / (n_leaves * sh["area"]))
    NB = canopy.surface_normal(P)

    d = np.array([0.0, 0.0, -1.0]) + NB * NB[:, 2:3]
    bad = np.linalg.norm(d, axis=1) < 0.3
    rnd = nrng.normal(size=(L, 3))
    rnd = rnd - NB * (rnd * NB).sum(1, keepdims=True)
    d[bad] = rnd[bad]
    d = _normalize(d)
    psi = nrng.uniform(-0.9, 0.9, (L, 1))
    a = np.cos(psi) * d + np.sin(psi) * np.cross(NB, d)
    tau = 0.03 + tilt * nrng.random((L, 1)) ** 2      # larger leaves lie flatter, so the clumps keep their shape
    a = _normalize(np.cos(tau) * a + np.sin(tau) * NB)
    nn = _normalize(NB - a * (NB * a).sum(1, keepdims=True))
    rho = nrng.uniform(-0.3, 0.3, (L, 1))
    nn = _normalize(np.cos(rho) * nn + np.sin(rho) * np.cross(a, nn))
    b = np.cross(a, nn)

    k = len(sh["u"])
    ln = leaf_len * nrng.uniform(0.86, 1.12, (L, 1))
    wd = ln * sh["width"] * nrng.uniform(0.9, 1.1, (L, 1))
    c = P - 0.02 * NB
    V = (c[:, None, :]
         + (sh["u"][None, :, None] * ln[:, None, :]) * a[:, None, :]
         + (sh["v"][None, :, None] * wd[:, None, :]) * b[:, None, :]
         + (sh["h"][None, :, None] * ln[:, None, :]) * nn[:, None, :]).reshape(-1, 3)
    field_n = canopy.normals(V)
    vn = _normalize(0.88 * field_n + 0.12 * np.repeat(nn, k, axis=0))
    t = canopy.shade(V, field_n) + np.repeat(nrng.uniform(-0.06, 0.06, L), k) + np.tile(sh["dt"], L)
    base = np.arange(L) * k
    faces = [tuple(int(b0 + i) for i in tri) for b0 in base for tri in sh["faces"]]
    return Part(V, faces, [mat] * len(faces), vn, t=t)


def _trunk_lod(skel, canopy, nrng, spec):
    """Trunk (and optionally branches) along the full variant's skeleton with fewer rings and sides; the
    trunk stops `top` of the way from the canopy bottom to the trunk top (the rest hides in the hull)."""
    path, radii, flare = skel["path"], skel["radii"], skel["flare"]
    z_stop = canopy.zmin + spec["top"] * (path[-1, 2] - canopy.zmin)
    last = max(2, int(np.searchsorted(path[:, 2], z_stop)) + 1)
    idx = np.unique(np.round(np.linspace(0, min(last, len(path) - 1), spec["rings"])).astype(int))
    V, F, N = _tube(path[idx], radii[idx], spec["sides"], nrng, flare=flare[idx])
    verts, faces, normals = [V], list(F), [N]
    off = len(V)
    if spec["branches"]:
        for bpath, br in skel["branches"]:
            bi = np.array([0, 2, 4, len(bpath) - 1])
            V, F, N = _tube(bpath[bi], br[bi], spec["branches"], nrng, ridge=0.05)
            verts.append(V)
            normals.append(N)
            faces += [tuple(off + x for x in f) for f in F]
            off += len(V)
    V = np.concatenate(verts)
    return Part(V, faces, [0] * len(faces), np.concatenate(normals), cols=_bark_colors(V, nrng))


def _variant_lod(spec, with_trunk, lod):
    ls = LOD_SPECS[lod]
    nrng = np.random.default_rng(spec["seed"])
    canopy = Canopy(spec["blobs"], nrng)            # the full variant's canopy (same draws)
    parts = []
    if with_trunk:
        skel = {}
        _trunk(spec, nrng, skeleton=skel)           # the full variant's trunk skeleton (same draws)
        lrng = np.random.default_rng(spec["seed"] * 7 + lod)
        parts.append(_trunk_lod(skel, canopy, lrng, ls))
        budget, f, mat = ls["tree"], ls["hull"], 1
    else:
        lrng = np.random.default_rng(spec["seed"] * 7 + lod)
        budget, f, mat = ls["bush"], ls["hull_bush"], 0
    depth = ls["depth"] * (1.0 if with_trunk else 0.6)
    hull = _blob_hull if ls["hull_type"] == "blobs" else _hull
    parts.append(hull(canopy, f, -depth, mat))
    used = sum(len(fc) - 2 for p in parts for fc in p.faces)
    per_leaf = len(LEAF_SHAPES[ls["leaf"]]["faces"])
    parts.append(_lod_leaves(canopy, max(8, (budget - used) // per_leaf), ls["coverage"], ls["leaf"], lrng, mat,
                             ls["tilt"]))
    return _merge(parts)


def build_lod_meshes(mat_bark, mat_leaves):
    """LOD meshes '<Kind><Variant>_<tint>_L<lod>' of every variant and tint (see LOD_SPECS); returns
    {mesh name: {variant, lod, mesh, tris}}. Does not touch the scene's full variant meshes or objects."""
    out = {}
    jobs = [("Tree", n, s, TREE_TINTS, [mat_bark, mat_leaves]) for n, s in TREES.items()]
    jobs += [("Bush", n, s, BUSH_TINTS, [mat_leaves]) for n, s in BUSHES.items()]
    for kind, vname, spec, tints, mats in jobs:
        for lod in range(len(LOD_SPECS)):
            data = _variant_lod(spec, kind == "Tree", lod)
            for tint in tints:
                me = _new_mesh(f"{kind}{vname}_{tint}_L{lod}", data, mats, tint)
                out[me.name] = dict(variant=f"{kind}{vname}_{tint}", lod=lod, mesh=me, tris=_mesh_tris(me))
    return out


def materials():
    bark = common.material("M_Bark", base=common.color("trunk"), roughness=0.9, use_vcol=True)
    leaves = common.material("M_Leaves", base=common.color("leaf_mid"), roughness=0.8, use_vcol=True,
                             translucency=0.32, specular=0.12)
    # light through the leaves comes out warmer and brighter than the leaf colour (backlit glow)
    nt = leaves.node_tree
    vc = next(n for n in nt.nodes if n.type == "VERTEX_COLOR")
    tr = next(n for n in nt.nodes if n.type == "BSDF_TRANSLUCENT")
    warm = nt.nodes.new("ShaderNodeMix")
    warm.data_type = "RGBA"
    warm.blend_type = "MULTIPLY"
    warm.location = (-200, -300)
    warm.inputs["Factor"].default_value = 1.0
    warm.inputs["B"].default_value = (1.5, 1.45, 0.8, 1.0)
    nt.links.new(vc.outputs["Color"], warm.inputs["A"])
    nt.links.new(warm.outputs["Result"], tr.inputs["Color"])
    return bark, leaves


# ---------------------------------------------------------------- placement
def _ground_z(x, y, sink=0.2, r=0.7):
    """Terrain height minus `sink`, lowered further on slopes so the downhill side never floats."""
    h = common.terrain_height(x, y)
    around = min(common.terrain_height(x + dx, y + dy) for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)))
    return h - sink - max(0.0, h - around)


def _view_limit(x, y, rho):
    """Max tree top (world z) so a canopy in front of the house stays under the line of sight from a
    camera at VIEW_AZIMUTH looking down >= 33 degrees at the house; None when the tree is not in the way."""
    az = math.radians(common.VIEW_AZIMUTH)
    vx, vy = math.cos(az), math.sin(az)
    dx, dy = x - common.HOUSE_POS.x, y - common.HOUSE_POS.y
    along = dx * vx + dy * vy
    lateral = abs(vx * dy - vy * dx)
    if along <= 0.0 or lateral > rho + 4.0:
        return None
    return common.HOUSE_GROUND + max(0.0, along - 0.4 * rho) * math.tan(math.radians(33.0))


class _Grid:
    def __init__(self, cell):
        self.cell = cell
        self.cells = {}

    def near(self, x, y, r):
        c = self.cell
        n = int(math.ceil(r / c))
        ix, iy = int(math.floor(x / c)), int(math.floor(y / c))
        for i in range(ix - n, ix + n + 1):
            for j in range(iy - n, iy + n + 1):
                yield from self.cells.get((i, j), ())

    def add(self, item, x, y):
        self.cells.setdefault((int(math.floor(x / self.cell)), int(math.floor(y / self.cell))), []).append(item)


def _pick_variant(rng, sd):
    names = ("Round", "Tall", "Broad", "Small")
    w = (0.32, 0.3, 0.2, 0.18) if sd < 7.0 else (0.3, 0.2, 0.38, 0.12)
    return rng.choices(names, w)[0]


def _scatter_forest(rng, meshes):
    lim = common.TERRAIN_HALF - 2.0
    by_variant = {}
    for info in meshes.values():
        if info["kind"] == "tree":
            by_variant.setdefault(info["variant"], info)
    lo, hi = FOREST_SPACING
    grid = _Grid(3.0)
    placed = []
    for _ in range(40000):
        x, y = rng.uniform(-lim, lim), rng.uniform(-lim, lim)
        sd = common.clearing_signed_distance(x, y)
        if sd <= -1.0 or common.pond_signed_distance(x, y) <= 2.5 or common.house_distance(x, y) <= 7.0:
            continue
        variant = _pick_variant(rng, sd)
        scale = rng.triangular(0.75, 1.3, 1.12)
        z = _ground_z(x, y)
        limit = _view_limit(x, y, by_variant[variant]["radius"] * scale)
        if limit is not None:
            fits = None
            for v in (variant, "Round", "Small"):
                s = min(scale, (limit - z) / by_variant[v]["height"])
                if s >= 0.75:
                    fits = (v, s)
                    break
            if fits is None:
                continue
            variant, scale = fits
        rho = by_variant[variant]["radius"] * scale
        ok = True
        for (qx, qy, qrho) in grid.near(x, y, hi):
            d = min(hi, max(lo, 0.56 * (rho + qrho)))
            if (x - qx) ** 2 + (y - qy) ** 2 < d * d:
                ok = False
                break
        if not ok:
            continue
        grid.add((x, y, rho), x, y)
        placed.append(dict(x=x, y=y, z=z, variant=variant, scale=scale, rot=rng.uniform(0, 2 * math.pi),
                           tint=rng.choice(TREE_TINTS)))
    return placed


# Individual trees inside the clearing: (x, y, variant, tint, scale).
GLADE_TREES = [
    (3.4, 6.9, "Round", "green", 0.95),      # behind the house, right of the roof in the main view
    (-9.6, 13.6, "Tall", "green", 0.9),      # upper terrace meadow
    (-5.6, 14.6, "Round", "yellow", 0.8),
    (-2.4, 13.9, "Small", "teal", 0.85),
    (10.5, 3.8, "Round", "teal", 1.0),       # lone tree in the east meadow
]


def _glade_trees():
    out, notes = [], []
    for (x, y, variant, tint, scale) in GLADE_TREES:
        ok = (common.pond_signed_distance(x, y) > 3.0 and common.house_distance(x, y) > 6.0
              and common.clearing_signed_distance(x, y) < -1.5)
        if not ok:
            notes.append(f"glade tree at ({x}, {y}) rejected by layout checks")
            continue
        out.append(dict(x=x, y=y, z=_ground_z(x, y), variant=variant, scale=scale,
                        rot=(x * 1.7 + y * 0.9) % (2 * math.pi), tint=tint))
    return out, notes


def _scatter_bushes(rng, trees, max_count, tree_radius):
    """A few bushes on the far side of the pond; the rest in small groups tucked under the canopy rim of
    the forest-edge trees, on the side facing the clearing, so they read as undergrowth."""
    lim = common.TERRAIN_HALF - 2.0
    grid = _Grid(2.5)
    for t in trees:
        grid.add((t["x"], t["y"], 1.2), t["x"], t["y"])
    bushes = []

    def try_add(x, y, min_d):
        if abs(x) > lim or abs(y) > lim:
            return False
        if common.pond_signed_distance(x, y) < 1.2 or common.house_distance(x, y) < 6.0:
            return False
        if abs(y - common.terrace_edge_y(x)) < 2.2:
            return False
        for (qx, qy, qd) in grid.near(x, y, 2.6):
            d = max(min_d, qd)
            if (x - qx) ** 2 + (y - qy) ** 2 < d * d:
                return False
        grid.add((x, y, min_d), x, y)
        return True

    def add(x, y, variant, tint, scale):
        bushes.append(dict(x=x, y=y, variant=variant, tint=tint, scale=scale,
                           rot=rng.uniform(0, 2 * math.pi)))

    # a few low ones on the far (west) shore of the pond, between the water and the forest
    pc = common.POND_CENTER
    for deg in (150, 172, 192, 218):
        a = math.radians(deg + rng.uniform(-6, 6))
        r = common.pond_radius_at(a) + rng.uniform(1.3, 2.0)
        x, y = pc.x + r * math.cos(a), pc.y + r * math.sin(a)
        if try_add(x, y, 1.6):
            add(x, y, rng.choice(("Low", "Low", "Tall")), rng.choice(BUSH_TINTS), rng.uniform(0.7, 0.95))
    # groups of 1-3 under the clearing-side rim of the forest-edge trees
    cc = common.CLEARING_CENTER
    edge = [t for t in trees if -1.5 < common.clearing_signed_distance(t["x"], t["y"]) < 4.0]
    rng.shuffle(edge)
    for t in edge:
        if len(bushes) >= max_count:
            break
        if rng.random() > 0.9:
            continue
        dx, dy = cc.x - t["x"], cc.y - t["y"]
        dl = math.hypot(dx, dy) or 1.0
        dx, dy = dx / dl, dy / dl
        rho = tree_radius(t)
        tint = rng.choice(BUSH_TINTS)
        for k in range(rng.choice((1, 2, 2, 3))):
            if len(bushes) >= max_count:
                break
            a = rng.uniform(-1.0, 1.0)
            r = rho * (rng.uniform(0.35, 0.6) if k == 0 else rng.uniform(0.55, 0.9))
            x = t["x"] + r * (dx * math.cos(a) - dy * math.sin(a))
            y = t["y"] + r * (dx * math.sin(a) + dy * math.cos(a))
            if common.pond_signed_distance(x, y) > 3.0 and try_add(x, y, 1.5):
                if k == 0:
                    add(x, y, "Tall", tint, rng.uniform(0.8, 1.05))
                else:
                    add(x, y, rng.choice(("Low", "Low", "Tall")), rng.choice(BUSH_TINTS), rng.uniform(0.7, 1.0))
    for b in bushes:
        b["z"] = _ground_z(b["x"], b["y"], sink=0.12, r=0.5)
    return bushes


def _place(coll, name, me, item):
    ob = bpy.data.objects.new(name, me)
    ob.location = (item["x"], item["y"], item["z"])
    ob.rotation_euler = (0.0, 0.0, item["rot"])
    ob.scale = (item["scale"],) * 3
    coll.objects.link(ob)
    return ob


# ---------------------------------------------------------------- entry point
def build():
    root = common.collection(COLLECTION)
    c_forest = common.collection("Forest_Trees", parent=root)
    c_glade = common.collection("Forest_GladeTrees", parent=root)
    c_bush = common.collection("Forest_Bushes", parent=root)
    mat_bark, mat_leaves = materials()
    meshes = build_variant_meshes(mat_bark, mat_leaves)

    rng = common.rng(SEED)
    glade, notes = _glade_trees()
    forest = _scatter_forest(rng, meshes)
    bush_room = 40
    keep = MAX_OBJECTS - len(glade) - bush_room
    if len(forest) > keep:
        # drop the trees farthest from the clearing (least visible) to respect the object budget
        cc = common.CLEARING_CENTER
        forest.sort(key=lambda t: math.hypot(t["x"] - cc.x, t["y"] - cc.y))
        notes.append(f"{len(forest) - keep} far forest trees dropped to stay within {MAX_OBJECTS} objects")
        forest = forest[:keep]

    def tree_radius(t):
        return meshes[f"Tree{t['variant']}_{t['tint']}"]["radius"] * t["scale"]

    bushes = _scatter_bushes(rng, forest + glade, min(bush_room, MAX_OBJECTS - len(glade) - len(forest)),
                             tree_radius)

    objects = []
    groups = ((c_forest, "ForestTree_{:03d}", "Tree", forest), (c_glade, "GladeTree_{:02d}", "Tree", glade),
              (c_bush, "Bush_{:03d}", "Bush", bushes))
    for coll, pattern, kind, items in groups:
        for i, it in enumerate(items):
            me = meshes[f"{kind}{it['variant']}_{it['tint']}"]["mesh"]
            objects.append(_place(coll, pattern.format(i), me, it))

    mesh_tris = {name: info["tris"] for name, info in meshes.items()}
    return dict(
        collection=COLLECTION,
        sub_collections=[c_forest.name, c_glade.name, c_bush.name],
        meshes=mesh_tris,
        mesh_sizes={n: (round(i["height"], 2), round(i["radius"], 2)) for n, i in meshes.items()},
        instances=len(objects),
        forest_trees=len(forest), glade_trees=len(glade), bushes=len(bushes),
        triangles_unique=sum(mesh_tris.values()),
        triangles_total=sum(mesh_tris[ob.data.name] for ob in objects),
        notes=notes,
    )
