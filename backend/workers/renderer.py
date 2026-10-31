"""Handwriting page renderer using ReportLab.

Takes a RenderJob from the database, loads the associated Profile's accepted
glyphs, and composites them into a PDF page that mimics natural handwriting.

The layout is a line-for-line port of ``layoutText`` in
``frontend/src/lib/layout.ts``: same units, same settings, and the same order
of PRNG calls, so a given seed produces the same page in the live preview and
in the exported PDF. Change both files together.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from reportlab.lib.utils import ImageReader
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
        """Return a random integer in ``[lo, hi]`` inclusive (``randInt`` in prng.ts)."""
        return math.floor(lo + self.next_float() * (hi - lo + 1))

    def next_range(self, lo: float, hi: float) -> float:
        """Return a random float in ``[lo, hi)`` (``randRange`` in prng.ts)."""
        return lo + self.next_float() * (hi - lo)


# ---------------------------------------------------------------------------
# Page geometry and default settings (mirror layout.ts and the frontend store)
# ---------------------------------------------------------------------------

_PX_PER_MM = 96 / 25.4
_PT_TO_PX = 96 / 72
_PX_TO_PT = 72 / 96

# A4 at 96 dpi, rounded the way Math.round does
_PAGE_W_PX = math.floor(210 * _PX_PER_MM + 0.5)
_PAGE_H_PX = math.floor(297 * _PX_PER_MM + 0.5)

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "font_size": 12,          # pt, x-height of the written text
    "line_spacing": 2.8,      # multiple of font_size (the x-height)
    "ink_color": "#1a1a2e",
    "margin_top": 20,         # mm
    "margin_bottom": 20,
    "margin_left": 25,
    "margin_right": 20,
    "messiness": 40,          # 0..100
    "slant_correction": 0,    # degrees
}

_X_HEIGHT_CHARS = set("acemnorsuvwxz")


@dataclass
class PlacedGlyph:
    """One glyph on a page, in 96 dpi page pixels (same as ``PlacedGlyph`` in layout.ts)."""

    glyph: Glyph
    x: float
    y: float          # baseline y
    scale: float
    rotation: float   # radians, clockwise on the page


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
        db.rollback()
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
    # 2. Load Profile + Glyphs (same order as GET /profiles/{id}/glyphs,
    #    which the preview uses, so variant indices line up)
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=10, stage="Loading glyphs")
    profile: Optional[Profile] = db.query(Profile).filter(Profile.id == render_job.profile_id).first()
    if profile is None:
        raise ValueError(f"Profile {render_job.profile_id} not found.")

    glyphs: List[Glyph] = (
        db.query(Glyph)
        .filter(Glyph.profile_id == render_job.profile_id)
        .order_by(Glyph.char, Glyph.variant_index)
        .all()
    )

    # ------------------------------------------------------------------
    # 3. Parse settings (the frontend's RenderSettings shape)
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=15, stage="Parsing settings")
    try:
        user_settings: Dict[str, Any] = json.loads(render_job.settings_json or "{}")
    except json.JSONDecodeError:
        user_settings = {}
    settings = {**_DEFAULT_SETTINGS, **user_settings}

    try:
        metrics = json.loads(profile.metrics_json or "{}")
    except json.JSONDecodeError:
        metrics = {}
    profile_x_height = float(metrics.get("x_height") or 0)

    # ------------------------------------------------------------------
    # 4. Lay out the text exactly as the preview does
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=30, stage="Planning layout")
    pages = layout_text(render_job.text, glyphs, settings, profile_x_height, render_job.seed)

    # ------------------------------------------------------------------
    # 5. Render to PDF with ReportLab
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=50, stage="Rendering PDF")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"render_{render_id}.pdf"
    output_abs = EXPORT_DIR / output_filename
    output_rel = f"exports/{output_filename}"

    page_w_pt = _PAGE_W_PX * _PX_TO_PT
    page_h_pt = _PAGE_H_PX * _PX_TO_PT
    ink_rgb = _parse_hex_color(str(settings.get("ink_color", "")))
    tinted: Dict[int, Optional[ImageReader]] = {}

    # invariant=1 drops the timestamp and random document ID, so the same
    # text, profile and seed always produce a byte-identical PDF
    c = rl_canvas.Canvas(str(output_abs), pagesize=(page_w_pt, page_h_pt), invariant=1)
    for page in pages:
        for placed in page:
            g = placed.glyph
            if g.id not in tinted:
                tinted[g.id] = _tinted_mask(g, ink_rgb)
            image = tinted[g.id]
            if image is None:
                continue

            # Same geometry as renderToCanvas: bottom of the mask sits
            # baseline_offset below the baseline, rotation about the centre
            draw_w = max(1.0, g.width * placed.scale)
            draw_h = max(1.0, g.height * placed.scale)
            center_y = placed.y - draw_h / 2 + g.baseline_offset * placed.scale

            c.saveState()
            # Page pixels are y-down; PDF points are y-up
            c.translate((placed.x + draw_w / 2) * _PX_TO_PT, page_h_pt - center_y * _PX_TO_PT)
            c.rotate(-math.degrees(placed.rotation))
            w_pt, h_pt = draw_w * _PX_TO_PT, draw_h * _PX_TO_PT
            c.drawImage(image, -w_pt / 2, -h_pt / 2, width=w_pt, height=h_pt, mask="auto")
            c.restoreState()
        c.showPage()

    c.save()

    # ------------------------------------------------------------------
    # 6. Update DB record
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=95, stage="Saving result")
    render_job.output_path = output_rel
    render_job.status = "done"
    db.commit()

    job_store.update_job(job_id, status="done", progress=100, stage="Complete")
    logger.info("Render %d complete: %d page(s), output %s", render_id, len(pages), output_rel)


# ---------------------------------------------------------------------------
# Layout (port of frontend/src/lib/layout.ts)
# ---------------------------------------------------------------------------


def estimate_x_height(glyphs: List[Glyph], fallback: float) -> float:
    """Median height of the short lowercase letters (``estimateXHeight`` in layout.ts)."""
    heights = sorted(g.height for g in glyphs if g.accepted and g.char in _X_HEIGHT_CHARS)
    if not heights:
        return fallback if fallback > 0 else 32.0
    mid = len(heights) // 2
    return heights[mid] if len(heights) % 2 else (heights[mid - 1] + heights[mid]) / 2


def layout_text(
    text: str,
    glyphs: List[Glyph],
    settings: Dict[str, Any],
    profile_x_height: float,
    seed: int,
) -> List[List[PlacedGlyph]]:
    """Lay out *text* into pages of placed glyphs (``layoutText`` in layout.ts)."""
    rng = Mulberry32(seed)

    glyph_map: Dict[str, List[Glyph]] = {}
    for g in glyphs:
        if g.accepted:
            glyph_map.setdefault(g.char, []).append(g)

    font_size_px = float(settings["font_size"]) * _PT_TO_PX
    x_height_ref = estimate_x_height(glyphs, profile_x_height)
    base_scale = font_size_px / x_height_ref

    m_top = float(settings["margin_top"]) * _PX_PER_MM
    m_bottom = float(settings["margin_bottom"]) * _PX_PER_MM
    m_left = float(settings["margin_left"]) * _PX_PER_MM
    m_right = float(settings["margin_right"]) * _PX_PER_MM

    content_width = _PAGE_W_PX - m_left - m_right
    content_height = _PAGE_H_PX - m_top - m_bottom
    line_height = font_size_px * float(settings["line_spacing"])
    mess = float(settings["messiness"]) / 100
    slant_rad = float(settings["slant_correction"]) * math.pi / 180

    pages: List[List[PlacedGlyph]] = [[]]
    cur_x = m_left
    cur_y = m_top + font_size_px
    word: List[PlacedGlyph] = []
    word_width = 0.0

    def new_line() -> None:
        nonlocal cur_x, cur_y
        cur_x = m_left
        cur_y += line_height
        if cur_y > m_top + content_height:
            pages.append([])
            cur_y = m_top + font_size_px

    def flush_word() -> None:
        nonlocal cur_x, word, word_width
        if not word:
            return
        if cur_x + word_width > m_left + content_width and cur_x > m_left:
            new_line()
        for placed in word:
            placed.x += cur_x
            placed.y += cur_y
            pages[-1].append(placed)
        cur_x += word_width
        word = []
        word_width = 0.0

    for ch in text:
        if ch == "\n":
            flush_word()
            new_line()
            continue

        if ch == " ":
            flush_word()
            cur_x += base_scale * x_height_ref * 0.45 * (1 + rng.next_range(-0.1, 0.1) * mess)
            continue

        variants = glyph_map.get(ch) or glyph_map.get(ch.lower()) or []
        if not variants:
            word_width += base_scale * x_height_ref * 0.5
            continue

        variant = variants[rng.next_int(0, len(variants) - 1)]
        scale = base_scale * (1 + rng.next_range(-0.03, 0.03) * mess)
        rotation = slant_rad + rng.next_range(-0.035, 0.035) * mess
        baseline_wobble = rng.next_range(-1, 1) * mess

        word.append(PlacedGlyph(
            glyph=variant,
            x=word_width + variant.bearing_left * scale,
            y=baseline_wobble,
            scale=scale,
            rotation=rotation,
        ))
        word_width += (variant.bearing_left + variant.width + variant.bearing_right) * scale

    flush_word()
    return pages


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _parse_hex_color(value: str) -> Tuple[int, int, int]:
    """Parse ``#rrggbb`` into an RGB tuple, falling back to the default ink."""
    value = value.strip().lstrip("#")
    if len(value) == 6:
        try:
            return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        except ValueError:
            pass
    return 0x1A, 0x1A, 0x2E


def _tinted_mask(glyph: Glyph, rgb: Tuple[int, int, int]) -> Optional[ImageReader]:
    """Load a glyph mask PNG and recolour its ink, keeping the alpha channel."""
    path = get_file_path(glyph.mask_path)
    if not path.exists():
        logger.warning("Glyph mask missing on disk: %s", path)
        return None
    with Image.open(path) as im:
        alpha = im.convert("RGBA").getchannel("A")
    solid = Image.new("RGBA", alpha.size, rgb + (255,))
    solid.putalpha(alpha)
    return ImageReader(solid)
