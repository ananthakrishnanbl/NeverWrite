"""Tracing pen paths through scanned ink (workers/tracing.py).

The round-trip tests draw the stroke alphabet into masks exactly as drawn
letters are stored, trace the masks, and compare the traced paths with the
strokes that made them. Other tests build masks with the troubles real
scans have: faint joins, pinholes, ragged edges, blobby dots.

Run from backend/: ``python -m pytest tests``
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import cv2
import numpy as np
import pytest

from stroke_letters import CHARS, letter
from workers.geometry import path_length
from workers.strokes import build_glyph, clean_strokes, rasterise
from workers.tracing import trace_mask

Point = Tuple[float, float]


def sample(paths: Sequence[Sequence[Point]], step: float = 0.5) -> np.ndarray:
    pts: List[Point] = []
    for p in paths:
        if len(p) == 1:
            pts.append(p[0])
            continue
        for (x0, y0), (x1, y1) in zip(p, p[1:]):
            n = max(1, int(math.dist((x0, y0), (x1, y1)) / step))
            pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
        pts.append(p[-1])
    return np.array(pts)


def distances(a_paths, b_paths) -> np.ndarray:
    """For each point along *a_paths*, the distance to the nearest point of *b_paths*."""
    a, b = sample(a_paths), sample(b_paths)
    return np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1)


def on_ink(paths, alpha: np.ndarray) -> bool:
    inside = cv2.dilate((alpha >= 0.15).astype(np.uint8), np.ones((3, 3), np.uint8))
    h, w = inside.shape
    return all(0 <= int(y) < h and 0 <= int(x) < w and inside[int(y), int(x)] for x, y in sample(paths, 0.25))


def round_trip(ch: str, variant: int = 0):
    glyph = build_glyph(clean_strokes(letter(ch, variant)))
    return glyph, trace_mask(glyph.alpha)


# ---------------------------------------------------------------------------
# Round trip through the stroke alphabet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ch", list(CHARS))
def test_traced_paths_follow_the_strokes(ch: str) -> None:
    for variant in range(3):
        glyph, traced = round_trip(ch, variant)
        assert traced, ch
        assert on_ink(traced, glyph.alpha), f"{ch}: a traced path leaves the ink"
        # Everywhere along the traced paths is close to a real stroke...
        assert distances(traced, glyph.strokes).mean() < 1.0
        # ...and every real stroke is traced, give or take the ends and corners the
        # pen's round tip fills in (about one stroke width at 12 px/mm)
        missed = distances(glyph.strokes, traced)
        assert missed.mean() < 1.0, ch
        assert missed.max() < 8.0, ch


@pytest.mark.parametrize("ch,count", [("o", 1), ("l", 1), ("v", 1), ("w", 1), ("M", 1), ("x", 2), ("t", 2),
                                      ("i", 2), ("j", 2), ("T", 2), ("H", 3), ("n", 2), ("m", 3)])
def test_stroke_counts(ch: str, count: int) -> None:
    _, traced = round_trip(ch)
    assert len(traced) == count, [len(s) for s in traced]


def test_an_o_is_one_closed_loop() -> None:
    _, (loop,) = round_trip("o")
    assert loop[0] == loop[-1] and len(loop) > 8


def test_the_point_of_a_v_stays_sharp() -> None:
    glyph, (v,) = round_trip("v")
    apex_true = max(glyph.strokes[0], key=lambda p: p[1])
    apex = max(v, key=lambda p: p[1])
    assert math.dist(apex, apex_true) < 4.0


def test_the_dot_of_an_i_is_one_touch() -> None:
    _, traced = round_trip("i")
    assert sorted(len(s) for s in traced)[0] == 1


# ---------------------------------------------------------------------------
# What real scans do
# ---------------------------------------------------------------------------


def blank(h: int = 60, w: int = 80) -> np.ndarray:
    return np.zeros((h, w), np.float32)


def line(alpha: np.ndarray, a: Point, b: Point, width: float, value: float = 1.0) -> None:
    stroke = rasterise([[a, b]], alpha.shape[1], alpha.shape[0], width)
    np.maximum(alpha, stroke * value, out=alpha)


def test_empty_and_specks() -> None:
    assert trace_mask(blank()) == []
    speck = blank()
    speck[30, 40] = 1.0
    assert trace_mask(speck) == []


def test_a_faint_join_stays_one_stroke() -> None:
    alpha = blank()
    line(alpha, (10, 30), (35, 30), 6)
    line(alpha, (35, 30), (45, 30), 4, value=0.25)  # the pen skipped
    line(alpha, (45, 30), (70, 30), 6)
    (stroke,) = trace_mask(alpha)
    assert path_length(stroke) > 50


def test_a_counter_washed_with_faint_ink_stays_open() -> None:
    # An e whose eye the scan left light grey rather than paper white
    alpha = blank(70, 70)
    ring = [(35 + 14 * math.cos(t / 40 * 2 * math.pi), 35 + 14 * math.sin(t / 40 * 2 * math.pi)) for t in range(41)]
    np.maximum(alpha, rasterise([ring], 70, 70, 7), out=alpha)
    cv2.circle(alpha, (35, 35), 9, 0.3, -1)
    (loop,) = trace_mask(alpha)
    assert loop[0] == loop[-1]
    assert all(abs(math.dist(p, (35, 35)) - 14) < 2.0 for p in loop)


def test_pinholes_are_filled_but_counters_kept() -> None:
    bar = blank()
    line(bar, (10, 30), (70, 30), 9)
    bar[29:31, 30] = 0.0  # a pinhole in the ink
    bar[30, 50] = 0.0
    assert len(trace_mask(bar)) == 1

    eight = blank(80, 60)
    for cy, r in ((22, 12), (55, 15)):
        ring = [(30 + r * math.cos(t / 40 * 2 * math.pi), cy + r * math.sin(t / 40 * 2 * math.pi)) for t in range(41)]
        np.maximum(eight, rasterise([ring], 60, 80, 7), out=eight)
    traced = trace_mask(eight)
    total = sum(path_length(s) for s in traced)
    expected = 2 * math.pi * (12 + 15)
    assert abs(total - expected) / expected < 0.15


def test_ragged_edges_dont_sprout_branches() -> None:
    rng = np.random.default_rng(3)
    alpha = blank()
    line(alpha, (8, 30), (72, 30), 10)
    edge = (alpha > 0.3) & (cv2.dilate((alpha < 0.3).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    alpha[edge & (rng.random(alpha.shape) < 0.5)] = 0.0  # bite chunks out of the edge
    alpha[(cv2.dilate((alpha > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & (rng.random(alpha.shape) < 0.3)] = 1.0
    (stroke,) = trace_mask(alpha)
    ys = [y for _, y in stroke]
    assert max(ys) - min(ys) < 3.0


def test_a_crossing_is_two_straight_strokes() -> None:
    alpha = blank(70, 70)
    line(alpha, (10, 10), (60, 60), 8)
    line(alpha, (60, 10), (10, 60), 8)
    traced = trace_mask(alpha)
    assert len(traced) == 2
    for stroke in traced:
        (x0, y0), (x1, y1) = stroke[0], stroke[-1]
        assert abs(abs(x1 - x0) - abs(y1 - y0)) < 6 and math.dist(stroke[0], stroke[-1]) > 55


def test_dots() -> None:
    small = blank()
    cv2.circle(small, (40, 30), 3, 1.0, -1)
    (dot,) = trace_mask(small)
    assert len(dot) == 1 and math.dist(dot[0], (40.5, 30.5)) < 0.8
    wide = blank()
    cv2.circle(wide, (40, 30), 8, 1.0, -1)
    (spiral,) = trace_mask(wide)
    assert len(spiral) > 5 and all(math.dist(p, (40.5, 30.5)) < 6 for p in spiral)


def test_uint8_input() -> None:
    alpha = blank()
    line(alpha, (10, 30), (70, 30), 6)
    assert trace_mask((alpha * 255).astype(np.uint8)) == trace_mask(alpha)
