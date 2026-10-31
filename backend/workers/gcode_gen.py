"""G-code generator for pen plotters / CNC machines.

Converts a list of PlacedGlyph objects (from ``layout_text``) into G-code that
traces each character's ink contours using OpenCV contour extraction.

Coordinate mapping
------------------
- Source: 96 DPI page pixels, y-down (top-left origin)
- Output: mm, y-up (bottom-left origin, standard G-code / CNC convention)

  ``x_mm = px * 25.4 / 96``
  ``y_mm = page_height_mm - (px * 25.4 / 96)``

Pen conventions
---------------
By default the generator uses spindle M-codes::

    M3 S1000   — pen down (start drawing)
    M5         — pen up   (stop drawing / travel)

When ``use_z_axis=True`` in *settings*, Z moves are used instead::

    G1 Z{pen_down_z} — lower pen
    G1 Z{pen_up_z}   — raise pen

Settings keys (all optional, merged over defaults)
---------------------------------------------------
feedrate     : float  — drawing speed in mm/min (default 1500)
travel_rate  : float  — travel speed in mm/min  (default 3000)
pen_down_z   : float  — Z position when pen is down  (default 0.0)
pen_up_z     : float  — Z position when pen is up    (default 5.0)
use_z_axis   : bool   — True → Z moves; False → M3/M5 (default False)
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List

import cv2
import numpy as np

from app.config import DATA_DIR, EXPORT_DIR
from app.storage import get_file_path
from workers.renderer import PlacedGlyph

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

_PX_PER_MM: float = 96 / 25.4
_PAGE_H_MM: float = 297.0  # A4

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "feedrate": 1500,     # mm/min — drawing moves
    "travel_rate": 3000,  # mm/min — pen-up rapid moves
    "pen_down_z": 0.0,    # mm
    "pen_up_z": 5.0,      # mm
    "use_z_axis": False,  # True → G1 Z; False → M3/M5
}

# Contour approximation: how far a simplified point may deviate from the true
# contour (in scaled pixel units).  Larger → fewer G-code points.
_APPROX_EPSILON_SCALE: float = 0.015  # fraction of contour arc length


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_gcode(
    render_id: int,
    pages: List[List[PlacedGlyph]],
    settings: Dict[str, Any],
) -> str:
    """Generate a G-code file for *pages* and return its relative path.

    Parameters
    ----------
    render_id:
        Primary key of the RenderJob — used to name the output file.
    pages:
        Nested list from ``layout_text``.  ``pages[0]`` is the first page,
        etc.  Each page is a list of :class:`~workers.renderer.PlacedGlyph`.
    settings:
        Merged render settings dict (same one passed to the renderer).

    Returns
    -------
    str
        Relative path from DATA_DIR, e.g. ``"exports/render_3.gcode"``.
    """
    cfg = {**_DEFAULT_SETTINGS, **{k: settings[k] for k in _DEFAULT_SETTINGS if k in settings}}

    feedrate: float = float(cfg["feedrate"])
    travel_rate: float = float(cfg["travel_rate"])
    pen_down_z: float = float(cfg["pen_down_z"])
    pen_up_z: float = float(cfg["pen_up_z"])
    use_z: bool = bool(cfg["use_z_axis"])

    lines: List[str] = []

    # ------------------------------------------------------------------
    # Programme header
    # ------------------------------------------------------------------
    lines += [
        "; NeverWrite — generated G-code",
        f"; render_id={render_id}",
        f"; pages={len(pages)}",
        "G21        ; units: mm",
        "G90        ; absolute positioning",
        "G28        ; home all axes",
        _pen_up(use_z, pen_up_z, travel_rate),
        "",
    ]

    page_number = 0
    for page in pages:
        page_number += 1
        lines.append(f"; ---- page {page_number} ----")

        for placed in page:
            contours = _extract_contours(placed)
            if not contours:
                continue

            for contour in contours:
                if len(contour) < 2:
                    continue

                # Travel to first point (pen up)
                first = contour[0]
                x0, y0 = _to_mm(first[0], first[1])
                lines.append(f"G0 X{x0:.3f} Y{y0:.3f} F{travel_rate:.0f}  ; travel")
                lines.append(_pen_down(use_z, pen_down_z, feedrate))

                # Draw the contour
                for pt in contour[1:]:
                    x, y = _to_mm(pt[0], pt[1])
                    lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feedrate:.0f}")

                # Close the contour back to first point
                lines.append(f"G1 X{x0:.3f} Y{y0:.3f} F{feedrate:.0f}")
                lines.append(_pen_up(use_z, pen_up_z, travel_rate))

        if page_number < len(pages):
            lines += [
                "; pause between pages — operator advances paper",
                "M0        ; unconditional stop",
                "",
            ]

    # ------------------------------------------------------------------
    # Programme footer
    # ------------------------------------------------------------------
    lines += [
        "",
        "G28        ; return home",
        _pen_up(use_z, pen_up_z, travel_rate),
        "M2         ; end of programme",
    ]

    gcode_text = "\n".join(lines) + "\n"

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"render_{render_id}.gcode"
    abs_path = EXPORT_DIR / filename
    abs_path.write_text(gcode_text, encoding="utf-8")

    rel_path = f"exports/{filename}"
    logger.info("G-code written: %s (%d lines)", abs_path, len(lines))
    return rel_path


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------


def _to_mm(x_px: float, y_px: float) -> tuple[float, float]:
    """Convert 96 DPI page pixels (y-down) → mm (y-up, CNC origin = bottom-left)."""
    x_mm = x_px / _PX_PER_MM
    # Flip Y: G-code Y=0 is at the bottom of the page
    y_mm = _PAGE_H_MM - (y_px / _PX_PER_MM)
    return x_mm, y_mm


# ---------------------------------------------------------------------------
# Pen up / down helpers
# ---------------------------------------------------------------------------


def _pen_up(use_z: bool, z: float, rate: float) -> str:
    if use_z:
        return f"G1 Z{z:.3f} F{rate:.0f}  ; pen up"
    return "M5         ; pen up"


def _pen_down(use_z: bool, z: float, rate: float) -> str:
    if use_z:
        return f"G1 Z{z:.3f} F{rate:.0f}  ; pen down"
    return "M3 S1000   ; pen down"


# ---------------------------------------------------------------------------
# Contour extraction
# ---------------------------------------------------------------------------


def _extract_contours(placed: PlacedGlyph) -> List[List[tuple[float, float]]]:
    """Return a list of contours for one placed glyph.

    Each contour is an ordered list of ``(x_page_px, y_page_px)`` tuples in
    the 96 DPI page coordinate space (y-down), already rotated, scaled, and
    offset to their final position on the page.

    Returns an empty list if the mask file is missing or has no ink.
    """
    glyph = placed.glyph
    mask_path = get_file_path(glyph.mask_path)

    if not mask_path.exists():
        logger.warning("G-code: mask missing: %s", mask_path)
        return []

    # Load image — must be BGRA (4 channels) with alpha = ink mask
    img = cv2.imread(str(mask_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        logger.warning("G-code: could not read mask: %s", mask_path)
        return []

    # Derive alpha channel
    if img.ndim == 2:
        # Grayscale: treat non-white as ink
        alpha = (img < 200).astype(np.uint8) * 255
    elif img.shape[2] == 4:
        # BGRA: use alpha channel directly
        alpha = img[:, :, 3]
    else:
        # BGR: derive from luminance
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        alpha = (gray < 200).astype(np.uint8) * 255

    # Threshold to binary
    _, binary = cv2.threshold(alpha, 127, 255, cv2.THRESH_BINARY)

    # Find contours in glyph-local pixel space
    raw_contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not raw_contours:
        return []

    result: List[List[tuple[float, float]]] = []

    scale = placed.scale
    rotation = placed.rotation  # radians, clockwise on page

    # Glyph image centre (for rotation pivot)
    img_cx = glyph.width / 2.0
    img_cy = glyph.height / 2.0

    # The rendered position: x is the left edge of the glyph box, y is the
    # baseline.  Centre of the glyph box on the page (mirrors renderToCanvas):
    draw_w = glyph.width * scale
    draw_h = glyph.height * scale
    # Horizontal centre
    page_cx = placed.x + draw_w / 2.0
    # Vertical centre — baseline_offset is how far above baseline the centre is
    page_cy = placed.y - draw_h / 2.0 + glyph.baseline_offset * scale

    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)

    for raw in raw_contours:
        # Approximate the contour to reduce point count
        arc_len = cv2.arcLength(raw, closed=True)
        epsilon = _APPROX_EPSILON_SCALE * arc_len
        approx = cv2.approxPolyDP(raw, epsilon, closed=True)

        pts: List[tuple[float, float]] = []
        for point in approx:
            lx, ly = float(point[0][0]), float(point[0][1])

            # 1. Scale from local px to page px
            sx = (lx - img_cx) * scale
            sy = (ly - img_cy) * scale

            # 2. Rotate (clockwise on page = standard rotation matrix with y-down)
            rx = sx * cos_r - sy * sin_r
            ry = sx * sin_r + sy * cos_r

            # 3. Translate to page position
            px = page_cx + rx
            py = page_cy + ry

            pts.append((px, py))

        if pts:
            result.append(pts)

    return result
