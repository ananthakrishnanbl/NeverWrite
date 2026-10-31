"""Handwriting page renderer using ReportLab.

Takes a RenderJob from the database, loads the associated Profile's accepted
glyphs, and composites them into a PDF page that mimics natural handwriting.

Randomness is controlled by a seedable mulberry32 PRNG so renders are
reproducible given the same seed.
"""
from __future__ import annotations

import json
import logging
import math
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from app import jobs as job_store
from app.config import EXPORT_DIR
from app.models.glyph import Glyph
from app.models.profile import Profile
from app.models.render_job import RenderJob
from app.storage import get_file_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PRNG
# ---------------------------------------------------------------------------

class Mulberry32:
    """Seedable 32-bit PRNG using the mulberry32 algorithm.

    Produces a deterministic sequence of floats in ``[0, 1)`` given the same
    integer seed, enabling fully reproducible renders.
    """

    def __init__(self, seed: int) -> None:
        """Initialise the PRNG with an integer *seed*."""
        self._state = seed & 0xFFFFFFFF

    def next_float(self) -> float:
        """Advance the state and return the next float in ``[0, 1)``."""
        self._state = (self._state + 0x6D2B79F5) & 0xFFFFFFFF
        z = self._state
        z = ((z ^ (z >> 15)) * (z | 1)) & 0xFFFFFFFF
        z ^= z + ((z ^ (z >> 7)) * (z | 61)) & 0xFFFFFFFF
        z = (z ^ (z >> 14)) & 0xFFFFFFFF
        return z / 0x100000000

    def next_int(self, lo: int, hi: int) -> int:
        """Return a random integer in ``[lo, hi]`` inclusive."""
        return lo + int(self.next_float() * (hi - lo + 1))

    def next_range(self, lo: float, hi: float) -> float:
        """Return a random float in ``[lo, hi]``."""
        return lo + self.next_float() * (hi - lo)


# ---------------------------------------------------------------------------
# Default render settings
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "font_size": 18,          # pt — glyph scaling target height
    "line_spacing": 1.4,      # multiple of font_size
    "ink_color": (0.05, 0.05, 0.20),  # RGB in [0,1] — dark navy blue
    "margins": {              # all in mm
        "top": 20,
        "bottom": 20,
        "left": 20,
        "right": 20,
    },
    "paper_type": "A4",       # "A4" or "letter"
}

_PAPER_SIZES: Dict[str, Tuple[float, float]] = {
    "A4": A4,
    "letter": letter,
}

# Jitter parameters
_ROTATION_JITTER_DEG = 2.0   # ± degrees
_SIZE_JITTER_FRAC = 0.03     # ± 3 % of target height
_BASELINE_WOBBLE_PT = 1.0    # ± points of vertical displacement

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_render(job_id: str, render_id: int, db_session_factory) -> None:
    """Execute the full render pipeline for a RenderJob.

    This function is designed to run in a daemon thread launched by
    :func:`app.jobs.run_in_thread`.

    Parameters
    ----------
    job_id:
        In-memory job ID to update with progress.
    render_id:
        Primary key of the ``RenderJob`` record in the database.
    db_session_factory:
        Callable returning a fresh SQLAlchemy ``Session``.
    """
    db = db_session_factory()
    try:
        _run(job_id, render_id, db)
    except Exception as exc:
        logger.exception("Renderer failed for job %s / render %d", job_id, render_id)
        job_store.update_job(job_id, status="error", error=str(exc))
        # Persist error status to DB
        rj = db.query(RenderJob).filter(RenderJob.id == render_id).first()
        if rj is not None:
            rj.status = "error"
            rj.error = str(exc)
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal pipeline
# ---------------------------------------------------------------------------


