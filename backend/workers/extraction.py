"""Glyph extraction pipeline.

This module is executed in a background daemon thread for every uploaded scan.
It progresses through a series of computer-vision stages and writes Glyph
records to the database for the characters found on the sheet.

Stage overview
--------------
1.  Load the photo.
2.  Detect the four ArUco corner markers of the printed template. Their ids
    say which sheet this is, so a photo of a different sheet than the one
    chosen is rejected.
3.  Warp the sheet onto the template's coordinates (``PX_PER_MM`` pixels per
    millimetre) and read every box by its known position, which gives the
    character and the variant directly. The printed baseline in each box is
    located to correct small misregistration and sets ``baseline_offset``.
4.  Turn ink darkness into a soft alpha mask, so anti-aliasing and the pen's
    texture survive. The light cyan guides drop out by colour (or by
    lightness on a black-and-white print). Specks are removed and pinholes
    filled. Empty boxes, and ink touching a box edge, are flagged.
5.  Save the variants found, replacing only those, and update the profile
    metrics.

A photo without markers (a hand-drawn sheet) falls back to grouping ink into
rows and characters and assigning the charset in reading order, one variant
per character.

``extract_sheet`` runs stages 2-4 without touching the database, so tests
can call it on a synthetic photo.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps

from app import jobs as job_store
from app.models.glyph import Glyph
from app.models.profile import Profile
from app.storage import get_file_path, save_glyph_mask
from workers.template import (
    ARUCO_DICT,
    CANVAS_H_PX,
    CANVAS_W_PX,
    CORNERS,
    MARKER_SIZE_MM,
    PX_PER_MM,
    SHEET_CONFIGS,
    Box,
    decode_marker_id,
    mm_to_px,
    sheet_boxes,
    sheet_markers,
    sheet_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Characters whose height is the x-height (no ascender or descender).
X_HEIGHT_CHARS = set("acemnorsuvwxz")

# Template path. Lengths in millimetres are converted with PX_PER_MM.
_DETECT_MAX_SIDE = 2000      # px; the markers are still ~90 px across at this size
_READ_MARGIN_MM = 0.8        # ink this far past the printed box edge still counts
_INNER_INSET_MM = 0.4        # a mark must reach this far inside the box to belong to it
_BASELINE_SEARCH_MM = 2.0    # how far the printed baseline may sit from where the warp puts it
_SIDE_SEARCH_MM = 1.5        # same for the box sides (boxes are only 2 mm apart)
_LINE_BAND_MM = 0.35         # half-width of a printed guide line
_SPECK_PX = 12               # smaller ink blobs are dust or JPEG noise
_PINHOLE_PX = 8              # smaller gaps inside a stroke are filled
_ALPHA_LO = 0.18             # darkness below this is paper or a printed guide
_GLYPH_PAD_PX = 2
_TEMPLATE_BEARING_PX = 0.5 * PX_PER_MM
_SMALL_MARK_MM = 1.5         # a letter smaller than this both ways is probably a stray mark
_SMALL_CHARS = set(".,'\"-")

ISSUE_EDGE = "Ink touches the box edge, so part of the character may be cut off"
ISSUE_SMALL = "Only a small mark was found, so this may not be the whole character"

# Fallback path: components smaller than this (or flatter than
# _MIN_COMPONENT_HEIGHT px) are specks or leftovers of ruled notebook lines.
_MIN_COMPONENT_AREA = 20
_MIN_COMPONENT_HEIGHT = 4
_FALLBACK_ALPHA_LO = 0.06
_FALLBACK_BEARING_PX = 3.0

# Characters whose bottom sits on the baseline; used to locate each row's baseline.
_ON_BASELINE = set("abcdefhiklmnorstuvwxzABCDEFGHIKLMNOPRSTUVWXYZ0123456789!?:")

# Colour of the stored masks. Only the alpha channel matters: the preview and
# the PDF renderer recolour glyphs to the chosen ink.
_INK_BGR = (25, 25, 35)


class SheetError(ValueError):
    """The photo can't be read as the chosen sheet. The message is shown to the person."""


