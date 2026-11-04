"""Letters drawn in the browser: clean up the strokes and turn them into glyphs.

The Draw page records each character as the path the pen tip took, in
millimetres of a writing box with the same size and guides as a box on the
printed sheet (``BOX_W_MM`` x ``BOX_H_MM``, baseline ``BASELINE_FROM_TOP_MM``
from the top, x-height ``X_HEIGHT_MM``). Here the strokes are scaled to the
extractor's ``PX_PER_MM``, so drawn and scanned glyphs come out the same size,
cropped to the ink, and drawn into a soft alpha mask.

The mask keeps Glyph Review, the preview and the PDF working unchanged. The
strokes, stored on the glyph in the mask's pixel coordinates, are what a pen
plotter follows (see ``workers/plotter.py``).
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from typing import Any, List, Tuple

import cv2
import numpy as np

from app.models.glyph import Glyph
from app.storage import get_file_path, save_glyph_mask
from workers.extraction import _TEMPLATE_BEARING_PX, _alpha_png, _update_profile_metrics
from workers.geometry import simplify
from workers.template import BASELINE_FROM_TOP_MM, BOX_H_MM, BOX_W_MM, PX_PER_MM, SHEET_CONFIGS, VARIANTS

Point = Tuple[float, float]
Stroke = List[Point]

# Line width of the stored mask, in box millimetres. Text at the default 10 pt
# shrinks the box's 4 mm x-height to about 3.5 mm, so this prints roughly
# 0.4 mm wide, like the fineliner a plotter would hold. Keep in step with
# STROKE_WIDTH_MM in frontend/src/lib/draw.ts.
STROKE_WIDTH_MM = 0.45

# Points closer than this to the line through their neighbours are dropped
# (Ramer-Douglas-Peucker). Small enough that curves keep their shape.
SIMPLIFY_MM = 0.03

# How far outside the box a point may land (a fast stroke that overshoots the
# edge) before it is pulled back onto the edge
_EDGE_SLACK_MM = 2.0

MAX_STROKES = 60
MAX_POINTS = 6000

_SUPERSAMPLE = 4
_PAD_PX = 2
_SUBPIXEL_BITS = 4

DRAWABLE_CHARS = "".join(cfg["charset"] for cfg in SHEET_CONFIGS.values())


class StrokeError(ValueError):
    """The drawing can't be used. The message is shown to the person."""


@dataclass
class DrawnGlyph:
    """A drawn character, ready to save."""

    strokes: List[Stroke]     # mask pixel coordinates, origin at the mask's top-left corner
    alpha: np.ndarray         # float32 in [0, 1]
    baseline_offset: float    # px the bottom edge of ``alpha`` sits below the baseline


def clean_strokes(raw: Any) -> List[Stroke]:
    """Validate strokes in box millimetres and drop redundant points.

    Each stroke is a list of ``[x, y]`` pairs. A stroke of one point is a dot.

    Raises
    ------
    StrokeError
        If there is nothing drawn, or the input isn't a list of point lists.
    """
    if not isinstance(raw, list) or not raw:
        raise StrokeError("Nothing was drawn. Write the character in the box first.")
    if len(raw) > MAX_STROKES:
        raise StrokeError(f"That's more than {MAX_STROKES} strokes for one character. Clear the box and try again.")

    strokes: List[Stroke] = []
    total = 0
    for stroke in raw:
        if not isinstance(stroke, list):
            raise StrokeError("Each stroke must be a list of points.")
        points: Stroke = []
        for point in stroke:
            if not (isinstance(point, (list, tuple)) and len(point) == 2):
                raise StrokeError("Each point must be an [x, y] pair.")
            x, y = (float(v) for v in point)
            if not (math.isfinite(x) and math.isfinite(y)):
                raise StrokeError("A point has an invalid position.")
            if not (-_EDGE_SLACK_MM <= x <= BOX_W_MM + _EDGE_SLACK_MM and -_EDGE_SLACK_MM <= y <= BOX_H_MM + _EDGE_SLACK_MM):
                raise StrokeError("A stroke runs far outside the writing box.")
            p = (min(max(x, 0.0), BOX_W_MM), min(max(y, 0.0), BOX_H_MM))
            if not points or p != points[-1]:
                points.append(p)
        if points:
            simplified = simplify(points, SIMPLIFY_MM)
            total += len(simplified)
            strokes.append(simplified)
    if not strokes:
        raise StrokeError("Nothing was drawn. Write the character in the box first.")
    if total > MAX_POINTS:
        raise StrokeError("That drawing has too many points. Clear the box and write the character again.")
    return strokes


