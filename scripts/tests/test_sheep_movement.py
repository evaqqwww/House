"""Exercise the saved runtime while replacing only Blender's host boundary."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest


class Root:
    def __init__(self, navigation, yaw=0):
        self.navigation = navigation
        self.rotation_euler = SimpleNamespace(z=yaw)
        self.location = (0, 0, 0)

    def __getitem__(self, key):
        if key == 'navigation':
            return self.navigation
        raise KeyError(key)

    @property
    def location(self):
        return self._location

    @location.setter
    def location(self, value):
        self._location = SimpleNamespace(x=value[0], y=value[1], z=value[2])


def runtime(points, yaw=0, speed=1):
    """Load the real script with a controllable clock and root transform."""
    import json

    # Public navigation coordinates are x, height, -world_y.
    points = [[x, z, -y] for x, y, z in points]
    nav = {'nodes': [points[0], points[-1]],
           'edges': [{'a': 0, 'b': 1, 'points': points}],
           'pause': [0, 0], 'speed': speed}
    root = Root(json.dumps(nav), yaw)
    registered = []
    scene = {'sheep_enabled': True}
    bpy = SimpleNamespace(
        app=SimpleNamespace(
            driver_namespace={}, is_job_running=lambda _: False,
            timers=SimpleNamespace(is_registered=lambda _: False,
                                   register=lambda tick, **_: registered.append(tick))),
        data=SimpleNamespace(objects={'Sheep_Root': root},
                             filepath='D:/House/house.blend', screens=[]),
        context=SimpleNamespace(scene=scene, mode='OBJECT'))
    clock = SimpleNamespace(now=0.0)
    path = Path(__file__).resolve().parents[1] / 'sheep_movement.py'
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.Import)
                         and any(alias.name in {'bpy', 'time'} for alias in node.names))]
    namespace = {'bpy': bpy, 'time': SimpleNamespace(monotonic=lambda: clock.now)}
    exec(compile(tree, str(path), 'exec'), namespace)

    def advance(dt):
        clock.now += dt
        return registered[0]()

    return root, advance, scene


class SheepMovementTests(unittest.TestCase):
    def test_turns_at_constant_rate_while_translating(self):
        root, advance, _ = runtime([(0, 0, 0), (0, 10, 0)])
        advance(0.1)
        self.assertAlmostEqual(root.rotation_euler.z, math.pi / 20)
        # Nine degrees of progress leaves 81 degrees of heading error.
        self.assertAlmostEqual(root.location.y, 0.03673258487801732)
        advance(0.1)
        self.assertAlmostEqual(root.rotation_euler.z, math.pi / 10)

    def test_reversal_retains_quarter_speed_while_turning(self):
        root, advance, _ = runtime([(0, 0, 0), (-10, 0, 0)])
        advance(0.1)
        self.assertAlmostEqual(abs(root.rotation_euler.z), math.pi / 20)
        self.assertAlmostEqual(root.location.x, -0.025)

    def test_wraps_to_shortest_arc_and_clamps_final_step(self):
        heading = math.radians(-179)
        root, advance, _ = runtime(
            [(0, 0, 0), (10 * math.cos(heading), 10 * math.sin(heading), 0)],
            yaw=math.radians(179))
        advance(0.1)
        self.assertAlmostEqual(root.rotation_euler.z, heading)
        self.assertAlmostEqual(math.hypot(root.location.x, root.location.y), 0.1)

    def test_uses_one_turn_budget_across_multiple_waypoints(self):
        root, advance, _ = runtime(
            [(0, 0, 0), (0, 0.01, 0), (10, 0.01, 0)])
        advance(0.1)
        self.assertAlmostEqual(root.rotation_euler.z, math.pi / 20)
        self.assertAlmostEqual(root.location.y, 0.01)
        self.assertAlmostEqual(root.location.x, 0.02673258487801732)

    def test_coincident_points_do_not_hide_next_heading(self):
        root, advance, _ = runtime([(0, 0, 0), (0, 0, 0), (0, 10, 0)])
        advance(0.1)
        self.assertAlmostEqual(root.rotation_euler.z, math.pi / 20)
        self.assertGreater(root.location.y, 0)

    def test_vertical_travel_keeps_yaw_and_follows_height(self):
        root, advance, _ = runtime([(0, 0, 0), (0, 0, 10)], yaw=1.2)
        advance(0.1)
        self.assertAlmostEqual(root.rotation_euler.z, 1.2)
        self.assertAlmostEqual(root.location.z, 0.1)

    def test_aligned_travel_keeps_full_speed_and_terrain_polyline(self):
        root, advance, _ = runtime([(0, 0, 0), (0.03, 0, 0.04), (10, 0, 0.04)])
        advance(0.1)
        self.assertAlmostEqual(root.location.x, 0.08)
        self.assertAlmostEqual(root.location.z, 0.04)

    def test_pause_and_time_cap_remain_effective(self):
        root, advance, scene = runtime([(0, 0, 0), (10, 0, 0)])
        scene['sheep_enabled'] = False
        advance(1)
        self.assertAlmostEqual(root.location.x, 0)
        scene['sheep_enabled'] = True
        advance(1)
        self.assertAlmostEqual(root.location.x, 0.1)


if __name__ == '__main__':
    unittest.main()
