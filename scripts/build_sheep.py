"""Minimal Blender sheep and a collision-cleared, ground-following meadow graph.

The moving sheep is exported separately so its shadows never enter the static bakes.
"""
import heapq
import json
import math
import os

import bpy
import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

import common as C

RADIUS = 1.08  # whole animal at any heading, plus at least 18 cm of clearance
STEP = 0.4
BOUNDS = (-7.6, 8.0, -8.0, 6.4)
CREAM = '#f4eee3'
PURPLE = '#76527f'


def _wool_body(builder):
    """One continuous pear-shaped fleece, lofted into a raised front hood (+X)."""
    # Tangent-continuous side contours keep the rump, belly and hood softly rounded.
    upper = [((-0.68,0.55),(-0.68,0.90),(-0.50,1.08),(-0.26,1.05)),
             ((-0.26,1.05),(-0.10,1.06),(0.00,1.02),(0.10,1.025)),
             ((0.10,1.025),(0.22,1.031),(0.22,1.17),(0.37,1.17)),
             ((0.37,1.17),(0.52,1.17),(0.66,1.08),(0.66,0.89))]
    lower = [((-0.68,0.55),(-0.68,0.22),(-0.60,0.08),(-0.40,0.08)),
             ((-0.40,0.08),(-0.10,0.04),(0.15,0.04),(0.30,0.08)),
             ((0.30,0.08),(0.60,0.08),(0.66,0.35),(0.66,0.89))]
    def contour(x,segments):
        a,b,c,d = next((s for s in segments if x <= s[-1][0]+1e-8),segments[-1])
        lo,hi = 0.0,1.0
        for _ in range(24):
            t = (lo+hi)/2;u = 1-t
            xx = u*u*u*a[0]+3*u*u*t*b[0]+3*u*t*t*c[0]+t*t*t*d[0]
            if xx < x:lo=t
            else:hi=t
        t = (lo+hi)/2;u=1-t
        return u*u*u*a[1]+3*u*u*t*b[1]+3*u*t*t*c[1]+t*t*t*d[1]
    sections = []
    for i in range(73):
        q = -1+2*i/72
        x = -0.01+0.67*q
        bottom,top = contour(x,lower),contour(x,upper)
        width = 0.49*max(0,1-abs(q)**2.5)**(1/2.5)
        sections.append((x,(top+bottom)/2,width,(top-bottom)/2))
    verts = [(sections[0][0],0,sections[0][1])]
    faces, rings = [], []
    n = 48
    for x,zc,width,height in sections[1:-1]:
        ring = []
        for j in range(n):
            theta = j*2*math.pi/n
            s,c = math.sin(theta),math.cos(theta)
            # The continuously rounded section is fuller below and narrower at the shoulders.
            y = max(0,width)*c*(1-0.14*s)
            z = zc+max(0,height)*s
            ring.append(len(verts))
            verts.append((x,y,z))
        rings.append(ring)
    for j in range(n):
        faces.append((0,rings[0][(j+1)%n],rings[0][j]))
    for a,b in zip(rings,rings[1:]):
        for j in range(n):
            k = (j+1)%n
            faces.append((a[j],a[k],b[k],b[j]))
    end = len(verts)
    verts.append((sections[-1][0],0,sections[-1][1]))
    for j in range(n):
        faces.append((rings[-1][j],rings[-1][(j+1)%n],end))
    builder.geometry(verts,faces,col=C.color(CREAM))


def _ellipsoid(builder,pos,scale,rot=(0,0,0),colour=PURPLE):
    from mathutils import Euler
    matrix = Matrix.Translation(pos)@Euler(rot,'XYZ').to_matrix().to_4x4()@Matrix.Diagonal((*scale,1))
    res = bmesh.ops.create_uvsphere(builder.bm,u_segments=32,v_segments=20,radius=1,matrix=matrix)
    builder.paint({f for v in res['verts'] for f in v.link_faces},C.color(colour))


def _hull(points):
    pts = sorted(set((round(p[0], 5), round(p[1], 5)) for p in points))
    if len(pts) < 3:
        return pts
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    low, high = [], []
    for p in pts:
        while len(low) > 1 and cross(low[-2], low[-1], p) <= 0:
            low.pop()
        low.append(p)
    for p in reversed(pts):
        while len(high) > 1 and cross(high[-2], high[-1], p) <= 0:
            high.pop()
        high.append(p)
    return low[:-1] + high[:-1]