def _run(job_id: str, render_id: int, db: Any) -> None:
    """Internal render pipeline."""

    # ------------------------------------------------------------------
    # 1. Load RenderJob from DB
    # ------------------------------------------------------------------
    job_store.update_job(job_id, status="running", progress=5, stage="Loading job")
    render_job: Optional[RenderJob] = db.query(RenderJob).filter(RenderJob.id == render_id).first()
    if render_job is None:
        raise ValueError(f"RenderJob {render_id} not found in database.")

    render_job.status = "running"
    db.commit()

    # ------------------------------------------------------------------
    # 2. Load Profile + Glyphs
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=10, stage="Loading glyphs")
    profile: Optional[Profile] = db.query(Profile).filter(Profile.id == render_job.profile_id).first()
    if profile is None:
        raise ValueError(f"Profile {render_job.profile_id} not found.")

    glyphs: List[Glyph] = (
        db.query(Glyph)
        .filter(Glyph.profile_id == render_job.profile_id, Glyph.accepted == True)  # noqa: E712
        .all()
    )

    # Build a lookup: char → [Glyph, ...]
    glyph_map: Dict[str, List[Glyph]] = {}
    for g in glyphs:
        glyph_map.setdefault(g.char, []).append(g)

    # ------------------------------------------------------------------
    # 3. Parse settings
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=15, stage="Parsing settings")
    try:
        user_settings: Dict[str, Any] = json.loads(render_job.settings_json or "{}")
    except json.JSONDecodeError:
        user_settings = {}

    settings = {**_DEFAULT_SETTINGS, **user_settings}
    font_size_pt: float = float(settings.get("font_size", _DEFAULT_SETTINGS["font_size"]))
    line_spacing: float = float(settings.get("line_spacing", _DEFAULT_SETTINGS["line_spacing"]))
    ink_color: Tuple[float, float, float] = tuple(settings.get("ink_color", _DEFAULT_SETTINGS["ink_color"]))  # type: ignore
    margins_mm: Dict[str, float] = settings.get("margins", _DEFAULT_SETTINGS["margins"])
    paper_type: str = settings.get("paper_type", "A4")

    paper_size = _PAPER_SIZES.get(paper_type, A4)
    page_w, page_h = paper_size

    margin_top = float(margins_mm.get("top", 20)) * mm
    margin_bottom = float(margins_mm.get("bottom", 20)) * mm
    margin_left = float(margins_mm.get("left", 20)) * mm
    margin_right = float(margins_mm.get("right", 20)) * mm

    text_width = page_w - margin_left - margin_right
    usable_height = page_h - margin_top - margin_bottom

    line_height = font_size_pt * line_spacing

    # ------------------------------------------------------------------
    # 4. Initialise PRNG
    # ------------------------------------------------------------------
    rng = Mulberry32(render_job.seed)

    # ------------------------------------------------------------------
    # 5 & 6. Word-wrap text into lines and plan glyph placements
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=30, stage="Planning layout")

    # Build a list of (char, Glyph | None) pairs in display order
    placements: List[Tuple[str, Optional[Glyph]]] = _plan_placements(
        text=render_job.text,
        glyph_map=glyph_map,
        rng=rng,
    )

    lines = _word_wrap(
        placements=placements,
        text_width=text_width,
        font_size_pt=font_size_pt,
    )

    # ------------------------------------------------------------------
    # 7. Render to PDF with ReportLab
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=50, stage="Rendering PDF")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"render_{render_id}.pdf"
    output_abs = EXPORT_DIR / output_filename
    output_rel = f"exports/{output_filename}"

    c = rl_canvas.Canvas(str(output_abs), pagesize=paper_size)
    c.setFillColorRGB(*ink_color)

    cursor_y = page_h - margin_top  # ReportLab y=0 is bottom-left

    for line_idx, line in enumerate(lines):
        if cursor_y - line_height < margin_bottom:
            # Start a new page when we run out of vertical space
            c.showPage()
            c.setFillColorRGB(*ink_color)
            cursor_y = page_h - margin_top

        cursor_x = margin_left

        for char, glyph in line:
            rotation_deg = rng.next_range(-_ROTATION_JITTER_DEG, _ROTATION_JITTER_DEG)
            size_jitter = 1.0 + rng.next_range(-_SIZE_JITTER_FRAC, _SIZE_JITTER_FRAC)
            baseline_wobble = rng.next_range(-_BASELINE_WOBBLE_PT, _BASELINE_WOBBLE_PT)

            target_h = font_size_pt * size_jitter
            draw_y = cursor_y - target_h + baseline_wobble

            if char == " ":
                # Advance cursor by a word gap
                space_w = _space_width(font_size_pt, glyph_map)
                cursor_x += space_w
                continue

            if glyph is None:
                # No glyph available — use a fallback advance
                cursor_x += font_size_pt * 0.6
                continue

            # Scale glyph to target height
            scale = target_h / max(glyph.height, 1.0)
            draw_w = glyph.width * scale

            abs_mask = get_file_path(glyph.mask_path)
            if abs_mask.exists():
                # Draw glyph image rotated around its centre
                cx = cursor_x + draw_w / 2.0
                cy = draw_y + target_h / 2.0
                c.saveState()
                c.translate(cx, cy)
                c.rotate(rotation_deg)
                c.drawImage(
                    str(abs_mask),
                    x=-draw_w / 2.0,
                    y=-target_h / 2.0,
                    width=draw_w,
                    height=target_h,
                    mask="auto",  # treat white as transparent
                )
                c.restoreState()

            advance = draw_w + glyph.bearing_right * scale
            cursor_x += advance

        cursor_y -= line_height

    c.save()

    # ------------------------------------------------------------------
    # 8. Update DB record
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=95, stage="Saving result")
    render_job.output_path = output_rel
    render_job.status = "done"
    db.commit()

    job_store.update_job(job_id, status="done", progress=100, stage="Complete")
    logger.info("Render %d complete — output: %s", render_id, output_rel)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _plan_placements(
    text: str,
    glyph_map: Dict[str, List[Glyph]],
    rng: Mulberry32,
) -> List[Tuple[str, Optional[Glyph]]]:
    """Map each character in *text* to a randomly selected Glyph variant.

    Parameters
    ----------
    text:
        Input text string (may include spaces and newlines).
    glyph_map:
        Mapping from character to list of available Glyph records.
    rng:
        Seeded PRNG for deterministic variant selection.

    Returns
    -------
    list of (char, Glyph or None)
        Each element is a ``(character, glyph_or_None)`` pair.  Spaces are
        represented as ``(" ", None)``.
    """
    # Track round-robin counters per character to cycle through variants
    counters: Dict[str, int] = {}
    result: List[Tuple[str, Optional[Glyph]]] = []

    for ch in text:
        if ch == "\n":
            result.append(("\n", None))
            continue
        if ch == " ":
            result.append((" ", None))
            continue

        variants = glyph_map.get(ch)
        if not variants:
            result.append((ch, None))
            continue

        # Cycle with a PRNG nudge so the sequence is not purely sequential
        counter = counters.get(ch, 0)
        nudge = rng.next_int(0, max(0, len(variants) - 1))
        idx = (counter + nudge) % len(variants)
        counters[ch] = counter + 1

        result.append((ch, variants[idx]))

    return result


