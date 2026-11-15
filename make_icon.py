#!/usr/bin/env python3
"""Generates app.ico (black tile, white beamed music notes) using only the standard library."""
import math, struct, sys, zlib
from pathlib import Path

BG, FG, EDGE = (10, 10, 10), (255, 255, 255), (85, 85, 85)
SS = 4  # supersampling per axis


def rot(px, py, cx, cy, deg):
    a = math.radians(deg)
    dx, dy = px - cx, py - cy
    return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)


def in_head(x, y, cx, cy):
    u, v = rot(x, y, cx, cy, -20)
    return (u / 0.118) ** 2 + (v / 0.086) ** 2 <= 1.0


def in_rect(x, y, x0, x1, y0, y1):
    return x0 <= x <= x1 and y0 <= y <= y1


def in_beam(x, y):
    if not (0.425 <= x <= 0.825):
        return False
    top = 0.28 + (x - 0.425) * (0.20 - 0.28) / 0.40
    return top <= y <= top + 0.095


def note(x, y):
    return (in_head(x, y, 0.33, 0.71) or in_head(x, y, 0.69, 0.63)
            or in_rect(x, y, 0.425, 0.468, 0.27, 0.71) or in_rect(x, y, 0.785, 0.828, 0.19, 0.63)
            or in_beam(x, y))


def tile(x, y):
    """0 = outside, 1 = edge ring, 2 = fill (rounded square)."""
    r, m = 0.20, 0.02
    px, py = min(max(x, m + r), 1 - m - r), min(max(y, m + r), 1 - m - r)
    d = math.hypot(x - px, y - py)
    if d > r:
        return 0
    return 1 if d > r - 0.018 else 2


def render(size):
    rows = []
    n = SS * SS
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            ar = ag = ab = aa = 0
            for sj in range(SS):
                for si in range(SS):
                    x, y = (i + (si + 0.5) / SS) / size, (j + (sj + 0.5) / SS) / size
                    t = tile(x, y)
                    if t == 0:
                        continue
                    c = EDGE if t == 1 else (FG if note(x, y) else BG)
                    ar += c[0]; ag += c[1]; ab += c[2]; aa += 255
            if aa:
                cov = aa // 255
                row += bytes((ar // cov, ag // cov, ab // cov, aa // n))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return b"".join(rows)


def png(size):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = render(size)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build(path):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    Path(path).write_bytes(head + entries + b"".join(images))
    return images[-1]


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("app.ico")
    big = build(out)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_bytes(big)
    print(f"Wrote {out}")