def build_glyph(strokes_mm: List[Stroke]) -> DrawnGlyph:
    """Scale strokes to glyph pixels, crop to the ink and draw the alpha mask."""
    half = STROKE_WIDTH_MM * PX_PER_MM / 2
    px_strokes = [[(x * PX_PER_MM, y * PX_PER_MM) for x, y in s] for s in strokes_mm]
    xs = [x for s in px_strokes for x, _ in s]
    ys = [y for s in px_strokes for _, y in s]

    # Pixel i covers [i, i + 1); the crop keeps the whole line width plus a margin
    x0 = math.floor(min(xs) - half - _PAD_PX)
    y0 = math.floor(min(ys) - half - _PAD_PX)
    x1 = math.ceil(max(xs) + half + _PAD_PX)
    y1 = math.ceil(max(ys) + half + _PAD_PX)
    local = [[(round(x - x0, 2), round(y - y0, 2)) for x, y in s] for s in px_strokes]

    alpha = rasterise(local, x1 - x0, y1 - y0, 2 * half)
    baseline_px = BASELINE_FROM_TOP_MM * PX_PER_MM
    return DrawnGlyph(strokes=local, alpha=alpha, baseline_offset=float(y1 - baseline_px))


def rasterise(strokes: List[Stroke], width: int, height: int, line_width: float) -> np.ndarray:
    """Draw strokes as round-capped lines into a soft mask of *width* x *height* pixels."""
    s = _SUPERSAMPLE
    canvas = np.zeros((height * s, width * s), np.uint8)
    thickness = max(1, int(round(line_width * s)))
    scale = 1 << _SUBPIXEL_BITS
    for stroke in strokes:
        # Continuous coordinates to OpenCV's pixel-centre ones, in fixed point
        pts = np.array([[(x * s - 0.5) * scale, (y * s - 0.5) * scale] for x, y in stroke])
        pts = np.round(pts).astype(np.int32)
        if len(pts) == 1 or np.all(pts == pts[0]):
            cv2.circle(canvas, tuple(int(v) for v in pts[0]), int(round(thickness / 2 * scale)), 255,
                       -1, cv2.LINE_AA, _SUBPIXEL_BITS)
        else:
            cv2.polylines(canvas, [pts.reshape(-1, 1, 2)], False, 255, thickness, cv2.LINE_AA, _SUBPIXEL_BITS)
    small = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    return small.astype(np.float32) / 255.0


def save_drawn_glyph(db: Any, profile_id: int, char: str, variant: int, raw_strokes: Any) -> Glyph:
    """Clean, draw and store one variant of a character, replacing whatever that slot held.

    Raises
    ------
    StrokeError
        If the character, variant or strokes can't be used.
    """
    if len(char) != 1 or char not in DRAWABLE_CHARS:
        raise StrokeError(f"{char!r} isn't one of the characters NeverWrite captures.")
    if not 0 <= variant < VARIANTS:
        raise StrokeError(f"Variant must be 1 to {VARIANTS}.")

    drawn = build_glyph(clean_strokes(raw_strokes))
    png = _alpha_png(drawn.alpha)
    if png is None:
        raise StrokeError("Couldn't save that drawing. Please try again.")
    mask_path = save_glyph_mask(png, profile_id, char, variant, tag=f"draw{uuid.uuid4().hex[:8]}")

    glyph = (
        db.query(Glyph)
        .filter(Glyph.profile_id == profile_id, Glyph.char == char, Glyph.variant_index == variant)
        .first()
    )
    if glyph is None:
        glyph = Glyph(profile_id=profile_id, char=char, variant_index=variant, svg_path=None)
        db.add(glyph)
    elif glyph.mask_path and glyph.mask_path != mask_path:
        get_file_path(glyph.mask_path).unlink(missing_ok=True)

    height, width = drawn.alpha.shape
    glyph.mask_path = mask_path
    glyph.width = float(width)
    glyph.height = float(height)
    glyph.baseline_offset = drawn.baseline_offset
    glyph.bearing_left = _TEMPLATE_BEARING_PX
    glyph.bearing_right = _TEMPLATE_BEARING_PX
    glyph.accepted = True
    glyph.issue = None
    glyph.strokes_json = json.dumps(drawn.strokes, separators=(",", ":"))
    glyph.stroke_source = "drawn"
    glyph.trace_version = None
    db.commit()
    db.refresh(glyph)

    _update_profile_metrics(profile_id, [{"stroke_w": STROKE_WIDTH_MM * PX_PER_MM, "slant": 0.0}], db)
    return glyph
