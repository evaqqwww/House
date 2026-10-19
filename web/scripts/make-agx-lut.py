"""Bakes Blender's view transform (AgX, look 'AgX - Base Contrast', sRGB display) into the 3D LUT the web viewer's
Look pass uses (src/agx-lut.png), so the web forms the image exactly as the Cycles renders do.

Runs in Blender's Python (it ships PyOpenColorIO and the colour management config):
    blender -b --factory-startup -P D:/House/web/scripts/make-agx-lut.py

LUT input: Blender's own AgX shaper. Scene-linear Rec.709 -> linear FilmLight E-Gamut (EGAMUT below, rows), then
t = (log2(E) - LOG_MIN) / (LOG_MAX - LOG_MIN) per channel; the grid nodes are the first SIZE nodes of Blender's
57-node AgX LUT (log2 from -12.47 to about +6.7: brighter values are display white anyway).
LUT output: the display-encoded sRGB value (0-255) of the node.
PNG layout: width SIZE, height SIZE * SIZE, RGB 8 bit; row = g + SIZE * b, column = r (Data3DTexture order).
"""
import os
import struct
import zlib

import bpy
import PyOpenColorIO as OCIO

SIZE = 44
BLENDER_NODES = 57                      # AgX_Base_sRGB.cube
LOG_MIN = -12.47393
LOG_SPAN = 25.0                         # AllocationTransform lg2 [-12.47393, 12.5260688117]
LOG_MAX = LOG_MIN + LOG_SPAN * (SIZE - 1) / (BLENDER_NODES - 1)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "agx-lut.png")


def processors():
    cfg = OCIO.Config.CreateFromFile(os.path.join(bpy.utils.resource_path("LOCAL"), "datafiles", "colormanagement",
                                                  "config.ocio"))
    view = OCIO.GroupTransform([
        OCIO.LookTransform(src="Linear Rec.709", dst="Linear Rec.709", looks="AgX - Base Contrast"),
        OCIO.DisplayViewTransform(src="Linear Rec.709", display="sRGB", view="AgX"),
    ])
    to_e = cfg.getProcessor("Linear Rec.709", "Linear FilmLight E-Gamut").getDefaultCPUProcessor()
    from_e = cfg.getProcessor("Linear FilmLight E-Gamut", "Linear Rec.709").getDefaultCPUProcessor()
    return cfg.getProcessor(view).getDefaultCPUProcessor(), to_e, from_e


def png_rgb8(path, width, height, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    view, to_e, from_e = processors()
    egamut = [to_e.applyRGB(c) for c in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])]  # columns
    node = [2.0 ** (LOG_MIN + (LOG_MAX - LOG_MIN) * i / (SIZE - 1)) for i in range(SIZE)]
    rows = []
    for b in range(SIZE):
        for g in range(SIZE):
            row = bytearray()
            for r in range(SIZE):
                lin = from_e.applyRGB([node[r], node[g], node[b]])
                out = view.applyRGB(lin)
                row += bytes(max(0, min(255, round(v * 255.0))) for v in out)
            rows.append(row)
    png_rgb8(os.path.normpath(OUT), SIZE, SIZE * SIZE, rows)
    m = [[egamut[c][r] for c in range(3)] for r in range(3)]
    print("agx-lut:", os.path.normpath(OUT), f"size {SIZE}, log2 [{LOG_MIN}, {LOG_MAX:.6f}]")
    print("EGAMUT rows:", [[round(x, 7) for x in row] for row in m])


main()
