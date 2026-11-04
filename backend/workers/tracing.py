"""Pen paths for scanned letters: trace the centreline of the ink.

A scanned glyph is a picture of ink, but a plotter needs the line the pen
tip followed. This finds it:

1. Decide what is ink: solid ink, plus fainter ink that touches it (so a
   stroke that thins out where the pen skipped stays one stroke), on a
   lightly blurred mask so ragged edges don't sprout branches. Holes too
   narrow to be a letter's counter (slivers where the pen skipped) are filled.
2. Thin each blob to a one-pixel skeleton (Zhang-Suen).
3. Turn the skeleton into a graph of ends, junctions and the pixel runs
   between them, linking pixels by mixed adjacency so a staircase isn't
   mistaken for a junction. Two junctions joined by a short bridge (how a
   crossing thins out) become one. A short branch that barely sticks out past the stroke's
   edge is noise from a blunt or blobby end and is dropped, unless it
   points into a sharp turn: then its tip is where the corner really is
   (the point of a v, the tops of an M), and the stroke is routed through it.
4. At each junction, pair the branches that continue most straight, so a
   crossing becomes two strokes passing through it, the way it was written.
5. Chain the branches into strokes, smooth the pixel staircase (keeping
   corners sharp) and drop redundant points. A blob too small to hold a
   line becomes a dot: one touch of the pen, or a small spiral when the
   blob is wide, so it comes out filled.

The result follows the middle of the ink. Where strokes cross or loop the
pen may take a slightly different route than the writer did, and the order
strokes were written in is unknown (the plotter reorders them anyway).
Letters drawn on the Draw page don't need any of this.

Paths are in mask pixel coordinates (pixel ``(row, col)`` covers
``[col, col + 1) x [row, row + 1)``), like drawn strokes.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import cv2
import numpy as np

from workers.geometry import path_length, simplify

logger = logging.getLogger(__name__)

Point = Tuple[float, float]
Stroke = List[Point]
Pixel = Tuple[int, int]

# Bump when tracing changes, so stored traced paths are traced again when next used
TRACE_VERSION = 1

INK_THRESHOLD = 0.4          # alpha at or above this is solid ink
FAINT_THRESHOLD = 0.15       # fainter ink counts when it touches solid ink
_BLUR_SIGMA = 0.8            # px; smooths ragged edges before thresholding
_MIN_BLOB_PX = 6             # smaller ink blobs are specks
_SPUR_PROTRUSION = 0.7       # a branch sticking out less than this x the half-width past the stroke is noise
_CORNER_MAX_ANGLE = math.radians(140)  # two arms meeting at less than this make a corner
_CORNER_ALIGN = 0.85         # how closely a branch must point out of the corner (cosine, about 30 degrees)
_CORNER_MAX_STUB = 4.0       # a corner's branch is shorter than this x the half-width
_CORNER_MAX_DEVIATION = 0.6  # a corner's straight legs stray from the arms by less than this x the half-width
_PAIR_MAX_TURN = math.radians(50)  # branches through a junction pair if they bend less than this
_BRIDGE_FACTOR = 1.6         # junctions closer than this x their half-width are one crossing
_PINHOLE_RADIUS = 1.5        # px; holes no wider than about twice this are slivers, not counters
_ELONGATED = 1.6            # a small blob this many times longer than wide is a short line, not a dot
_SPIRAL_MIN_RADIUS = 5.0     # px of solid ink; wider dots (over about 1 mm across) are filled with a spiral
_SHARP_TURN = math.radians(125)  # a path bending to an angle tighter than this has a corner the skeleton rounded
_SHARP_REACH = 2.5           # how far out (x the half-width) such a corner may be pushed
_SPIRAL_TURNS = 1.75
_SMOOTH_WINDOW = 2           # moving average over 2k + 1 points
_SIMPLIFY_PX = 0.35
_PAD = 2

# Neighbour offsets clockwise from north: P2..P9 in Zhang-Suen's naming
_RING = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def trace_mask(alpha: np.ndarray) -> List[Stroke]:
    """Centreline strokes for a glyph mask (float alpha in [0, 1], or 0-255)."""
    alpha = np.asarray(alpha, dtype=np.float32)
    if alpha.size and alpha.max() > 1.0:
        alpha = alpha / 255.0
    if alpha.size == 0 or alpha.max() < INK_THRESHOLD:
        return []

    ink = _fill_pinholes(np.pad(ink_mask(alpha), _PAD))
    dist = cv2.distanceTransform(ink, cv2.DIST_L2, 5)
    inside = cv2.dilate(ink, np.ones((3, 3), np.uint8))
    solid = np.pad(alpha >= 0.5, _PAD)
    n, labels = cv2.connectedComponents(ink, connectivity=8)
    skeleton = _thin(ink)

    strokes: List[Stroke] = []
    for label in range(1, n):
        blob = labels == label
        if int(blob.sum()) < _MIN_BLOB_PX:
            continue
        on_skeleton = blob & (skeleton > 0)
        half_width = max(1.0, float(np.median(dist[on_skeleton]))) if on_skeleton.any() else 1.0
        paths, corners = _trace_component(on_skeleton, dist, half_width, inside)
        if not paths or sum(path_length(p) for p in paths) < 3 * half_width:
            # A dot, a short dash or a comma: too small for the skeleton to say much
            strokes.append(_small_mark(blob, blob & solid))
            continue
        for path in paths:
            sharp = _sharpen(_smooth(path, corners), dist, inside, half_width)
            smooth = simplify(sharp, _SIMPLIFY_PX)
            strokes.append([(round(x - _PAD, 2), round(y - _PAD, 2)) for x, y in smooth])
    return strokes


def _sharpen(path: Stroke, dist: np.ndarray, inside: np.ndarray, half_width: float) -> Stroke:
    """Put back the point of corners the skeleton rounded off.

    Where the path bends sharply (the bottom of a v, the corners of a z),
    the skeleton cuts inside the turn. The pen's centre was where the ink
    is one half-width from its outer edge, so the sharpest point of each
    bend moves outwards along the bisector until it gets there, and the
    path runs straight to and from it, as long as those lines stay on the ink.
    """
    if len(path) < 5 or path[0] == path[-1]:
        return path
    h, w = dist.shape

    def dist_at(p: Point) -> float:
        r, c = int(p[1]), int(p[0])
        return float(dist[r, c]) if 0 <= r < h and 0 <= c < w else 0.0

    def along(i: int, step: int) -> Optional[int]:
        """The first point at least ``1.5 * half_width`` away from point *i*, walking by *step*."""
        j = i + step
        while 0 <= j < len(path):
            if math.dist(path[i], path[j]) >= 1.5 * half_width:
                return j
            j += step
        return None

    # The sharpest bend in each run of sharp points
    sharpness: Dict[int, float] = {}
    for i in range(1, len(path) - 1):
        a, b = along(i, -1), along(i, 1)
        if a is None or b is None:
            continue
        u = (path[a][0] - path[i][0], path[a][1] - path[i][1])
        v = (path[b][0] - path[i][0], path[b][1] - path[i][1])
        nu, nv = math.hypot(*u), math.hypot(*v)
        angle = math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (nu * nv))))
        if angle < _SHARP_TURN:
            sharpness[i] = angle
    apexes = []
    for i in sorted(sharpness):
        if apexes and i - apexes[-1] <= 2 or (apexes and math.dist(path[i], path[apexes[-1]]) < 1.5 * half_width):
            if sharpness[i] < sharpness[apexes[-1]]:
                apexes[-1] = i
            continue
        apexes.append(i)

    # From the end backwards, so the indices of earlier points stay valid. A
    # bend whose legs would overlap the one already sharpened is left alone.
    out = list(path)
    limit = len(path)
    for i in reversed(apexes):
        a, b = along(i, -1), along(i, 1)
        if a is None or b is None or b >= limit:
            continue
        p = out[i]
        u = (out[a][0] - p[0], out[a][1] - p[1])
        v = (out[b][0] - p[0], out[b][1] - p[1])
        nu, nv = math.hypot(*u), math.hypot(*v)
        bis = (-(u[0] / nu + v[0] / nv), -(u[1] / nu + v[1] / nv))
        nb = math.hypot(*bis)
        if nb < 1e-6:
            continue
        bis = (bis[0] / nb, bis[1] / nb)
        tip, step = p, 0.25
        travelled = 0.0
        while travelled < _SHARP_REACH * half_width:
            nxt = (tip[0] + bis[0] * step, tip[1] + bis[1] * step)
            if dist_at(nxt) < half_width:
                break
            tip, travelled = nxt, travelled + step
        if tip == p or not (_on_ink(out[a], tip, inside) and _on_ink(tip, out[b], inside)):
            continue
        out = out[:a + 1] + [tip] + out[b:]
        limit = a
    return out


def _small_mark(blob: np.ndarray, solid: np.ndarray) -> Stroke:
    """A blob too small for a skeleton: a short line along it when it is
    elongated (a comma, an apostrophe), otherwise a dot."""
    rows, cols = np.nonzero(solid if solid.any() else blob)
    xs, ys = cols.astype(np.float64) + 0.5 - _PAD, rows.astype(np.float64) + 0.5 - _PAD
    centre = (float(xs.mean()), float(ys.mean()))
    if len(xs) >= 3:
        cov = np.cov(np.stack([xs, ys]))
        values, vectors = np.linalg.eigh(cov)
        minor, major = max(values[0], 1e-6), values[1]
        if major / minor > _ELONGATED * _ELONGATED:
            axis = vectors[:, 1]
            along = (xs - centre[0]) * axis[0] + (ys - centre[1]) * axis[1]
            half_thickness = 2.0 * math.sqrt(minor)  # about the ink's half-width
            lo, hi = along.min() + half_thickness, along.max() - half_thickness
            if hi - lo > 1.0:
                return [(round(centre[0] + lo * axis[0], 2), round(centre[1] + lo * axis[1], 2)),
                        (round(centre[0] + hi * axis[0], 2), round(centre[1] + hi * axis[1], 2))]
    return _dot(centre, math.sqrt(len(xs) / math.pi))


def _dot(centre: Point, radius: float) -> Stroke:
    """One touch of the pen, or for a wide blob a spiral out to half its radius."""
    cx, cy = centre
    if radius <= _SPIRAL_MIN_RADIUS:
        return [(round(cx, 2), round(cy, 2))]
    steps = 16
    out = []
    for i in range(steps + 1):
        t = i / steps
        angle = 2 * math.pi * _SPIRAL_TURNS * t
        r = 0.5 * radius * t
        out.append((round(cx + r * math.cos(angle), 2), round(cy + r * math.sin(angle), 2)))
    return out


def _fill_pinholes(ink: np.ndarray) -> np.ndarray:
    """Fill holes too narrow to be a counter: gaps where the pen skipped or the scan speckled.

    Even the smallest real counter (the loop of an r, the triangle of a 4)
    is a few pixels across inside; a sliver is not, and would only make a
    tiny loop in the skeleton.
    """
    out = ink.copy()
    for hole, _ in _holes(ink):
        if _hole_radius(hole) < _PINHOLE_RADIUS:
            out[hole] = 1
    return out


def set_traced_strokes(glyph: Any, strokes: List[Stroke]) -> None:
    """Store traced strokes on a glyph (none when nothing could be traced)."""
    glyph.strokes_json = json.dumps(strokes, separators=(",", ":")) if strokes else None
    glyph.stroke_source = "traced" if strokes else None
    glyph.trace_version = TRACE_VERSION


def trace_missing(glyphs: Iterable[Any], db: Any) -> int:
    """Trace every scanned glyph whose pen paths are missing or came from an
    older version of the tracer, from its mask image, and save them.

    Glyphs scanned before tracing existed (or traced before it improved) get
    their paths the first time a plot or Glyph Review needs them. Drawn
    glyphs are never touched. Returns how many glyphs were traced.
    """
    from app.storage import get_file_path  # kept local: the tracing itself needs only numpy and OpenCV

    traced = 0
    for glyph in glyphs:
        if glyph.stroke_source == "drawn" or getattr(glyph, "trace_version", None) == TRACE_VERSION:
            continue
        path = get_file_path(glyph.mask_path)
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED) if path.exists() else None
        if image is None:
            logger.warning("Glyph %s has no readable mask at %s; it can't be plotted", glyph.id, path)
            continue
        if image.ndim == 3 and image.shape[2] == 4:
            alpha = image[..., 3]
        else:
            # An opaque image: dark ink on light paper
            gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            alpha = 255 - gray
        set_traced_strokes(glyph, trace_mask(alpha))
        traced += 1
    if traced:
        db.commit()
    return traced


def ink_mask(alpha: np.ndarray) -> np.ndarray:
    """Solid ink, plus faint ink in any blob that also holds solid ink (uint8, 1 = ink).

    Faint ink bridges a stroke where the pen skipped, but never fills a
    counter: a hole the solid ink closes all the way round (the eye of an e
    washed with faint ink) stays a hole.
    """
    blurred = cv2.GaussianBlur(alpha, (0, 0), _BLUR_SIGMA)
    strong = blurred >= INK_THRESHOLD
    weak = (blurred >= FAINT_THRESHOLD).astype(np.uint8)
    n, labels = cv2.connectedComponents(weak, connectivity=8)
    keep = np.zeros(n, bool)
    keep[np.unique(labels[strong])] = True
    keep[0] = False
    ink = keep[labels].astype(np.uint8)
    solid = strong.astype(np.uint8)
    for hole, _ in _holes(solid):
        if _hole_radius(hole) >= _PINHOLE_RADIUS:
            ink[hole] = 0
    return ink


def _holes(ink: np.ndarray):
    """Each enclosed hole in *ink*, as (full-size boolean mask, bounding box)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats((ink == 0).astype(np.uint8), connectivity=4)
    h, w = ink.shape
    for i in range(1, n):
        x, y, bw, bh, _ = (int(v) for v in stats[i])
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue  # background around the letter, not a hole
        yield labels == i, (x, y, bw, bh)


