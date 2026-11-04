"""Small helpers for pen paths, shared by drawn and traced strokes."""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]


def simplify(points: Sequence[Point], tolerance: float) -> List[Point]:
    """Ramer-Douglas-Peucker: keep the end points and every point that bends the line by more than *tolerance*."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst, worst_i = -1.0, -1
        for i in range(first + 1, last):
            px, py = points[i]
            if length == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / length
            if d > worst:
                worst, worst_i = d, i
        if worst > tolerance:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [p for p, k in zip(points, keep) if k]


def path_length(path: Sequence[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))
