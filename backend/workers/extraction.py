"""Glyph extraction pipeline.

This module is executed in a background daemon thread for every uploaded scan.
It progresses through a series of computer-vision stages and writes Glyph
records to the database as characters are located on the sheet.

Stage overview
--------------
1.  Load image with OpenCV.
2.  Detect ArUco markers and rectify perspective (stubbed — falls back to the
    raw image when markers are not found).
3.  Clean / binarise the image (adaptive threshold; cyan-guide suppression is a
    TODO item).
4.  Divide the image into a character grid (equal-width cells for now; a
    template-aware layout is a TODO item).
5.  For each cell: locate ink pixels, measure bounding box and metrics, save
    mask PNG, create Glyph record.
6.  Compute aggregate profile metrics (median x_height, stroke_w, slant) and
    persist them on the Profile.
"""
from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app import jobs as job_store
from app.config import DATA_DIR
from app.models.glyph import Glyph
from app.models.profile import Profile
from app.storage import get_file_path, save_glyph_mask

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

# Default grid layout used when no template metadata is available.
_DEFAULT_COLS = 10
_DEFAULT_ROWS = 8

# Character set expected on the standard template (fallback).
_DEFAULT_CHARSET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "0123456789.,!?;:'\"-()"
)

# Sheet-specific layout configurations.
# Characters naturally flow across multiple rows (row-major order):
# E.g. Sheet 1 Lowercase: Row 1 = a..m (13 chars), Row 2 = n..z (13 chars)
# This ensures letters have plenty of comfortable space on the paper.
SHEET_CONFIGS: Dict[int, Dict[str, Any]] = {
    1: {
        "name": "Lowercase",
        "rows": 2,
        "cols": 13,
        "charset": "abcdefghijklmnopqrstuvwxyz",
    },
    2: {
        "name": "Uppercase",
        "rows": 2,
        "cols": 13,
        "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    },
    3: {
        "name": "Digits & Punctuation",
        "rows": 3,
        "cols": 10,
        "charset": "0123456789.,!?;:'\"-()",
    },
}


def run_extraction(
    job_id: str,
    scan_path: str,
    profile_id: int,
    sheet_number: int,
    db_session_factory: Callable,
) -> None:
    """Run the full glyph extraction pipeline for a single scanned sheet.

    This function is designed to be executed in a daemon thread via
    :func:`app.jobs.run_in_thread`.  Progress is reported through the
    in-memory job store so the client can poll ``GET /api/jobs/{job_id}``.

    Parameters
    ----------
    job_id:
        In-memory job ID to update throughout processing.
    scan_path:
        Relative path (from DATA_DIR) to the uploaded scan image.
    profile_id:
        Primary key of the owning Profile.
    sheet_number:
        1-based sheet index (reserved for multi-sheet support).
    db_session_factory:
        Callable that returns a new SQLAlchemy ``Session`` (i.e. ``SessionLocal``).
    """
    db = db_session_factory()
    try:
        _run(job_id, scan_path, profile_id, sheet_number, db)
    except Exception as exc:
        logger.exception("Extraction pipeline failed for job %s", job_id)
        job_store.update_job(job_id, status="error", error=str(exc))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal pipeline stages
# ---------------------------------------------------------------------------

def _run(
    job_id: str,
    scan_path: str,
    profile_id: int,
    sheet_number: int,
    db: Any,
) -> None:
    """Internal pipeline — called by :func:`run_extraction` inside a try/except."""

    # ------------------------------------------------------------------
    # Stage 1 — Load image
    # ------------------------------------------------------------------
    job_store.update_job(job_id, status="running", progress=5, stage="Loading image")
    abs_path = get_file_path(scan_path)
    image = cv2.imread(str(abs_path))
    if image is None:
        raise ValueError(f"OpenCV could not read image at {abs_path}. "
                         "Ensure the file is a valid PNG/JPEG/TIFF.")

    h, w = image.shape[:2]
    logger.info("Loaded scan %s — %dx%d px", abs_path.name, w, h)

    # ------------------------------------------------------------------
    # Stage 2 — Detect ArUco markers & rectify perspective
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=15, stage="Detecting markers")
    rectified = _detect_and_rectify(image)

    # ------------------------------------------------------------------
    # Stage 3 — Clean / binarise
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=30, stage="Cleaning")
    binary = _binarise(rectified)

    # ------------------------------------------------------------------
    # Stage 4 & 5 — Extract characters & measure glyphs
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=45, stage="Extracting cells")
    cfg = SHEET_CONFIGS.get(sheet_number, {
        "rows": _DEFAULT_ROWS,
        "cols": _DEFAULT_COLS,
        "charset": _DEFAULT_CHARSET,
    })
    rows = cfg["rows"]
    cols = cfg["cols"]
    charset = cfg["charset"]

    extracted_glyphs = _extract_character_crops(
        binary=binary,
        rows=rows,
        cols=cols,
        charset=charset,
    )

    job_store.update_job(job_id, progress=70, stage="Measuring glyphs")
    metrics_list = _save_extracted_glyphs(
        extracted_glyphs=extracted_glyphs,
        profile_id=profile_id,
        db=db,
    )

    # ------------------------------------------------------------------
    # Stage 6 — Build aggregate profile metrics
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=90, stage="Building profile metrics")
    _update_profile_metrics(profile_id=profile_id, metrics_list=metrics_list, db=db)

    job_store.update_job(job_id, status="done", progress=100, stage="Complete")
    logger.info("Extraction complete for job %s — %d glyphs saved", job_id, len(metrics_list))


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _detect_and_rectify(image: np.ndarray) -> np.ndarray:
    """Attempt ArUco marker detection and perspective warp.

    If markers are not found the original image is returned unchanged.

    Parameters
    ----------
    image:
        Full-colour BGR image loaded by OpenCV.

    Returns
    -------
    numpy.ndarray
        Rectified (or original) BGR image.

    Notes
    -----
    TODO: Implement full ArUco detection and homography warp.

    - Use ``cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)``
    - Detect corners with ``cv2.aruco.detectMarkers``
    - Compute homography from the four corner marker centres
    - Warp with ``cv2.warpPerspective`` to a canonical output size
    """
    # TODO: detect four corner ArUco markers
    # TODO: compute homography matrix H
    # TODO: warp image to canonical paper dimensions
    # Fallback: return image as-is
    logger.debug("ArUco detection not yet implemented — using raw image.")
    return image