@dataclass
class ExtractedGlyph:
    """One variant of one character, cut out of a sheet."""

    char: str
    variant: int
    alpha: np.ndarray            # float32 in [0, 1], 1 = solid ink
    baseline_offset: float       # px the bottom edge of ``alpha`` sits below the baseline
    issue: Optional[str] = None  # set when the glyph needs a human look before use


@dataclass
class SheetResult:
    glyphs: List[ExtractedGlyph]
    used_markers: bool
    empty: List[Tuple[str, int]] = field(default_factory=list)
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

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
        1-based sheet index chosen in the UI.
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


def extract_sheet(
    image: np.ndarray,
    sheet_number: int,
    progress: Optional[Callable[[int, str], None]] = None,
) -> SheetResult:
    """Read every glyph on one photographed sheet. Touches no database or job.

    Parameters
    ----------
    image:
        BGR photo or scan.
    sheet_number:
        The sheet the person said this is.
    progress:
        Optional ``(percent, stage)`` callback.

    Raises
    ------
    SheetError
        Wrong sheet, too few corner markers visible, or no handwriting found.
    """
    report = progress or (lambda pct, stage: None)
    if sheet_number not in SHEET_CONFIGS:
        raise SheetError(f"There is no sheet {sheet_number}.")

    report(15, "Detecting markers")
    found = _detect_markers(image)
    if not found:
        return _extract_without_markers(image, sheet_number, report)

    seen_sheet = Counter(decode_marker_id(mid)[0] for mid in found).most_common(1)[0][0]
    if seen_sheet != sheet_number:
        raise SheetError(
            f"This photo is of Sheet {seen_sheet} ({sheet_name(seen_sheet).lower()}), but it was "
            f"uploaded as Sheet {sheet_number} ({sheet_name(sheet_number).lower()}). "
            f"Choose Sheet {seen_sheet} and upload it again."
        )
    mine = {mid: pts for mid, pts in found.items() if decode_marker_id(mid)[0] == sheet_number}
    if len(mine) < 3:
        missing = [CORNERS[m.corner] for m in sheet_markers(sheet_number) if m.marker_id not in mine]
        raise SheetError(
            f"Only {len(mine)} of the 4 corner squares are visible (missing: {', '.join(missing)}). "
            "Retake the photo with the whole sheet in view."
        )

    warped = _rectify(image, mine, sheet_number)
    return _read_boxes(warped, sheet_number, report)


# ---------------------------------------------------------------------------
# Internal pipeline
# ---------------------------------------------------------------------------

def _run(
    job_id: str,
    scan_path: str,
    profile_id: int,
    sheet_number: int,
    db: Any,
) -> None:
    """Internal pipeline: called by :func:`run_extraction` inside a try/except."""

    def progress(pct: int, stage: str) -> None:
        job_store.update_job(job_id, status="running", progress=pct, stage=stage)

    progress(5, "Loading image")
    image = _load_image(get_file_path(scan_path))
    logger.info("Loaded scan %s: %dx%d px", scan_path, image.shape[1], image.shape[0])

    result = extract_sheet(image, sheet_number, progress)
    if result.warning:
        job_store.update_job(job_id, warning=result.warning)

    progress(70, "Measuring glyphs")
    metrics_list = _save_glyphs(
        glyphs=result.glyphs,
        profile_id=profile_id,
        db=db,
        bearing=_TEMPLATE_BEARING_PX if result.used_markers else _FALLBACK_BEARING_PX,
        tag=job_id[:8],
    )

    progress(90, "Building profile metrics")
    _update_profile_metrics(profile_id=profile_id, metrics_list=metrics_list, db=db)

    job_store.update_job(job_id, status="done", progress=100, stage="Complete")
    logger.info("Extraction complete for job %s: %d glyphs saved", job_id, len(metrics_list))


def _load_image(path: Path) -> np.ndarray:
    """Load a photo as BGR, upright according to its EXIF orientation.

    Pillow rather than cv2.imread, which can't open non-ASCII paths on Windows.
    """
    try:
        with Image.open(path) as im:
            rgb = ImageOps.exif_transpose(im).convert("RGB")
    except Exception as exc:
        raise SheetError("Couldn't read this file as an image. Upload a JPEG, PNG or TIFF photo or scan.") from exc
    return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# Template path: markers, warp, boxes