def _hole_radius(hole: np.ndarray) -> float:
    """How far the middle of a hole is from its edge."""
    return float(cv2.distanceTransform(np.pad(hole.astype(np.uint8), 1), cv2.DIST_L2, 5).max())


# ---------------------------------------------------------------------------
# Thinning
# ---------------------------------------------------------------------------


def _neighbour_codes(img: np.ndarray) -> np.ndarray:
    """8-bit code of each pixel's neighbours, bit i set when ``_RING[i]`` is ink."""
    h, w = img.shape
    padded = np.pad(img, 1)
    code = np.zeros((h, w), np.int32)
    for bit, (dr, dc) in enumerate(_RING):
        code |= padded[1 + dr:1 + dr + h, 1 + dc:1 + dc + w].astype(np.int32) << bit
    return code


def _zhang_suen_tables() -> Tuple[np.ndarray, np.ndarray]:
    first = np.zeros(256, bool)
    second = np.zeros(256, bool)
    for code in range(256):
        p = [(code >> i) & 1 for i in range(8)]  # P2..P9
        count = sum(p)
        transitions = sum(1 for i in range(8) if p[i] == 0 and p[(i + 1) % 8] == 1)
        if not (2 <= count <= 6 and transitions == 1):
            continue
        p2, p3, p4, p5, p6, p7, p8, p9 = p
        first[code] = p2 * p4 * p6 == 0 and p4 * p6 * p8 == 0
        second[code] = p2 * p4 * p8 == 0 and p2 * p6 * p8 == 0
    return first, second


