"""Builds the whole forest-cottage scene in the running Blender session and saves D:\\House\\house.blend
(Blender's house.blend1 backup of the previous save is removed).

In Blender's Python console or via MCP:
    import sys; sys.dont_write_bytecode = True; sys.path.insert(0, r"D:\\House\\scripts")
    import build_scene; build_scene.build()
"""
import importlib
import os
import sys
import time

import bpy

sys.dont_write_bytecode = True
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import common  # noqa: E402

PARTS = ["build_terrain", "build_house", "build_trees", "build_lighting"]
BLEND_PATH = os.path.join(common.ROOT, "house.blend")


def build(save=True, samples=256, parts=None):
    importlib.reload(common)
    report = {}
    for name in parts or PARTS:
        mod = importlib.import_module(name)
        importlib.reload(mod)
        t = time.time()
        info = mod.build(samples=samples) if name == "build_lighting" else mod.build()
        report[name] = {"seconds": round(time.time() - t, 1), "info": info}
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    if save:
        bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
        if os.path.exists(BLEND_PATH + "1"):
            os.remove(BLEND_PATH + "1")
    return report
