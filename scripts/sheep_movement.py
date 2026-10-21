"""Run this saved Blender Text to start/resume the sheep without timeline animation.

Controls: scene['sheep_enabled'] = False pauses; start() safely replaces an old timer.
Only the root transform changes. Navigation is baked against scene obstacles by build_sheep.
"""
import bpy
import heapq
import json
import math
import random
import time

_KEY = '_house_sheep_timer'


def _turn_and_speed(yaw, dx, dy, dt):
    """Use one 90-degree/second turn budget and coordinate forward speed."""
    if math.hypot(dx, dy) <= 1e-8:
        return yaw, 1.0
    target = math.atan2(dy, dx)
    error = math.atan2(math.sin(target-yaw), math.cos(target-yaw))
    budget = (math.pi/2)*dt
    step = min(max(error, -budget), budget)
    heading = math.atan2(math.sin(yaw+step), math.cos(yaw+step))
    speed_factor = 0.25+0.75*max(0, math.cos(error-step))
    return heading, speed_factor


def start():
    old = bpy.app.driver_namespace.pop(_KEY,None)
    if old and bpy.app.timers.is_registered(old):
        bpy.app.timers.unregister(old)
    root = bpy.data.objects.get('Sheep_Root')
    if root is None:
        return
    nav = json.loads(root['navigation'])
    rng = random.Random()
    node = min(range(len(nav['nodes'])),key=lambda i:math.hypot(
        nav['nodes'][i][0]-root.location.x,nav['nodes'][i][2]+root.location.y))
    adjacency = [[] for _ in nav['nodes']]
    for edge in nav['edges']:
        pts = edge['points']
        dist = sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))
        adjacency[edge['a']].append((edge['b'],dist,pts))
        adjacency[edge['b']].append((edge['a'],dist,list(reversed(pts))))
    # Resume from a known safe node rather than drawing a new unchecked segment from an edited location.
    p = nav['nodes'][node]
    root.location = (p[0],-p[2],p[1])
    state = {'node':node,'path':[],'pause':rng.uniform(*nav['pause']),'last':time.monotonic()}
    def route(a,b):
        q, costs, prev = [(0,a)], {a:0}, {}
        while q:
            cost,k = heapq.heappop(q)
            if k == b:
                paths = []
                while k != a:
                    k,pts = prev[k]
                    paths.append(pts)
                return [p for pts in reversed(paths) for p in pts[1:]]
            for nk,d,pts in adjacency[k]:
                c = cost+d
                if c < costs.get(nk,float('inf')):
                    costs[nk],prev[nk] = c,(k,pts)
                    heapq.heappush(q,(c,nk))
        return []
    def tick():
        now = time.monotonic()
        dt = min(max(now-state['last'],0),0.1)
        state['last'] = now
        ob = bpy.data.objects.get('Sheep_Root')
        if ob is None or bpy.data.filepath.lower().replace('\\','/') != _filepath:
            return None
        if (not bpy.context.scene.get('sheep_enabled',True)
                or bpy.app.is_job_running('RENDER') or bpy.context.mode != 'OBJECT'):
            return 0.2
        if state['pause'] > 0:
            state['pause'] -= dt
            return 0.05
        if not state['path']:
            target = rng.choice([i for i in range(len(nav['nodes'])) if i != state['node']])
            state['path'] = route(state['node'],target)
            state['node'] = target
        # Select the first meaningful segment before spending this tick's turn budget.
        while state['path']:
            p = state['path'][0]
            dx,dy,dz = p[0]-ob.location.x,-p[2]-ob.location.y,p[1]-ob.location.z
            if math.sqrt(dx*dx+dy*dy+dz*dz) > 1e-8:
                break
            ob.location = (p[0],-p[2],p[1])
            state['path'].pop(0)
        speed_factor = 1.0
        if state['path']:
            ob.rotation_euler.z,speed_factor = _turn_and_speed(
                ob.rotation_euler.z, dx, dy, dt)
        remaining = nav['speed']*dt*speed_factor
        while state['path'] and remaining > 1e-8:
            p = state['path'][0]
            dx,dy,dz = p[0]-ob.location.x,-p[2]-ob.location.y,p[1]-ob.location.z
            d = math.sqrt(dx*dx+dy*dy+dz*dz)
            if d <= remaining:
                ob.location = (p[0],-p[2],p[1])
                state['path'].pop(0)
                remaining -= d
            else:
                ob.location.x += dx*remaining/d
                ob.location.y += dy*remaining/d
                ob.location.z += dz*remaining/d
                remaining = 0
        if not state['path']:
            state['pause'] = rng.uniform(*nav['pause'])
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return 0.05
    _filepath = bpy.data.filepath.lower().replace('\\','/')
    bpy.app.driver_namespace[_KEY] = tick
    bpy.app.timers.register(tick,first_interval=0.05)


start()