def _point_segment(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy) / max(dx*dx+dy*dy, 1e-12)))
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _intersects(a, b, c, d):
    if (max(a[0],b[0]) < min(c[0],d[0]) or max(c[0],d[0]) < min(a[0],b[0])
            or max(a[1],b[1]) < min(c[1],d[1]) or max(c[1],d[1]) < min(a[1],b[1])):
        return False
    return _cross(a,b,c)*_cross(a,b,d) <= 0 and _cross(c,d,a)*_cross(c,d,b) <= 0


def _inside(p, poly):
    return all(_cross(poly[i-1], q, p) >= -1e-8 for i, q in enumerate(poly))


def _segment_distance(a, b, c, d):
    if _intersects(a,b,c,d):
        return 0.0
    return min(_point_segment(a,c,d), _point_segment(b,c,d),
               _point_segment(c,a,b), _point_segment(d,a,b))


def obstacles():
    """Convex footprints per actual connected rock, and conservative solid house/shore/bush footprints."""
    out = []
    def add(name, pts):
        poly = _hull(pts)
        if len(poly) >= 3:
            out.append({'name':name, 'polygon':poly,
                        'bounds':(min(p[0] for p in poly),max(p[0] for p in poly),
                                  min(p[1] for p in poly),max(p[1] for p in poly))})
    house = bpy.data.collections.get('House')
    add('House', [o.matrix_world@v.co for o in house.all_objects
                  if o.type == 'MESH' and o.name != 'House_Smoke' for v in o.data.vertices])
    water = bpy.data.objects['PondWater']
    add('Pond', [water.matrix_world@v.co for v in water.data.vertices])
    for o in bpy.data.collections['Rocks'].all_objects:
        if o.type != 'MESH':
            continue
        links = [[] for _ in o.data.vertices]
        for e in o.data.edges:
            a,b = e.vertices
            links[a].append(b)
            links[b].append(a)
        seen = set()
        for start in range(len(links)):
            if start in seen:
                continue
            stack, component = [start], []
            seen.add(start)
            while stack:
                k = stack.pop()
                component.append(o.matrix_world@o.data.vertices[k].co)
                for v in links[k]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            add(o.name, component)
    for o in bpy.data.collections['Forest_Bushes'].all_objects:
        add(o.name, [o.matrix_world@v.co for v in o.data.vertices])
    for cn in ('Forest_GladeTrees', 'Forest_Trees'):
        for o in bpy.data.collections[cn].all_objects:
            if o.type != 'MESH':
                continue
            x,y,z = o.matrix_world.translation
            if not (-11 < x < 12 and -12 < y < 10):
                continue
            pts = [o.matrix_world@v.co for v in o.data.vertices]
            # Only trunk/low branches: a high tree canopy is not an obstacle for this 1 m sheep.
            add(o.name, [p for p in pts if z+0.15 < p.z < z+1.3])
    return out


def _clear(a, b, obs, radius=RADIUS):
    for ob in obs:
        x0,x1,y0,y1 = ob['bounds']
        if (max(a[0],b[0])+radius < x0 or min(a[0],b[0])-radius > x1
                or max(a[1],b[1])+radius < y0 or min(a[1],b[1])-radius > y1):
            continue
        poly = ob['polygon']
        if _inside(a,poly) or _inside(b,poly):
            return False
        if any(_segment_distance(a,b,poly[i-1],p) <= radius for i,p in enumerate(poly)):
            return False
    return True