# ---------------------------------------------------------------------------

def _detect_markers(image: np.ndarray) -> Dict[int, np.ndarray]:
    """Find our corner markers. Returns ``{marker_id: 4x2 corners}`` in image pixels,
    clockwise from the marker's own top-left corner."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = min(1.0, _DETECT_MAX_SIDE / max(gray.shape[:2]))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray

    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(ARUCO_DICT), cv2.aruco.DetectorParameters()
    )
    corners, ids, _ = detector.detectMarkers(small)
    found: Dict[int, np.ndarray] = {}
    if ids is None:
        return found

    # The detector's corners are only good to a pixel or so (more on the
    # shrunken copy), so refine them on the full image
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
    win = max(3, int(np.ceil(1 / scale)) + 1)
    for quad, mid in zip(corners, ids.flatten()):
        mid = int(mid)
        if decode_marker_id(mid) is None or mid in found:
            continue
        pts = ((quad.reshape(4, 2) + 0.5) / scale - 0.5).astype(np.float32)
        found[mid] = cv2.cornerSubPix(gray, pts.reshape(-1, 1, 2), (win, win), (-1, -1), criteria).reshape(4, 2)
    return found


def _rectify(image: np.ndarray, markers: Dict[int, np.ndarray], sheet_number: int) -> np.ndarray:
    """Warp the photo onto the template's canonical canvas using every marker corner."""
    layout = {m.marker_id: m for m in sheet_markers(sheet_number)}
    ids = sorted(markers)
    src = np.float32([p for mid in ids for p in markers[mid]])
    dst = np.float32([(mm_to_px(x), mm_to_px(y)) for mid in ids for x, y in layout[mid].corner_points()])

    # warpPerspective samples without averaging, so shrink a much sharper scan
    # first or fine strokes alias
    sides = [np.linalg.norm(markers[mid][i] - markers[mid][(i + 1) % 4]) for mid in ids for i in range(4)]
    src_px_per_mm = float(np.mean(sides)) / MARKER_SIZE_MM
    if src_px_per_mm > 1.5 * PX_PER_MM:
        f = 1.2 * PX_PER_MM / src_px_per_mm
        image = cv2.resize(image, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        src = (src + 0.5) * f - 0.5

    H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.5 * PX_PER_MM)
    if H is None:
        raise SheetError("Couldn't line the sheet up from its corner squares. Retake the photo from straight above.")
    err = np.linalg.norm(cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2) - dst, axis=1)
    if float(np.median(err)) > PX_PER_MM:
        raise SheetError("The sheet looks bent or folded. Lay it flat and retake the photo.")

    return cv2.warpPerspective(
        image, H, (CANVAS_W_PX, CANVAS_H_PX), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def _paper_level(image: np.ndarray, window_px: int) -> np.ndarray:
    """Local paper brightness per channel, so shadows and uneven light divide out.

    The brightest value in a window wider than any pen stroke, worked out at
    quarter resolution for speed.
    """
    h, w = image.shape[:2]
    small = cv2.resize(image, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    k = max(3, (window_px // 4) | 1)
    bg = cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)


def _darkness(region: np.ndarray, paper: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Ink darkness in [0, 1] and a mask of guide-coloured pixels.

    Darkness comes from the green channel, where the light cyan guides are
    nearly white but black, blue and red inks are all dark.
    """
    norm = np.minimum(region.astype(np.float32) / np.maximum(paper.astype(np.float32), 1.0), 1.0)
    b, g, r = cv2.split(norm)
    guide = ((g - r) > 0.10) & ((b - r) > 0.10)
    return 1.0 - g, guide


def _find_line(profile: np.ndarray, expected: float, search: float) -> Optional[float]:
    """Sub-pixel position of the strongest printed line near ``expected`` in a
    coverage profile, or ``None`` if nothing stands out from the background."""
    lo, hi = max(0, int(np.floor(expected - search))), min(profile.size, int(np.ceil(expected + search)) + 1)
    seg = profile[lo:hi]
    if seg.size < 3:
        return None
    i = int(np.argmax(seg))
    if seg[i] - float(np.median(seg)) < 0.25:
        return None
    a, b = max(0, i - 3), min(seg.size, i + 4)
    w = seg[a:b] - float(np.median(seg))
    w = np.clip(w, 0, None)
    return lo + float((np.arange(a, b) * w).sum() / max(w.sum(), 1e-6))


@dataclass
class _BoxRead:
    dark: np.ndarray                     # guide-free darkness of the read region
    inner: Tuple[int, int, int, int]     # the box proper, inset, in region coordinates
    baseline: float                      # baseline as an edge coordinate in the region


def _read_box(warped: np.ndarray, paper: np.ndarray, box: Box) -> _BoxRead:
    """Cut out one box, find its printed lines, and remove them."""
    px = PX_PER_MM
    search = int(np.ceil(_BASELINE_SEARCH_MM * px))
    margin = int(round(_READ_MARGIN_MM * px))
    bx0, by0 = int(round(box.x * px)), int(round(box.y * px))
    bx1, by1 = int(round((box.x + box.w) * px)), int(round((box.y + box.h) * px))

    # Generous crop: the box may sit a little off where the warp puts it
    cx0, cy0 = max(0, bx0 - margin - search), max(0, by0 - margin - search)
    cx1, cy1 = min(CANVAS_W_PX, bx1 + margin + search), min(CANVAS_H_PX, by1 + margin + search)
    dark, guide = _darkness(warped[cy0:cy1, cx0:cx1], paper[cy0:cy1, cx0:cx1])
    line_like = (guide | ((dark > 0.07) & (dark < 0.35))).astype(np.float32)

    # Baseline: strongest horizontal line near where it should be
    inset = int(px)
    rows = line_like[:, bx0 - cx0 + inset: bx1 - cx0 - inset].mean(axis=1)
    expected_base = mm_to_px(box.baseline_y) - cy0
    found = _find_line(rows, expected_base, _BASELINE_SEARCH_MM * px)
    dy = 0.0 if found is None else found - expected_base

    # Sides: both printed edges together, so the neighbouring box 2 mm away can't win
    cols = line_like[by0 - cy0 + inset: by1 - cy0 - inset, :].mean(axis=0)
    left, right = mm_to_px(box.x) - cx0, mm_to_px(box.x + box.w) - cx0
    side_search = int(_SIDE_SEARCH_MM * px)
    scores = [
        (cols[int(round(left + s))] + cols[int(round(right + s))], s)
        for s in range(-side_search, side_search + 1)
        if 0 <= left + s and right + s < cols.size - 1
    ]
    dx = 0.0
    if scores:
        best, s = max(scores)
        if best / 2 - float(np.median(cols)) >= 0.25:
            dx = float(s)

    # Drop the guides: guide-coloured pixels that aren't dark ink, and on a
    # black-and-white print, anything faint along the printed lines
    dark = np.where(guide & (dark < 0.45), 0.0, dark)
    band = int(np.ceil(_LINE_BAND_MM * px))
    faint = dark < 0.3
    for y_mm in (box.baseline_y, box.x_height_y, box.y, box.y + box.h):
        yc = int(round(mm_to_px(y_mm) - cy0 + dy))
        sl = (slice(max(0, yc - band), max(0, yc + band + 1)), slice(None))
        dark[sl] = np.where(faint[sl], 0.0, dark[sl])
    for x_mm in (box.x, box.x + box.w):
        xc = int(round(mm_to_px(x_mm) - cx0 + dx))
        sl = (slice(None), slice(max(0, xc - band), max(0, xc + band + 1)))
        dark[sl] = np.where(faint[sl], 0.0, dark[sl])

    # Read region: the box where it really is, plus a margin for slight overflow
    ox, oy = int(round(dx)), int(round(dy))
    ex0, ey0 = max(0, bx0 - cx0 + ox - margin), max(0, by0 - cy0 + oy - margin)
    ex1, ey1 = min(dark.shape[1], bx1 - cx0 + ox + margin), min(dark.shape[0], by1 - cy0 + oy + margin)
    ins = int(round(_INNER_INSET_MM * px))
    inner = (margin + ins, margin + ins, ex1 - ex0 - margin - ins, ey1 - ey0 - margin - ins)
    baseline_edge = mm_to_px(box.baseline_y) + 0.5 - cy0 + dy - ey0
    return _BoxRead(dark=dark[ey0:ey1, ex0:ex1], inner=inner, baseline=baseline_edge)


def _box_glyph(read: _BoxRead, char: str, hi: float) -> Optional[Tuple[np.ndarray, float, Optional[str]]]:
    """Turn a box's darkness into ``(alpha, baseline_offset, issue)``, or ``None`` if it's empty."""
    alpha = np.clip((read.dark - _ALPHA_LO) / (hi - _ALPHA_LO), 0.0, 1.0).astype(np.float32)
    n, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 0.1).astype(np.uint8), connectivity=8)
    ix0, iy0, ix1, iy1 = read.inner

    keep = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < _SPECK_PX:
            continue
        own = labels[y:y + h, x:x + w] == i
        if alpha[y:y + h, x:x + w][own].max() < 0.5:
            continue  # faint smudge
        # Must reach inside the box proper: not a neighbour's overflow or the label above
        ox0, oy0, ox1, oy1 = max(x, ix0), max(y, iy0), min(x + w, ix1), min(y + h, iy1)
        if ox0 >= ox1 or oy0 >= oy1 or not (labels[oy0:oy1, ox0:ox1] == i).any():
            continue
        keep.append(i)
    if not keep:
        return None

    mask = np.isin(labels, keep)
    touches = bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())
    # Keep the soft edge just outside the component threshold
    halo = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    alpha = _fill_pinholes(np.where(halo, alpha, 0.0).astype(np.float32))

    ys, xs = np.nonzero(mask)
    ink_h, ink_w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
    rows, cols = alpha.shape
    y1, y2 = max(0, ys.min() - _GLYPH_PAD_PX), min(rows, ys.max() + 1 + _GLYPH_PAD_PX)
    x1, x2 = max(0, xs.min() - _GLYPH_PAD_PX), min(cols, xs.max() + 1 + _GLYPH_PAD_PX)

    issue = None
    if touches:
        issue = ISSUE_EDGE
    elif char not in _SMALL_CHARS and max(ink_h, ink_w) < _SMALL_MARK_MM * PX_PER_MM:
        issue = ISSUE_SMALL
    return alpha[y1:y2, x1:x2], float(y2) - read.baseline, issue


def _read_boxes(
    warped: np.ndarray,
    sheet_number: int,
    report: Callable[[int, str], None],
) -> SheetResult:
    """Read every box of a rectified sheet."""
    report(30, "Cleaning")
    paper = _paper_level(warped, window_px=5 * PX_PER_MM)

    report(45, "Extracting cells")
    boxes = sheet_boxes(sheet_number)
    reads = [_read_box(warped, paper, box) for box in boxes]

    # One darkness scale for the whole sheet, so light and heavy strokes keep
    # their difference; the darkest quarter of the ink becomes fully opaque
    ink = np.concatenate([r.dark[r.dark > 0.3] for r in reads])
    hi = float(np.clip(np.percentile(ink, 75), 0.45, 0.9)) if ink.size else 0.6

    glyphs: List[ExtractedGlyph] = []
    empty: List[Tuple[str, int]] = []
    for box, read in zip(boxes, reads):
        cut = _box_glyph(read, box.char, hi)
        if cut is None:
            empty.append((box.char, box.variant))
            continue
        alpha, offset, issue = cut
        glyphs.append(ExtractedGlyph(box.char, box.variant, alpha, offset, issue))

    if not glyphs:
        raise SheetError("No handwriting found in any box. Check the photo is sharp and the pen is dark enough.")
    return SheetResult(glyphs, used_markers=True, empty=empty, warning=_template_warning(len(boxes), glyphs, empty))


def _list_boxes(items: List[Tuple[str, int]]) -> str:
    """``[('q', 2), ('z', 0), ('z', 1)]`` -> ``"q box 3; z boxes 1 and 2"``."""
    by_char: Dict[str, List[int]] = {}
    for ch, v in items:
        by_char.setdefault(ch, []).append(v + 1)
    parts = []
    for ch, nums in by_char.items():
        if len(nums) == 1:
            parts.append(f"{ch} box {nums[0]}")
        else:
            parts.append(f"{ch} boxes {', '.join(map(str, nums[:-1]))} and {nums[-1]}")
    return "; ".join(parts)


def _template_warning(n_boxes: int, glyphs: List[ExtractedGlyph], empty: List[Tuple[str, int]]) -> Optional[str]:
    flagged: Dict[str, List[Tuple[str, int]]] = {}
    for g in glyphs:
        if g.issue:
            flagged.setdefault(g.issue, []).append((g.char, g.variant))
    if not empty and not flagged:
        return None
    parts = [f"Read {len(glyphs)} of {n_boxes} boxes."]
    if empty:
        parts.append(f"Empty, so left as they were: {_list_boxes(empty)}.")
    for issue, items in flagged.items():
        parts.append(f"Held back for review. {issue}: {_list_boxes(items)}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Glyph clean-up shared by both paths
# ---------------------------------------------------------------------------

def _fill_pinholes(alpha: np.ndarray) -> np.ndarray:
    """Fill tiny gaps inside strokes (paper texture, a skipping pen) without
    closing real counters like the eye of an 'e'."""
    gaps = (alpha <= 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(gaps, connectivity=4)
    rows, cols = alpha.shape
    grown = None
    out = alpha
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area > _PINHOLE_PX or x == 0 or y == 0 or x + w == cols or y + h == rows:
            continue
        if grown is None:
            grown = cv2.dilate(alpha, np.ones((5, 5), np.uint8))
            out = alpha.copy()
        hole = labels[y:y + h, x:x + w] == i
        out[y:y + h, x:x + w][hole] = grown[y:y + h, x:x + w][hole]
    return out


def _alpha_png(alpha: np.ndarray) -> Optional[bytes]:
    """Encode a soft mask as an RGBA PNG: constant ink colour, darkness in alpha."""
    bgra = np.empty((*alpha.shape, 4), dtype=np.uint8)
    bgra[..., :3] = _INK_BGR
    bgra[..., 3] = np.round(np.clip(alpha, 0.0, 1.0) * 255).astype(np.uint8)
    ok, buf = cv2.imencode(".png", bgra)
    return buf.tobytes() if ok else None


# ---------------------------------------------------------------------------
# Fallback path: no markers, characters in reading order
# ---------------------------------------------------------------------------

def _extract_without_markers(
    image: np.ndarray,
    sheet_number: int,
    report: Callable[[int, str], None],
) -> SheetResult:
    """Read a hand-drawn sheet: one sample per character, in reading order."""
    report(30, "Cleaning")
    binary, dark = _binarise(image)
    ink = dark[binary > 0]
    hi = float(np.clip(np.percentile(ink, 75), 0.3, 0.9)) if ink.size else 0.5
    alpha = np.clip((dark - _FALLBACK_ALPHA_LO) / (hi - _FALLBACK_ALPHA_LO), 0.0, 1.0).astype(np.float32)

    report(45, "Extracting cells")
    charset = SHEET_CONFIGS[sheet_number]["charset"]
    crops, shapes_found = _extract_character_crops(binary=binary, alpha=alpha, charset=charset)
    if not crops:
        raise SheetError("No handwriting found on this sheet. Check the photo is sharp and well lit.")

    warning = None
    if shapes_found != len(charset):
        # Characters are assigned in order, so a miscount shifts everything after it
        warning = (
            f"Found {shapes_found} characters but this sheet has {len(charset)}. "
            "Some glyphs are probably labelled wrong; check them on the review page "
            "or retake the photo with the characters in the template order. "
            "If this is a printed NeverWrite sheet, its corner squares weren't found: "
            "retake the photo with all four in view and in focus."
        )
    glyphs = [ExtractedGlyph(ch, 0, _fill_pinholes(crop), offset) for ch, crop, offset in crops]
    return SheetResult(glyphs, used_markers=False, warning=warning)


def _binarise(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Binary ink mask for grouping (255 = ink) and soft darkness for the glyphs.

    Uses morphological illumination background subtraction to flatten lighting
    gradients and eliminate paper shadows from smartphone photos. Horizontal
    ruled notebook lines are removed from both outputs.
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

    # Soft darkness relative to the local paper, with the ruled lines taken
    # out too except where a stroke crosses them
    dark = np.clip(diff.astype(np.float32) / np.maximum(bg.astype(np.float32), 1.0), 0.0, 1.0)
    lines = cv2.dilate(horiz_lines, np.ones((3, 3), np.uint8)) > 0
    dark[lines & (binary == 0)] = 0.0

    return binary, dark


def _extract_character_crops(
    binary: np.ndarray,
    alpha: np.ndarray,
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
        Clean binary uint8 image (255 = ink), used to find the characters.
    alpha:
        Soft ink mask (float32, 0..1) the crops are cut from.
    charset:
        Ordered string of expected characters in reading order.

    Returns
    -------
    (list of (char, alpha_crop, baseline_offset), number_of_characters_found)
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
    halo = np.ones((3, 3), np.uint8)
    for ch, (row_idx, (x1, y1, x2, y2, ids)) in pairs:
        px1, py1 = max(0, x1 - 2), max(0, y1 - 2)
        px2, py2 = min(w, x2 + 2), min(h, y2 + 2)
        # Keep only this character's own components (and their soft edge), not ink from neighbours
        own = cv2.dilate(np.isin(labels[py1:py2, px1:px2], ids).astype(np.uint8), halo)
        crop = np.where(own > 0, alpha[py1:py2, px1:px2], 0.0).astype(np.float32)
        if crop.shape[0] > 0 and crop.shape[1] > 0:
            results.append((ch, crop, float(py2) - baselines[row_idx]))

    return results, len(shapes)


# ---------------------------------------------------------------------------
# Saving and metrics
# ---------------------------------------------------------------------------

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


def _save_glyphs(
    glyphs: List[ExtractedGlyph],
    profile_id: int,
    db: Any,
    bearing: float,
    tag: str,
) -> List[Dict[str, float]]:
    """Save mask PNGs and add or replace exactly the variants this scan contains.

    Variants the scan doesn't contain (empty boxes) keep whatever they had.
    """
    metrics_list: List[Dict[str, float]] = []

    for g in glyphs:
        bh, bw = g.alpha.shape[:2]
        if bh == 0 or bw == 0:
            continue
        png = _alpha_png(g.alpha)
        if png is None:
            logger.warning("Failed to encode PNG for char=%r", g.char)
            continue
        mask_path = save_glyph_mask(data=png, profile_id=profile_id, char=g.char, variant=g.variant, tag=tag)

        glyph = (
            db.query(Glyph)
            .filter(Glyph.profile_id == profile_id, Glyph.char == g.char, Glyph.variant_index == g.variant)
            .first()
        )
        if glyph is None:
            glyph = Glyph(profile_id=profile_id, char=g.char, variant_index=g.variant, svg_path=None)
            db.add(glyph)
        elif glyph.mask_path and glyph.mask_path != mask_path:
            get_file_path(glyph.mask_path).unlink(missing_ok=True)

        glyph.mask_path = mask_path
        glyph.width = float(bw)
        glyph.height = float(bh)
        glyph.baseline_offset = g.baseline_offset
        glyph.bearing_left = bearing
        glyph.bearing_right = bearing
        glyph.accepted = g.issue is None
        glyph.issue = g.issue

        metrics_list.append({
            "stroke_w": _estimate_stroke_width((g.alpha > 0.5).astype(np.uint8) * 255),
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
        List of per-glyph metric dicts from :func:`_save_glyphs`.
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