_FIRST, _SECOND = _zhang_suen_tables()


def _thin(ink: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning to a one-pixel-wide, 8-connected skeleton."""
    img = ink.copy()
    while True:
        changed = False
        for table in (_FIRST, _SECOND):
            remove = (img > 0) & table[_neighbour_codes(img)]
            if remove.any():
                img[remove] = 0
                changed = True
        if not changed:
            return img


# ---------------------------------------------------------------------------
# Skeleton graph
# ---------------------------------------------------------------------------


class _Graph:
    """Nodes (ends and junctions) and edges (pixel runs between them)."""

    def __init__(self) -> None:
        self.node_pos: List[Point] = []
        self.node_half_width: List[float] = []
        self.edges: Dict[int, Tuple[int, int, List[Point]]] = {}  # id -> (node a, node b, points a..b)
        self._next = 0

    def add_node(self, pos: Point, half_width: float) -> int:
        self.node_pos.append(pos)
        self.node_half_width.append(half_width)
        return len(self.node_pos) - 1

    def add_edge(self, a: int, b: int, pts: List[Point]) -> int:
        self.edges[self._next] = (a, b, pts)
        self._next += 1
        return self._next - 1

    def incident(self, node: int) -> List[int]:
        return [e for e, (a, b, _) in self.edges.items() if a == node or b == node]

    def degree(self, node: int) -> int:
        return sum((a == node) + (b == node) for a, b, _ in self.edges.values())

    def leaving(self, edge: int, node: int) -> List[Point]:
        """The edge's points, starting at *node*."""
        a, _, pts = self.edges[edge]
        return pts if a == node else pts[::-1]


def _centre(p: Pixel) -> Point:
    return float(p[1]) + 0.5, float(p[0]) + 0.5


def _trace_component(
    skel: np.ndarray, dist: np.ndarray, half_width: float, inside: np.ndarray
) -> Tuple[List[Stroke], Set[Point]]:
    """Strokes through one blob's skeleton, and the corner points they pass through.

    *inside* is the ink grown by a pixel; a corner is only taken when its
    straight legs stay on it.
    """
    pixels: Set[Pixel] = set(zip(*(a.tolist() for a in np.nonzero(skel))))
    if not pixels:
        return [], set()

    def nbrs(p: Pixel) -> List[Pixel]:
        """Mixed adjacency: a diagonal neighbour counts only when no shared side neighbour links them."""
        out = []
        for dr, dc in _RING:
            q = (p[0] + dr, p[1] + dc)
            if q not in pixels:
                continue
            if dr and dc and ((p[0] + dr, p[1]) in pixels or (p[0], p[1] + dc) in pixels):
                continue
            out.append(q)
        return out

    degree = {p: len(nbrs(p)) for p in pixels}
    special = {p for p, d in degree.items() if d != 2}

    # Junction pixels that touch form one node
    graph = _Graph()
    node_of: Dict[Pixel, int] = {}
    for p in sorted(special):
        if p in node_of:
            continue
        cluster, stack = [], [p]
        node_of[p] = -1
        while stack:
            q = stack.pop()
            cluster.append(q)
            if degree[q] < 3:
                continue
            for r in nbrs(q):
                if r in special and r not in node_of and degree[r] >= 3:
                    node_of[r] = -1
                    stack.append(r)
        # The cluster pixel nearest its middle, so the node stays on the skeleton
        mean = (float(np.mean([q[0] for q in cluster])), float(np.mean([q[1] for q in cluster])))
        middle = min(cluster, key=lambda q: (q[0] - mean[0]) ** 2 + (q[1] - mean[1]) ** 2)
        node = graph.add_node(_centre(middle), float(max(dist[q] for q in cluster)))
        for q in cluster:
            node_of[q] = node

    # Walk the runs between nodes
    used: Set[Pixel] = set()
    seen_links: Set[Tuple[Pixel, Pixel]] = set()
    for start in sorted(node_of):
        for first in nbrs(start):
            if first in node_of:
                a, b = node_of[start], node_of[first]
                if a != b and (first, start) not in seen_links:
                    seen_links.add((start, first))
                    graph.add_edge(a, b, [graph.node_pos[a], graph.node_pos[b]])
                continue
            if first in used:
                continue
            # Every pixel that isn't a node has exactly two neighbours, so the run is unambiguous
            run, prev, cur = [first], start, first
            used.add(first)
            end: Optional[Pixel] = None
            while True:
                nxt = [q for q in nbrs(cur) if q != prev]
                if not nxt:
                    break
                q = nxt[0]
                if q in node_of:
                    end = q
                    break
                if q in used:
                    break
                prev, cur = cur, q
                used.add(cur)
                run.append(cur)
            if end is None:
                continue
            a, b = node_of[start], node_of[end]
            if a == b and len(run) <= 2:
                continue  # a bump on a junction, not a loop
            graph.add_edge(a, b, [graph.node_pos[a]] + [_centre(q) for q in run] + [graph.node_pos[b]])

    # Closed loops with no node at all (an o written as one ring)
    loops: List[Stroke] = []
    rest = pixels - used - set(node_of)
    while rest:
        start = min(rest)
        loop, cur = [start], start
        rest.discard(start)
        while True:
            nxt = [q for q in nbrs(cur) if q in rest]
            if not nxt:
                break
            cur = nxt[0]
            rest.discard(cur)
            loop.append(cur)
        pts = [_centre(q) for q in loop]
        loops.append(pts + [pts[0]])

    _merge_crossings(graph)
    corners = _prune_spurs(graph, half_width, inside)
    corner_points = _merge_pass_through(graph, corners)
    return _chain(graph) + loops, corner_points


def _merge_crossings(graph: _Graph) -> None:
    """Make two junctions joined by a short bridge into one, so a crossing reads as two straight strokes."""
    changed = True
    while changed:
        changed = False
        for e, (a, b, pts) in list(graph.edges.items()):
            if a == b or graph.degree(a) < 3 or graph.degree(b) < 3:
                continue
            if path_length(pts) >= _BRIDGE_FACTOR * max(graph.node_half_width[a], graph.node_half_width[b]):
                continue
            if sum(1 for x, y, _ in graph.edges.values() if {x, y} == {a, b}) > 1:
                continue  # a small loop between them, not a bridge
            mid = pts[len(pts) // 2]
            del graph.edges[e]
            graph.node_pos[a] = mid
            graph.node_half_width[a] = max(graph.node_half_width[a], graph.node_half_width[b])
            for f, (x, y, fpts) in list(graph.edges.items()):
                if a not in (x, y) and b not in (x, y):
                    continue
                x2, y2 = (a if x == b else x), (a if y == b else y)
                fpts = list(fpts)
                if x2 == a:
                    fpts[0] = mid
                if y2 == a:
                    fpts[-1] = mid
                graph.edges[f] = (x2, y2, fpts)
            changed = True
            break


def _prune_spurs(graph: _Graph, half_width: float, inside: np.ndarray) -> Dict[int, Point]:
    """Drop short branches from a junction to a free end. Returns corner tips by junction.

    First, a junction of three where a short branch points straight out of
    the sharp angle between the other two is a corner: the branch's tip is
    the point of the turn (a v, the tops of an M), and the stroke will be
    routed through it.

    Otherwise a branch is a spur when its ink reaches less than
    ``_SPUR_PROTRUSION`` half-widths past the ink around its junction (the
    skeleton stops a half-width short of a stroke's end, so that is added
    back). If the junction would be left with one branch, the longest spur
    stays: it is where the stroke goes on.
    """
    corners: Dict[int, Point] = {}

    def free_branches(node: int, incident: List[int]) -> List[int]:
        out = []
        for e in incident:
            a, b, _ = graph.edges[e]
            other = b if a == node else a
            if other != node and graph.degree(other) == 1:
                out.append(e)
        return sorted(out, key=lambda e: path_length(graph.edges[e][2]))

    for _ in range(3):
        changed = False
        for node in range(len(graph.node_pos)):
            incident = graph.incident(node)
            if len(incident) < 3:
                continue
            free = free_branches(node, incident)
            if len(incident) == 3 and free and node not in corners:
                stub = free[0]
                if path_length(graph.edges[stub][2]) < _CORNER_MAX_STUB * half_width:
                    tip = _corner_tip(graph, node, [e for e in incident if e != stub], stub, inside)
                    if tip is not None:
                        corners[node] = tip
                        del graph.edges[stub]
                        changed = True
                        continue

            spurs = []
            for e in free:
                a, b, pts = graph.edges[e]
                other = b if a == node else a
                protrusion = path_length(pts) + graph.node_half_width[other] - graph.node_half_width[node]
                if protrusion < _SPUR_PROTRUSION * half_width:
                    spurs.append(e)
            if len(incident) - len(spurs) <= 1:
                spurs = spurs[:-1]  # the longest one is where the stroke goes on
            for e in spurs:
                del graph.edges[e]
                changed = True
        if not changed:
            break
    return corners


def _corner_route(graph: _Graph, node: int, arm: int, tip: Point) -> List[Point]:
    """An arm's points leaving *node*, minus those closer to it than the corner tip is."""
    here = graph.node_pos[node]
    radius = math.dist(tip, here)
    pts = graph.leaving(arm, node)
    while len(pts) > 2 and math.dist(pts[0], here) < radius:
        pts = pts[1:]
    return pts


def _on_ink(a: Point, b: Point, inside: np.ndarray) -> bool:
    """Whether the straight line from *a* to *b* stays on the ink."""
    steps = max(1, int(math.dist(a, b) * 2))
    h, w = inside.shape
    for i in range(steps + 1):
        x = a[0] + (b[0] - a[0]) * i / steps
        y = a[1] + (b[1] - a[1]) * i / steps
        r, c = int(y), int(x)
        if not (0 <= r < h and 0 <= c < w) or not inside[r, c]:
            return False
    return True


def _off_line(points: Sequence[Point], a: Point, b: Point) -> float:
    """The furthest any of *points* lies from the segment *a*-*b*."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy or 1e-9
    worst = 0.0
    for px, py in points:
        t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / length2))
        worst = max(worst, math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy)))
    return worst


def _corner_tip(graph: _Graph, node: int, arms: List[int], spur: int, inside: np.ndarray) -> Optional[Point]:
    """The spur's tip when the two arms meet sharply, the spur points out of their angle,
    and the straight line from each arm to the tip stays on the ink and close to
    the part of the arm it replaces (otherwise the spur is a stroke of its own,
    like the middle stem of an m)."""
    hw = graph.node_half_width[node]
    a = _direction(graph.leaving(arms[0], node), hw)
    b = _direction(graph.leaving(arms[1], node), hw)
    if math.acos(max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))) > _CORNER_MAX_ANGLE:
        return None
    out = (-(a[0] + b[0]), -(a[1] + b[1]))
    norm = math.hypot(*out)
    if norm < 1e-6:
        return None
    s_pts = graph.leaving(spur, node)
    s = _direction(s_pts, hw)
    if (s[0] * out[0] + s[1] * out[1]) / norm < _CORNER_ALIGN:
        return None
    tip = s_pts[-1]
    for arm in arms:
        route = _corner_route(graph, node, arm, tip)
        full = graph.leaving(arm, node)
        dropped = full[1:len(full) - len(route) + 1]
        if not _on_ink(route[0], tip, inside):
            return None
        if dropped and _off_line(dropped, route[0], tip) > _CORNER_MAX_DEVIATION * hw:
            return None
    return tip


def _merge_pass_through(graph: _Graph, corners: Dict[int, Point]) -> Set[Point]:
    """Join the two edges of a node left with exactly two, so a stroke doesn't stop there.

    At a corner the joined stroke runs out to the corner tip and back,
    instead of cutting across the inside of the turn. Returns the corner points.
    """
    corner_points: Set[Point] = set()
    changed = True
    while changed:
        changed = False
        for node in range(len(graph.node_pos)):
            incident = graph.incident(node)
            if len(incident) != 2:
                continue
            e1, e2 = incident
            a1, b1, _ = graph.edges[e1]
            a2, b2, _ = graph.edges[e2]
            if a1 == b1 or a2 == b2:
                continue
            p1 = graph.leaving(e1, node)[::-1]   # ends at the node
            p2 = graph.leaving(e2, node)         # starts at the node
            start = a1 if b1 == node else b1
            end = b2 if a2 == node else a2
            if node in corners:
                tip = corners[node]
                joined = _corner_route(graph, node, e1, tip)[::-1] + [tip] + _corner_route(graph, node, e2, tip)
                corner_points.add(tip)
            else:
                joined = p1 + p2[1:]
            del graph.edges[e1], graph.edges[e2]
            graph.add_edge(start, end, joined)
            changed = True
            break
    return corner_points


def _direction(pts: List[Point], half_width: float) -> Tuple[float, float]:
    """Unit direction of a path leaving its first point, measured a little way along it."""
    reach = max(3.0, 2.5 * half_width)
    x0, y0 = pts[0]
    travelled, target = 0.0, pts[-1]
    for p, q in zip(pts, pts[1:]):
        travelled += math.dist(p, q)
        if travelled >= reach:
            target = q
            break
    dx, dy = target[0] - x0, target[1] - y0
    norm = math.hypot(dx, dy) or 1.0
    return dx / norm, dy / norm


def _chain(graph: _Graph) -> List[Stroke]:
    """Join edges into strokes, passing straight through junctions where branches line up."""
    # Each edge end: (edge id, 0 for its start or 1 for its end)
    pairs: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for node in range(len(graph.node_pos)):
        ends = []
        for e, (a, b, pts) in graph.edges.items():
            if a == node:
                ends.append(((e, 0), pts))
            if b == node:
                ends.append(((e, 1), pts[::-1]))
        if len(ends) < 3:
            continue
        hw = graph.node_half_width[node]
        dirs = {end: _direction(pts, hw) for end, pts in ends}
        candidates = []
        keys = list(dirs)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                if keys[i][0] == keys[j][0]:
                    continue
                (x1, y1), (x2, y2) = dirs[keys[i]], dirs[keys[j]]
                turn = math.acos(max(-1.0, min(1.0, -(x1 * x2 + y1 * y2))))
                if turn <= _PAIR_MAX_TURN:
                    candidates.append((turn, keys[i], keys[j]))
        for _, k1, k2 in sorted(candidates):
            if k1 not in pairs and k2 not in pairs:
                pairs[k1], pairs[k2] = k2, k1

    strokes: List[Stroke] = []
    used: Set[int] = set()

    def walk(edge: int, from_end: int) -> Stroke:
        path: Stroke = []
        while edge not in used:
            used.add(edge)
            pts = graph.edges[edge][2]
            pts = pts if from_end == 0 else pts[::-1]
            path.extend(pts if not path else pts[1:])
            far = (edge, 1 - from_end)
            if far not in pairs:
                break
            edge, from_end = pairs[far]
        return path

    # Start at free ends first; whatever is left is a closed chain
    for e in sorted(graph.edges):
        for end in (0, 1):
            if e not in used and (e, end) not in pairs:
                strokes.append(walk(e, end))
    for e in sorted(graph.edges):
        if e not in used:
            strokes.append(walk(e, 0))
    return [s for s in strokes if len(s) >= 2]


def _smooth(pts: Stroke, fixed: Set[Point]) -> Stroke:
    """Moving average that keeps the ends, any corner in *fixed*, and a closed path closed."""
    if len(pts) < 5:
        return pts
    cuts = [i for i, p in enumerate(pts) if p in fixed and 0 < i < len(pts) - 1]
    if cuts:
        out: Stroke = []
        bounds = [0] + cuts + [len(pts) - 1]
        for a, b in zip(bounds, bounds[1:]):
            piece = _smooth_open(pts[a:b + 1])
            out.extend(piece if not out else piece[1:])
        return out
    if pts[0] == pts[-1]:
        arr = np.array(pts[:-1], dtype=np.float64)
        k = _SMOOTH_WINDOW
        padded = np.concatenate([arr[-k:], arr, arr[:k]])
        kernel = np.ones(2 * k + 1) / (2 * k + 1)
        out_arr = np.stack([np.convolve(padded[:, i], kernel, mode="valid") for i in range(2)], axis=1)
        result = [tuple(p) for p in out_arr.tolist()]
        return result + [result[0]]
    return _smooth_open(pts)


def _smooth_open(pts: Sequence[Point]) -> Stroke:
    if len(pts) < 5:
        return list(pts)
    arr = np.array(pts, dtype=np.float64)
    k = _SMOOTH_WINDOW
    padded = np.concatenate([np.repeat(arr[:1], k, 0), arr, np.repeat(arr[-1:], k, 0)])
    kernel = np.ones(2 * k + 1) / (2 * k + 1)
    out = np.stack([np.convolve(padded[:, i], kernel, mode="valid") for i in range(2)], axis=1)
    out[0], out[-1] = arr[0], arr[-1]
    return [tuple(p) for p in out.tolist()]