def navigation():
    obs = obstacles()
    terrain = bpy.data.objects.get('Terrain_Bake') or bpy.data.objects['Terrain']
    bvh = BVHTree.FromObject(terrain, bpy.context.evaluated_depsgraph_get())
    heights = {}
    def height(p):
        key = (round(p[0],6),round(p[1],6))
        if key not in heights:
            world = Vector((p[0],p[1],20))
            loc = bvh.ray_cast(terrain.matrix_world.inverted()@world, Vector((0,0,-1)))[0]
            if loc is None:
                raise RuntimeError('Sheep waypoint outside terrain')
            heights[key] = (terrain.matrix_world@loc).z
        return heights[key]
    def walkable(a,b):
        if not _clear(a,b,obs):
            return False
        n = max(1, math.ceil(math.dist(a,b)/0.15))
        last = height(a)
        for i in range(1,n+1):
            p = (a[0]+(b[0]-a[0])*i/n,a[1]+(b[1]-a[1])*i/n)
            h = height(p)
            if abs(h-last) > math.dist(a,b)/n*0.38:
                return False
            last = h
        return True
    x0,x1,y0,y1 = BOUNDS
    coords = {(i,j):(round(x0+i*STEP,4),round(y0+j*STEP,4))
              for i in range(round((x1-x0)/STEP)+1) for j in range(round((y1-y0)/STEP)+1)}
    coords = {k:p for k,p in coords.items() if _clear(p,p,obs)}
    adj = {k:[] for k in coords}
    for k,a in coords.items():
        for di,dj in ((1,0),(0,1),(1,1),(1,-1)):
            nk = (k[0]+di,k[1]+dj)
            if nk in coords and walkable(a,coords[nk]):
                adj[k].append(nk)
                adj[nk].append(k)
    # Retain one connected grass region; no inaccessible destinations or cliff jumps.
    remaining, groups = set(coords), []
    while remaining:
        q = [remaining.pop()]
        component = set(q)
        while q:
            for k in adj[q.pop()]:
                if k in remaining:
                    remaining.remove(k)
                    component.add(k)
                    q.append(k)
        groups.append(component)
    region = max(groups,key=len)
    anchors = [(2.8,-6.0),(-0.8,-6.0),(-4.5,-5.8),(-6.4,-3.2),
               (6.0,-5.4),(6.3,-1.2),(6.2,3.5),(0,5.3),(-4.2,5.5)]
    keys = []
    for a in anchors:
        k = min(region,key=lambda k:math.dist(coords[k],a))
        if all(math.dist(coords[k],coords[j]) > 1.6 for j in keys):
            keys.append(k)
    if len(keys) < 5:
        raise RuntimeError('Not enough collision-free sheep destinations')
    def astar(start,end):
        queue = [(0,start)]
        cost,prev = {start:0}, {}
        while queue:
            _,k = heapq.heappop(queue)
            if k == end:
                path = [end]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                return [coords[k] for k in reversed(path)]
            for nk in adj[k]:
                c = cost[k]+math.dist(coords[k],coords[nk])
                if c < cost.get(nk,float('inf')):
                    cost[nk],prev[nk] = c,k
                    heapq.heappush(queue,(c+math.dist(coords[nk],coords[end]),nk))
        raise RuntimeError('Disconnected sheep route')
    pairs = {(a,b) for a in range(len(keys)) for b in range(a+1,len(keys))
             if math.dist(coords[keys[a]],coords[keys[b]]) < 4.8}
    # Minimum spanning tree also guarantees connectivity when an anchor is isolated.
    connected = {0}
    while len(connected) < len(keys):
        a,b = min(((a,b) for a in connected for b in range(len(keys)) if b not in connected),
                  key=lambda ab:math.dist(coords[keys[ab[0]]],coords[keys[ab[1]]]))
        pairs.add(tuple(sorted((a,b))))
        connected.add(b)
    edges = []
    def to_web(p):
        return [round(p[0],4),round(height(p)+0.015,5),round(-p[1],4)]
    for a,b in sorted(pairs):
        path = astar(keys[a],keys[b])
        simple = [path[0]]
        i = 0
        while i < len(path)-1:
            j = len(path)-1
            while j > i+1 and not walkable(path[i],path[j]):
                j -= 1
            simple.append(path[j])
            i = j
        pts = [simple[0]]
        for p,q in zip(simple,simple[1:]):
            n = max(1,math.ceil(math.dist(p,q)/0.15))
            pts.extend([(p[0]+(q[0]-p[0])*i/n,p[1]+(q[1]-p[1])*i/n) for i in range(1,n+1)])
        assert all(walkable(p,q) for p,q in zip(pts,pts[1:])), 'Unsafe sheep edge'
        edges.append({'a':a,'b':b,'points':[to_web(p) for p in pts]})
    initial = min(range(len(keys)),key=lambda i:math.dist(coords[keys[i]],(4.8,2.4)))
    return {'version':1,'radius':RADIUS,'speed':0.45,'pause':[3,6],'initialNode':initial,
            'nodes':[to_web(coords[k]) for k in keys],'edges':edges,
            'validation':{'obstacles':len(obs),'connectedGroundCells':len(region),
                          'maxSampleSpacing':0.15,'maxSlope':0.38}}