def _space_width(font_size_pt: float, glyph_map: Dict[str, List[Glyph]]) -> float:
    """Estimate the width of a space character.

    Uses the average width of glyphs available in the map scaled to the target
    font size, multiplied by 0.35 — a common typographic heuristic.

    Parameters
    ----------
    font_size_pt:
        Target font size in points.
    glyph_map:
        Mapping from character to Glyph list.

    Returns
    -------
    float
        Estimated space width in points.
    """
    if not glyph_map:
        return font_size_pt * 0.35

    avg_h = sum(g.height for variants in glyph_map.values() for g in variants) / max(
        sum(len(v) for v in glyph_map.values()), 1
    )
    scale = font_size_pt / max(avg_h, 1.0)
    avg_w = sum(g.width for variants in glyph_map.values() for g in variants) / max(
        sum(len(v) for v in glyph_map.values()), 1
    )
    return avg_w * scale * 0.35


def _word_wrap(
    placements: List[Tuple[str, Optional[Glyph]]],
    text_width: float,
    font_size_pt: float,
) -> List[List[Tuple[str, Optional[Glyph]]]]:
    """Break placements into lines that fit within *text_width*.

    Parameters
    ----------
    placements:
        Ordered list of ``(char, glyph_or_None)`` pairs from
        :func:`_plan_placements`.
    text_width:
        Maximum line width in points.
    font_size_pt:
        Target font size used to estimate advance widths.

    Returns
    -------
    list of list
        Each inner list is a line of ``(char, glyph_or_None)`` pairs.
    """
    lines: List[List[Tuple[str, Optional[Glyph]]]] = []
    current_line: List[Tuple[str, Optional[Glyph]]] = []
    current_x: float = 0.0

    for char, glyph in placements:
        if char == "\n":
            lines.append(current_line)
            current_line = []
            current_x = 0.0
            continue

        if glyph is not None and glyph.height > 0:
            scale = font_size_pt / glyph.height
            advance = (glyph.width + glyph.bearing_right) * scale
        else:
            advance = font_size_pt * 0.6  # fallback advance

        if current_x + advance > text_width and current_line:
            # Wrap: find last space to break at word boundary
            break_idx = _last_space(current_line)
            if break_idx is not None:
                lines.append(current_line[: break_idx])
                current_line = current_line[break_idx + 1 :]  # strip the space
                current_x = sum(
                    (font_size_pt / max(g.height, 1.0)) * (g.width + g.bearing_right)
                    if g is not None and g.height > 0
                    else font_size_pt * 0.6
                    for _, g in current_line
                )
            else:
                lines.append(current_line)
                current_line = []
                current_x = 0.0

        current_line.append((char, glyph))
        current_x += advance

    if current_line:
        lines.append(current_line)

    return lines


def _last_space(line: List[Tuple[str, Optional[Glyph]]]) -> Optional[int]:
    """Return the index of the last space in *line*, or ``None``."""
    for i in range(len(line) - 1, -1, -1):
        if line[i][0] == " ":
            return i
    return None
