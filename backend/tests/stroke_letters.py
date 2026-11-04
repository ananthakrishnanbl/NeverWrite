"""A small hand-made stroke alphabet for tests and demos.

Each character is a list of strokes in millimetres of the Draw page's writing
box (18 x 21 mm, y down, baseline at 13 mm, x-height line at 9 mm), as the
browser would send them. ``letter(ch, variant)`` adds a small, seeded wobble
so the three variants of a character differ the way handwriting does.

These are simple print shapes, not anyone's handwriting.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Tuple

Point = Tuple[float, float]
Stroke = List[Point]


def arc(cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, n: int = 28) -> Stroke:
    """Points on an ellipse from angle *a0* to *a1* in degrees (y down, so increasing runs clockwise)."""
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def smooth(points: List[Point], steps: int = 8) -> Stroke:
    """Catmull-Rom curve through *points*."""
    if len(points) < 3:
        return list(points)
    padded = [points[0]] + list(points) + [points[-1]]
    out: Stroke = []
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1:i + 3]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    out.append(points[-1])
    return out


def _arch(x0: float, x1: float, top: float = 9.2, spring: float = 10.6) -> Stroke:
    """The hump of an n, h or m: up from the stem, over, and down to the baseline."""
    cx, rx = (x0 + x1) / 2, (x1 - x0) / 2
    return arc(cx, spring, rx, spring - top, 180, 360) + [(x1, 13.0)]


_SHAPES: Dict[str, Callable[[], List[Stroke]]] = {
    "a": lambda: [arc(8.6, 11, 1.8, 2, -40, -380), [(10.4, 9.0), (10.4, 12.6), (10.9, 13.1)]],
    "b": lambda: [[(7.3, 5.5), (7.3, 13.0)], arc(8.9, 11, 1.6, 2, 180, 540)],
    "c": lambda: [arc(9, 11, 1.8, 2, -40, -320)],
    "d": lambda: [arc(8.6, 11, 1.7, 2, -30, -390), [(10.3, 5.5), (10.3, 13.0)]],
    "e": lambda: [[(7.2, 11.0), (10.8, 11.0)] + arc(9, 11, 1.8, 2, 0, -310)],
    "f": lambda: [smooth([(10.8, 6.2), (9.6, 5.5), (8.8, 6.4), (8.8, 9.5), (8.8, 13.0)]), [(7.4, 9.0), (10.2, 9.0)]],
    "g": lambda: [arc(8.6, 11, 1.7, 2, -40, -380),
                  smooth([(10.4, 9.0), (10.4, 13.0), (10.2, 15.4), (8.8, 16.4), (7.3, 15.7)])],
    "h": lambda: [[(7.3, 5.5), (7.3, 13.0)], _arch(7.3, 10.6)],
    "i": lambda: [[(9.0, 9.0), (9.0, 13.0)], [(9.0, 7.4)]],
    "j": lambda: [smooth([(9.6, 9.0), (9.6, 13.0), (9.4, 15.6), (8.4, 16.4), (7.4, 15.8)]), [(9.6, 7.4)]],
    "k": lambda: [[(7.3, 5.5), (7.3, 13.0)], [(10.6, 9.0), (7.4, 11.2), (10.8, 13.0)]],
    "l": lambda: [smooth([(8.7, 5.5), (8.7, 11.0), (8.8, 12.6), (9.5, 13.1)])],
    "m": lambda: [[(6.4, 9.0), (6.4, 13.0)], _arch(6.4, 8.9), _arch(8.9, 11.4)],
    "n": lambda: [[(7.3, 9.0), (7.3, 13.0)], _arch(7.3, 10.6)],
    "o": lambda: [arc(9, 11, 1.8, 2, -90, -450)],
    "p": lambda: [[(7.3, 9.0), (7.3, 16.6)], arc(8.9, 11, 1.6, 2, 180, 540)],
    "q": lambda: [arc(8.6, 11, 1.7, 2, -30, -390), [(10.3, 9.0), (10.3, 16.6)]],
    "r": lambda: [[(7.6, 9.0), (7.6, 13.0)], smooth([(7.6, 10.8), (8.4, 9.5), (9.4, 9.0), (10.3, 9.3)])],
    "s": lambda: [smooth([(10.4, 9.6), (9.4, 9.0), (8.0, 9.2), (7.7, 10.1), (8.6, 10.8), (9.9, 11.4),
                          (10.4, 12.3), (9.7, 13.0), (8.3, 13.1), (7.4, 12.5)])],
    "t": lambda: [smooth([(8.6, 6.2), (8.6, 12.2), (9.1, 13.0), (10.2, 12.8)]), [(7.2, 9.0), (10.2, 9.0)]],
    "u": lambda: [[(7.3, 9.0), (7.3, 11.4)] + arc(8.95, 11.4, 1.65, 1.6, 180, 0) + [(10.6, 9.0)],
                  [(10.6, 9.0), (10.6, 13.0)]],
    "v": lambda: [[(7.2, 9.0), (9.0, 13.0), (10.8, 9.0)]],
    "w": lambda: [[(6.3, 9.0), (7.6, 13.0), (9.0, 10.0), (10.4, 13.0), (11.7, 9.0)]],
    "x": lambda: [[(7.3, 9.0), (10.7, 13.0)], [(10.7, 9.0), (7.3, 13.0)]],
    "y": lambda: [[(7.3, 9.0), (9.0, 13.0)], smooth([(10.7, 9.0), (9.4, 13.0), (8.4, 15.6), (7.4, 16.4)])],
    "z": lambda: [[(7.3, 9.0), (10.7, 9.0), (7.3, 13.0), (10.7, 13.0)]],
    "A": lambda: [[(6.2, 13.0), (9.0, 6.0), (11.8, 13.0)], [(7.3, 10.4), (10.7, 10.4)]],
    "D": lambda: [[(7.0, 6.0), (7.0, 13.0)],
                  smooth([(7.0, 6.0), (9.5, 6.3), (11.3, 8.2), (11.3, 10.8), (9.5, 12.7), (7.0, 13.0)])],
    "H": lambda: [[(6.6, 6.0), (6.6, 13.0)], [(11.4, 6.0), (11.4, 13.0)], [(6.6, 9.6), (11.4, 9.6)]],
    "M": lambda: [[(6.0, 13.0), (6.4, 6.0), (9.0, 11.0), (11.6, 6.0), (12.0, 13.0)]],
    "T": lambda: [[(5.8, 6.0), (12.2, 6.0)], [(9.0, 6.0), (9.0, 13.0)]],
    "W": lambda: [[(5.6, 6.0), (7.2, 13.0), (9.0, 8.0), (10.8, 13.0), (12.4, 6.0)]],
    ".": lambda: [[(9.0, 12.8)]],
    ",": lambda: [[(9.2, 12.6), (8.6, 14.2)]],
    "!": lambda: [[(9.0, 6.0), (9.0, 11.0)], [(9.0, 12.8)]],
    "'": lambda: [[(9.0, 6.0), (8.8, 7.6)]],
}

CHARS = "".join(_SHAPES)


def letter(ch: str, variant: int = 0) -> List[List[List[float]]]:
    """Strokes for *ch* as the browser sends them, with a wobble seeded by the character and variant."""
    rng = random.Random(f"{ch}-{variant}")
    angle = math.radians(rng.uniform(-3, 3))
    scale = 1 + rng.uniform(-0.04, 0.04)
    dx, dy = rng.uniform(-0.3, 0.3), rng.uniform(-0.15, 0.15)
    cx, cy = 9.0, 13.0  # turn about the middle of the baseline
    out = []
    for stroke in _SHAPES[ch]():
        pts = []
        for x, y in stroke:
            x, y = (x - cx) * scale, (y - cy) * scale
            x, y = x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)
            pts.append([round(x + cx + dx, 3), round(y + cy + dy, 3)])
        out.append(pts)
    return out
