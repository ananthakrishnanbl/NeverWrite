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
4.  Group ink components into text rows and characters, then assign the
    sheet's characters in reading order.
5.  For each character: measure its bounding box and baseline offset, save
    mask PNG, create Glyph record.
6.  Compute aggregate profile metrics (median x_height, stroke_w, slant) and
    persist them on the Profile.
"""
from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

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

# Character set expected on the standard template (fallback).
_DEFAULT_CHARSET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "0123456789.,!?;:'\"-()"
)

# Sheet-specific character sets, in reading order. The number of rows the
# person used doesn't matter: rows are found from the ink, and characters are
# assigned left to right, top to bottom.
SHEET_CONFIGS: Dict[int, Dict[str, Any]] = {
    1: {"name": "Lowercase", "charset": "abcdefghijklmnopqrstuvwxyz"},
    2: {"name": "Uppercase", "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    3: {"name": "Digits & Punctuation", "charset": "0123456789.,!?;:'\"-()"},
}

# Components smaller than this (or flatter than _MIN_COMPONENT_HEIGHT px) are
# specks or leftovers of the ruled notebook lines, not ink strokes.
_MIN_COMPONENT_AREA = 20
_MIN_COMPONENT_HEIGHT = 4

# Characters whose bottom sits on the baseline; used to locate each row's baseline.
_ON_BASELINE = set("abcdefhiklmnorstuvwxzABCDEFGHIKLMNOPRSTUVWXYZ0123456789!?:")

# Characters whose height is the x-height (no ascender or descender).
X_HEIGHT_CHARS = set("acemnorsuvwxz")


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
    charset = SHEET_CONFIGS.get(sheet_number, {"charset": _DEFAULT_CHARSET})["charset"]

    extracted_glyphs, shapes_found = _extract_character_crops(binary=binary, charset=charset)
    if not extracted_glyphs:
        raise ValueError("No handwriting found on this sheet. Check the photo is sharp and well lit.")
    if shapes_found != len(charset):
        # Characters are assigned in order, so a miscount shifts everything after it
        job_store.update_job(
            job_id,
            warning=(
                f"Found {shapes_found} characters but this sheet has {len(charset)}. "
                "Some glyphs are probably labelled wrong; check them on the review page "
                "or retake the photo with the characters in the template order."
            ),
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
    charset: str,
) -> Tuple[List[Tuple[str, np.ndarray, float]], int]:
    """Extract individual character crops in reading order.

    Rows are found from the ink itself instead of cutting the photo into equal
    bands, so a descender, an uneven margin or a different number of written
    rows doesn't push characters into the wrong row.

    1.  Collect ink components and drop specks and ruled-line leftovers.
    2.  Tall components define the rows: a component joins a row when it
        overlaps that row vertically by a decent fraction of its height, so a
        'g' tail that just touches the 't' below stays in its own row.
    3.  Small marks (dots, commas, colon halves) first join nearby small marks,
        then go to the row whose core band (median top to median bottom) is
        closest.
    4.  Inside each row, horizontally overlapping groups merge into one
        character ('i' and its dot, the two halves of ':' and '"').
    5.  ``charset`` is assigned in reading order, and each glyph's baseline
        offset is measured against its row's baseline.

    Parameters
    ----------
    binary:
        Clean binary uint8 image (255 = ink).
    charset:
        Ordered string of expected characters in reading order.

    Returns
    -------
    (list of (char, crop_image, baseline_offset), number_of_characters_found)
        ``baseline_offset`` is how far the crop's bottom edge sits below the
        row baseline in pixels (negative for marks above it, like apostrophes).
    """
    h, w = binary.shape[:2]
    _, labels, stats, _ = cv2.connectedComponentsWithStats(binary)

    # Each box is [x1, y1, x2, y2, label_ids]
    boxes: List[List[Any]] = [
        [int(s[cv2.CC_STAT_LEFT]), int(s[cv2.CC_STAT_TOP]),
         int(s[cv2.CC_STAT_LEFT] + s[cv2.CC_STAT_WIDTH]),
         int(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT]), [label]]
        for label, s in enumerate(stats)
        if label != 0
        and s[cv2.CC_STAT_AREA] >= _MIN_COMPONENT_AREA
        and s[cv2.CC_STAT_HEIGHT] >= _MIN_COMPONENT_HEIGHT
        and s[cv2.CC_STAT_WIDTH] < w * 0.7
        and s[cv2.CC_STAT_HEIGHT] < h * 0.7
    ]
    if not boxes:
        return [], 0

    letter_h = float(np.percentile([b[3] - b[1] for b in boxes], 75))
    gap = 0.3 * letter_h
    tall = sorted((b for b in boxes if b[3] - b[1] >= 0.5 * letter_h), key=lambda b: b[1])
    small = [b for b in boxes if b[3] - b[1] < 0.5 * letter_h]

    # Step 2: rows from tall components
    rows: List[Dict[str, Any]] = []
    for b in tall:
        best, best_overlap = None, 0.0
        for row in rows:
            overlap = min(b[3], row["bottom"]) - max(b[1], row["top"])
            frac = overlap / max(1, min(b[3] - b[1], row["bottom"] - row["top"]))
            if frac >= 0.3 and frac > best_overlap:
                best, best_overlap = row, frac
        if best is None:
            rows.append({"top": b[1], "bottom": b[3], "boxes": [b]})
        else:
            best["top"], best["bottom"] = min(best["top"], b[1]), max(best["bottom"], b[3])
            best["boxes"].append(b)
    if not rows:
        rows.append({"top": 0, "bottom": h, "boxes": []})
    for row in rows:
        tops = [b[1] for b in row["boxes"]] or [row["top"]]
        bottoms = [b[3] for b in row["boxes"]] or [row["bottom"]]
        row["core"] = (float(np.median(tops)), float(np.median(bottoms)))

    # Step 3: small marks join each other, then the nearest row core
    small_groups: List[List[Any]] = []
    for b in sorted(small, key=lambda b: (b[1], b[0])):
        for g in small_groups:
            if b[0] <= g[2] + gap and g[0] <= b[2] + gap and b[1] <= g[3] + gap and g[1] <= b[3] + gap:
                g[:4] = [min(g[0], b[0]), min(g[1], b[1]), max(g[2], b[2]), max(g[3], b[3])]
                g[4] = g[4] + b[4]
                break
        else:
            small_groups.append(list(b))
    for g in small_groups:
        cy = (g[1] + g[3]) / 2
        dist = [max(row["core"][0] - cy, cy - row["core"][1], 0.0) for row in rows]
        rows[int(np.argmin(dist))]["boxes"].append(g)

    # Step 4: merge horizontally overlapping groups inside each row
    rows.sort(key=lambda r: sum(r["core"]) / 2)
    shapes: List[Tuple[int, List[Any]]] = []
    for row_idx, row in enumerate(rows):
        merged: List[List[Any]] = []
        for b in sorted(row["boxes"], key=lambda b: b[0]):
            if merged and b[0] <= merged[-1][2] + gap:
                m = merged[-1]
                merged[-1] = [min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3]), m[4] + b[4]]
            else:
                merged.append(list(b))
        shapes.extend((row_idx, m) for m in merged)

    # Step 5: assign characters in reading order and measure baselines
    pairs = list(zip(charset, shapes))
    baselines: Dict[int, float] = {}
    for row_idx in range(len(rows)):
        bottoms = [m[3] for ch, (r, m) in pairs if r == row_idx and ch in _ON_BASELINE]
        if not bottoms:
            bottoms = [m[3] for r, m in shapes if r == row_idx]
        baselines[row_idx] = float(np.median(bottoms)) if bottoms else 0.0

    results: List[Tuple[str, np.ndarray, float]] = []
    for ch, (row_idx, (x1, y1, x2, y2, ids)) in pairs:
        px1, py1 = max(0, x1 - 2), max(0, y1 - 2)
        px2, py2 = min(w, x2 + 2), min(h, y2 + 2)
        # Keep only this character's own components, not ink from neighbours
        crop = np.where(np.isin(labels[py1:py2, px1:px2], ids), 255, 0).astype(np.uint8)
        if crop.shape[0] > 0 and crop.shape[1] > 0:
            results.append((ch, crop, float(py2) - baselines[row_idx]))

    return results, len(shapes)


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


def _save_extracted_glyphs(
    extracted_glyphs: List[Tuple[str, np.ndarray, float]],
    profile_id: int,
    db: Any,
) -> List[Dict[str, float]]:
    """Measure extracted glyph crops, save mask PNGs, and update DB records."""
    metrics_list: List[Dict[str, float]] = []

    for char, mask_crop, baseline_offset in extracted_glyphs:
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

    stroke_ws = [m["stroke_w"] for m in metrics_list]
    slants = [m["slant"] for m in metrics_list]

    # Load existing metrics so we merge rather than overwrite cross-scan data
    try:
        existing: Dict[str, Any] = json.loads(profile.metrics_json or "{}")
    except json.JSONDecodeError:
        existing = {}

    # x-height comes from every short lowercase letter in the profile, not just
    # this sheet, so uploading the digits sheet last doesn't change it
    x_heights = [
        g.height
        for g in db.query(Glyph).filter(Glyph.profile_id == profile_id, Glyph.accepted == True)  # noqa: E712
        if g.char in X_HEIGHT_CHARS
    ]

    updated_metrics = {
        **existing,
        "x_height": float(np.median(x_heights)) if x_heights else existing.get("x_height", 0.0),
        "stroke_w": float(np.median(stroke_ws)),
        "slant": float(np.median(slants)),
        "avg_char_gap": existing.get("avg_char_gap", 0.0),  # TODO: measure inter-glyph gaps
        "avg_word_gap": existing.get("avg_word_gap", 0.0),  # TODO: measure inter-word gaps
    }

    profile.metrics_json = json.dumps(updated_metrics)
    db.commit()
    logger.info("Profile %d metrics updated: %s", profile_id, updated_metrics)