def build():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    coll = C.collection('Sheep')
    root = bpy.data.objects.new('Sheep_Root',None)
    coll.objects.link(root)
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.18
    mb = C.MeshBuilder()
    _wool_body(mb)
    # Rounded featureless face; a fuller depth gives the cheek a continuous convex side arc.
    _ellipsoid(mb,(0.645,0,0.89),(0.185,0.185,0.18),rot=(0,-0.22,0))
    # Short plump ears sit behind the face, with their tips slightly lower and swept back.
    for side in (-1,1):
        _ellipsoid(mb,(0.555,side*0.225,0.91),(0.085,0.10,0.07),
                   rot=(-side*0.15,0,side*0.16))
    # Keep the user's legless design grounded at the root origin.
    bottom = min(v.co.z for v in mb.bm.verts)
    for v in mb.bm.verts:
        v.co.z -= bottom
    mat = C.material('M_Sheep',roughness=0.88,use_vcol=True,specular=0.25)
    model = mb.to_object('Sheep_Model',coll,[mat],smooth=True)
    model.parent = root
    model['parts'] = 'Continuous_Fleece_Hood;Purple_Face;Ear_Left;Ear_Right'
    model['shade'] = 'sheep'
    root['description'] = 'Four-view reference sheep: continuous cream fleece and hood, purple featureless face and plump ears. No limbs or keyframes.'
    nav = navigation()
    root['navigation'] = json.dumps(nav,separators=(',',':'))
    x,z,y = nav['nodes'][nav['initialNode']]
    root.location = (x,-y,z)
    root.rotation_euler.z = -0.55
    bpy.context.scene['sheep_enabled'] = True
    # Keep the movement source with the blend. Blender's normal trusted-script policy applies.
    source = os.path.join(os.path.dirname(__file__),'sheep_movement.py')
    text = bpy.data.texts.get('Sheep_Movement.py') or bpy.data.texts.new('Sheep_Movement.py')
    text.clear()
    with open(source,encoding='utf-8') as f:
        text.write(f.read())
    text.use_module = True
    text.use_fake_user = True
    bpy.context.view_layer.update()
    if not bpy.app.background:
        exec(compile(text.as_string(),text.name,'exec'),{})
    return {'root':root.name,'dimensions':[round(v,3) for v in model.dimensions],
            'destinations':len(nav['nodes']),'routes':len(nav['edges']),
            'triangles':C.triangle_count(model),'clearanceRadius':RADIUS}


def export_web(out):
    root = bpy.data.objects.get('Sheep_Root')
    if root is None:
        build()
        root = bpy.data.objects['Sheep_Root']
    os.makedirs(out,exist_ok=True)
    # Recompute against the current environment whenever exported.
    nav = navigation()
    root['navigation'] = json.dumps(nav,separators=(',',':'))
    navpath = os.path.join(out,'sheep-navigation.json')
    with open(navpath,'w',encoding='utf-8') as f:
        json.dump(nav,f,separators=(',',':'))
        f.write('\n')
    model = bpy.data.objects['Sheep_Model']
    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    matrix = root.matrix_world.copy()
    try:
        for o in selected:
            o.select_set(False)
        root.matrix_world = Matrix.Identity(4)
        root.select_set(True)
        model.select_set(True)
        bpy.context.view_layer.objects.active = model
        bpy.context.view_layer.update()
        bpy.ops.export_scene.gltf(filepath=os.path.join(out,'sheep.glb'),export_format='GLB',
            use_selection=True,export_yup=True,export_animations=False,export_skins=False,
            export_morph=False,export_extras=True,export_cameras=False,export_lights=False,
            export_vertex_color='ACTIVE',export_all_vertex_colors=False,
            export_active_vertex_color_when_no_material=True,export_normals=True,export_tangents=False)
    finally:
        root.matrix_world = matrix
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        for o in selected:
            o.select_set(True)
        bpy.context.view_layer.objects.active = active
        bpy.context.view_layer.update()
    return {n:os.path.getsize(os.path.join(out,n)) for n in ('sheep.glb','sheep-navigation.json')}