def _binarise(image: np.ndarray) -> np.ndarray:
    """Convert image to a clean binary ink mask (255 = ink, 0 = background).

    Uses morphological illumination background subtraction to flatten lighting
    gradients and eliminate paper shadows from smartphone photos. Also
    suppresses horizontal ruled notebook lines.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 1. Morphological background subtraction to remove shadows and illumination gradients
    bg_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 40))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, bg_kernel)
    diff = cv2.absdiff(bg, gray)
    _, binary = cv2.threshold(diff, 26, 255, cv2.THRESH_BINARY)

    # 2. Suppress horizontal ruled notebook lines
    line_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 1))
    horiz_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, line_kernel)
    binary = cv2.bitwise_and(binary, cv2.bitwise_not(horiz_lines))

    # 3. Light morphological opening to remove isolated noise specks
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return binary


def _extract_character_crops(
    binary: np.ndarray,
    rows: int,
    cols: int,
    charset: str,
) -> List[Tuple[str, np.ndarray]]:
    """Extract individual character crops row by row.

    Divides the sheet into horizontal row bands, extracts ink components
    in left-to-right order, and merges multi-stroke characters (e.g. dots on
    'i', 'j', arms on 'k', crossed strokes on 't', 'x').

    Parameters
    ----------
    binary:
        Clean binary uint8 image (255 = ink).
    rows:
        Expected number of text lines on the sheet (e.g. 2 for lowercase/uppercase).
    cols:
        Expected number of characters per line (e.g. 13 for a–m and n–z).
    charset:
        Ordered string of expected characters in row-major order.

    Returns
    -------
    list of (char, crop_image)
    """
    h, w = binary.shape[:2]
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary)
    # Filter for valid character stroke components
    comps = [
        s for s in stats[1:]
        if s[cv2.CC_STAT_AREA] >= 25
        and s[cv2.CC_STAT_HEIGHT] >= 10
        and s[cv2.CC_STAT_WIDTH] >= 5
        and s[cv2.CC_STAT_WIDTH] < w * 0.7
    ]

    results: List[Tuple[str, np.ndarray]] = []
    row_h = h // rows

    for r in range(rows):
        y_min = r * row_h
        y_max = (r + 1) * row_h

        # Get components belonging to this horizontal row band
        row_comps = [c for c in comps if y_min <= (c[cv2.CC_STAT_TOP] + c[cv2.CC_STAT_HEIGHT] // 2) < y_max]
        row_comps.sort(key=lambda c: c[cv2.CC_STAT_LEFT])

        # Merge closely adjacent or overlapping components (multi-stroke characters)
        merged: List[List[int]] = []
        merge_dist = max(18, int(w / (cols * 5)))

        for c in row_comps:
            x, y, cw, ch = c[cv2.CC_STAT_LEFT], c[cv2.CC_STAT_TOP], c[cv2.CC_STAT_WIDTH], c[cv2.CC_STAT_HEIGHT]
            if not merged:
                merged.append([x, y, x + cw, y + ch])
            else:
                prev = merged[-1]
                if x <= prev[2] + merge_dist:
                    merged[-1] = [min(prev[0], x), min(prev[1], y), max(prev[2], x + cw), max(prev[3], y + ch)]
                else:
                    merged.append([x, y, x + cw, y + ch])

        chars_for_row = charset[r * cols : (r + 1) * cols]

        for c_idx, box in enumerate(merged[:len(chars_for_row)]):
            ch = chars_for_row[c_idx]
            x1, y1, x2, y2 = box
            px1 = max(0, x1 - 2)
            py1 = max(0, y1 - 2)
            px2 = min(w, x2 + 2)
            py2 = min(h, y2 + 2)
            crop = binary[py1:py2, px1:px2].copy()
            if crop.shape[0] > 0 and crop.shape[1] > 0:
                results.append((ch, crop))

    # Fallback to naive grid if components were not detected
    if len(results) < len(charset) // 3:
        results = []
        cell_h = h // rows
        cell_w = w // cols
        idx = 0
        for r in range(rows):
            for c in range(cols):
                if idx >= len(charset):
                    break
                ch = charset[idx]
                idx += 1
                y0 = r * cell_h
                y1 = y0 + cell_h
                x0 = c * cell_w
                x1 = x0 + cell_w
                cell = binary[y0:y1, x0:x1]
                bbox = _ink_bounding_box(cell)
                if bbox is not None:
                    bx, by, bw, bh = bbox
                    tight = cell[by:by + bh, bx:bx + bw].copy()
                    results.append((ch, tight))

    return results

    # Fallback to naive grid if line detection found too few characters
    if len(results) < len(charset) // 3:
        results = []
        cell_h = h // rows
        cell_w = w // cols
        idx = 0
        for r in range(rows):
            for c in range(cols):
                if idx >= len(charset):
                    break
                ch = charset[idx]
                idx += 1
                y0 = r * cell_h
                y1 = y0 + cell_h
                x0 = c * cell_w
                x1 = x0 + cell_w
                cell = binary[y0:y1, x0:x1]
                bbox = _ink_bounding_box(cell)
                if bbox is not None:
                    bx, by, bw, bh = bbox
                    tight = cell[by:by + bh, bx:bx + bw].copy()
                    results.append((ch, tight))

    return results


def _ink_bounding_box(cell: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """Return the tight bounding box of ink pixels in a cell, or None if empty."""
    coords = cv2.findNonZero(cell)
    if coords is None:
        return None
    x, y, bw, bh = cv2.boundingRect(coords)
    return (x, y, bw, bh)


def _estimate_stroke_width(cell: np.ndarray) -> float:
    """Estimate average stroke width using distance transform."""
    if cv2.countNonZero(cell) == 0:
        return 0.0
    dist = cv2.distanceTransform(cell, cv2.DIST_L2, 5)
    kernel = np.ones((3, 3), dtype=np.uint8)
    dilated = cv2.dilate(dist, kernel)
    local_max = (dist == dilated) & (cell > 0)
    values = dist[local_max]
    if values.size == 0:
        return 0.0
    return float(np.median(values) * 2.0)


# Set of characters that drop below the typographic baseline
DESCENDERS = set("gjpqy,;")


def _save_extracted_glyphs(
    extracted_glyphs: List[Tuple[str, np.ndarray]],
    profile_id: int,
    db: Any,
) -> List[Dict[str, float]]:
    """Measure extracted glyph crops, save mask PNGs, and update DB records."""
    metrics_list: List[Dict[str, float]] = []

    for char, mask_crop in extracted_glyphs:
        bh, bw = mask_crop.shape[:2]
        if bh == 0 or bw == 0:
            continue

        # Convert binary mask into transparent 4-channel BGRA PNG
        # Ink pixels (255) are dark with full opacity (Alpha=255)
        # Background pixels (0) are completely transparent (Alpha=0)
        bgra = np.zeros((bh, bw, 4), dtype=np.uint8)
        bgra[mask_crop > 0] = [25, 25, 35, 255]
        bgra[mask_crop == 0] = [255, 255, 255, 0]

        success, png_buf = cv2.imencode(".png", bgra)
        if not success:
            logger.warning("Failed to encode PNG for char=%r", char)
            continue

        variant_index = 0
        relative_mask_path = save_glyph_mask(
            data=png_buf.tobytes(),
            profile_id=profile_id,
            char=char,
            variant=variant_index,
        )

        stroke_w = _estimate_stroke_width(mask_crop)

        # Baseline offset: descenders hang below the baseline
        if char in DESCENDERS:
            baseline_offset = float(bh * 0.35)
        else:
            baseline_offset = 0.0

        bearing_left = 3.0
        bearing_right = 3.0

        existing_glyph = (
            db.query(Glyph)
            .filter(Glyph.profile_id == profile_id, Glyph.char == char)
            .first()
        )

        if existing_glyph:
            existing_glyph.mask_path = relative_mask_path
            existing_glyph.width = float(bw)
            existing_glyph.height = float(bh)
            existing_glyph.baseline_offset = baseline_offset
            existing_glyph.bearing_left = bearing_left
            existing_glyph.bearing_right = bearing_right
            existing_glyph.accepted = True
        else:
            glyph = Glyph(
                profile_id=profile_id,
                char=char,
                variant_index=variant_index,
                mask_path=relative_mask_path,
                svg_path=None,
                width=float(bw),
                height=float(bh),
                baseline_offset=baseline_offset,
                bearing_left=bearing_left,
                bearing_right=bearing_right,
                accepted=True,
            )
            db.add(glyph)

        metrics_list.append({
            "x_height": float(bh),
            "stroke_w": stroke_w,
            "slant": 0.0,
        })

    db.commit()
    return metrics_list


def _update_profile_metrics(
    profile_id: int,
    metrics_list: List[Dict[str, float]],
    db: Any,
) -> None:
    """Compute median metrics from all extracted glyphs and persist to Profile.

    Parameters
    ----------
    profile_id:
        PK of the Profile to update.
    metrics_list:
        List of per-glyph metric dicts from :func:`_measure_and_save_glyphs`.
    db:
        Active SQLAlchemy session.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None or not metrics_list:
        return

    x_heights = [m["x_height"] for m in metrics_list]
    stroke_ws = [m["stroke_w"] for m in metrics_list]
    slants = [m["slant"] for m in metrics_list]

    # Load existing metrics so we merge rather than overwrite cross-scan data
    try:
        existing: Dict[str, Any] = json.loads(profile.metrics_json or "{}")
    except json.JSONDecodeError:
        existing = {}

    updated_metrics = {
        **existing,
        "x_height": float(np.median(x_heights)),
        "stroke_w": float(np.median(stroke_ws)),
        "slant": float(np.median(slants)),
        "avg_char_gap": existing.get("avg_char_gap", 0.0),  # TODO: measure inter-glyph gaps
        "avg_word_gap": existing.get("avg_word_gap", 0.0),  # TODO: measure inter-word gaps
    }

    profile.metrics_json = json.dumps(updated_metrics)
    db.commit()
    logger.info("Profile %d metrics updated: %s", profile_id, updated_metrics)
