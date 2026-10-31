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
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Preset page sizes in mm (``PAGE_PRESETS`` in layout.ts), used in messages
_PAGE_LABELS: Dict[str, str] = {
    "A6": "A6 card",
    "A7": "A7 card",
    "DL": "DL envelope",
    "A4": "A4 page",
    "custom": "custom-size card",
}

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "page_size": "A6",
    "page_width_mm": 105,
    "page_height_mm": 148,
    "align": "left",          # "left" or "center"
    "font_size": 10,          # pt, x-height of the written text
    "line_spacing": 2.8,      # multiple of font_size (the x-height)
    "ink_color": "#1a1a2e",
    "margin_top": 12,         # mm
    "margin_bottom": 12,
    "margin_left": 12,
    "margin_right": 12,
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


@dataclass
class _WordChar:
    """One character of the word being built: its glyph (``None`` if it has none) and advance."""

    placed: Optional[PlacedGlyph]
    advance: float


# ---------------------------------------------------------------------------
# Public entry points
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
    _run_guarded(job_id, render_id, db_session_factory, RenderJob, _run)


def _run_guarded(
    job_id: str,
    record_id: int,
    db_session_factory,
    model: Any,
    pipeline: Callable[[str, int, Any], None],
) -> None:
    """Run *pipeline* and record any failure on the job and the DB record."""
    db = db_session_factory()
    try:
        pipeline(job_id, record_id, db)
    except Exception as exc:
        logger.exception("Renderer failed for job %s / %s %d", job_id, model.__tablename__, record_id)
        job_store.update_job(job_id, status="error", error=str(exc))
        # Persist error status to DB
        db.rollback()
        record = db.query(model).filter(model.id == record_id).first()
        if record is not None:
            record.status = "error"
            record.error = str(exc)
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal pipelines
# ---------------------------------------------------------------------------


def _run(job_id: str, render_id: int, db: Any) -> None:
    """Internal render pipeline for one text."""

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
    # 2. Load glyphs and settings
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=10, stage="Loading glyphs")
    glyphs, settings, profile_x_height = _load_inputs(db, render_job.profile_id, render_job.settings_json)

    # ------------------------------------------------------------------
    # 3. Lay out the text exactly as the preview does, and refuse a card
    #    that would run onto a second page
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=30, stage="Planning layout")
    pages = layout_text(render_job.text, glyphs, settings, profile_x_height, render_job.seed)
    render_job.missing_chars = json.dumps(missing_chars(render_job.text, glyphs))
    db.commit()
    if is_single_page(settings) and len(pages) > 1:
        raise ValueError(_doesnt_fit_message(settings, "The text"))

    # ------------------------------------------------------------------
    # 4. Render to PDF with ReportLab
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=50, stage="Rendering PDF")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"render_{render_id}.pdf"
    painter = _PdfPainter(settings)
    c = painter.canvas(str(EXPORT_DIR / output_filename))
    painter.draw(c, pages)
    c.save()

    # ------------------------------------------------------------------
    # 5. Update DB record
    # ------------------------------------------------------------------
    job_store.update_job(job_id, progress=95, stage="Saving result")
    render_job.output_path = f"exports/{output_filename}"
    render_job.status = "done"
    db.commit()

    job_store.update_job(job_id, status="done", progress=100, stage="Complete")
    logger.info("Render %d complete: %d page(s), output %s", render_id, len(pages), render_job.output_path)


def _load_inputs(db: Any, profile_id: int, settings_json: Optional[str]) -> Tuple[List[Glyph], Dict[str, Any], float]:
    """Load a profile's glyphs and x-height, and parse the render settings.

    Glyphs come in the same order as GET /profiles/{id}/glyphs, which the
    preview uses, so variant indices line up.
    """
    profile: Optional[Profile] = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found.")

    glyphs: List[Glyph] = (
        db.query(Glyph)
        .filter(Glyph.profile_id == profile_id)
        .order_by(Glyph.char, Glyph.variant_index)
        .all()
    )

    # Settings arrive in the frontend's RenderSettings shape
    try:
        user_settings: Dict[str, Any] = json.loads(settings_json or "{}")
    except json.JSONDecodeError:
        user_settings = {}
    settings = {**_DEFAULT_SETTINGS, **user_settings}

    try:
        metrics = json.loads(profile.metrics_json or "{}")
    except json.JSONDecodeError:
        metrics = {}
    return glyphs, settings, float(metrics.get("x_height") or 0)


def _doesnt_fit_message(settings: Dict[str, Any], subject: str) -> str:
    """Explain that *subject* runs past one card, and how to fix it."""
    label = _PAGE_LABELS.get(str(settings.get("page_size")), "card")
    return (
        f"{subject} doesn't fit on one {label}. Shorten the text, lower the "
        "font size, line spacing or margins, or pick a bigger size."
    )


# ---------------------------------------------------------------------------
# Layout (port of frontend/src/lib/layout.ts)
# ---------------------------------------------------------------------------


def page_size_px(settings: Dict[str, Any]) -> Tuple[int, int]:
    """Page size in 96 dpi pixels, rounded the way Math.round does (``pageSizePx``)."""
    return (
        math.floor(float(settings["page_width_mm"]) * _PX_PER_MM + 0.5),
        math.floor(float(settings["page_height_mm"]) * _PX_PER_MM + 0.5),
    )


def is_single_page(settings: Dict[str, Any]) -> bool:
    """Cards and envelopes must fit on one page; only A4 may run onto more (``isSinglePage``)."""
    return settings.get("page_size") != "A4"


def missing_chars(text: str, glyphs: List[Glyph]) -> List[str]:
    """Characters in *text* with no accepted glyph, in order of first use (``missingChars``)."""
    have = {g.char for g in glyphs if g.accepted}
    missing: List[str] = []
    for ch in text:
        if ch in (" ", "\n"):
            continue
        if ch in have or ch.lower() in have:
            continue
        if ch not in missing:
            missing.append(ch)
    return missing


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

    page_w_px, page_h_px = page_size_px(settings)

    m_top = float(settings["margin_top"]) * _PX_PER_MM
    m_bottom = float(settings["margin_bottom"]) * _PX_PER_MM
    m_left = float(settings["margin_left"]) * _PX_PER_MM
    m_right = float(settings["margin_right"]) * _PX_PER_MM

    content_width = page_w_px - m_left - m_right
    content_height = page_h_px - m_top - m_bottom
    max_x = m_left + content_width
    line_height = font_size_px * float(settings["line_spacing"])
    mess = float(settings["messiness"]) / 100
    slant_rad = float(settings["slant_correction"]) * math.pi / 180
    center = settings.get("align") == "center"

    pages: List[List[PlacedGlyph]] = [[]]
    cur_x = m_left
    cur_y = m_top + font_size_px

    # Glyphs on the current line, and the right edge of its last word
    line: List[PlacedGlyph] = []
    line_end = m_left

    word: List[_WordChar] = []
    word_width = 0.0

    def end_line() -> None:
        nonlocal line, line_end
        if center:
            shift = (content_width - (line_end - m_left)) / 2
            for placed in line:
                placed.x += shift
        line = []
        line_end = m_left

    def new_line() -> None:
        nonlocal cur_x, cur_y
        end_line()
        cur_x = m_left
        cur_y += line_height
        if cur_y > m_top + content_height:
            pages.append([])
            cur_y = m_top + font_size_px

    def place(wc: _WordChar) -> None:
        nonlocal cur_x, line_end
        if wc.placed is not None:
            wc.placed.x += cur_x
            wc.placed.y += cur_y
            pages[-1].append(wc.placed)
            line.append(wc.placed)
        cur_x += wc.advance
        line_end = cur_x

    def flush_word() -> None:
        nonlocal word, word_width
        if not word:
            return
        # Wrap if the word doesn't fit on the current line
        if cur_x + word_width > max_x and cur_x > m_left:
            new_line()
        # A word wider than a whole line breaks between characters
        too_wide = cur_x + word_width > max_x
        for wc in word:
            if too_wide and cur_x + wc.advance > max_x and cur_x > m_left:
                new_line()
            place(wc)
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
            # No glyph: leave a gap (missing_chars lists these before export)
            advance = base_scale * x_height_ref * 0.5
            word.append(_WordChar(placed=None, advance=advance))
            word_width += advance
            continue

        variant = variants[rng.next_int(0, len(variants) - 1)]
        scale = base_scale * (1 + rng.next_range(-0.03, 0.03) * mess)
        rotation = slant_rad + rng.next_range(-0.035, 0.035) * mess
        baseline_wobble = rng.next_range(-1, 1) * mess
        advance = (variant.bearing_left + variant.width + variant.bearing_right) * scale

        word.append(_WordChar(
            placed=PlacedGlyph(
                glyph=variant,
                x=variant.bearing_left * scale,
                y=baseline_wobble,
                scale=scale,
                rotation=rotation,
            ),
            advance=advance,
        ))
        word_width += advance

    flush_word()
    end_line()

    # Trailing newlines can leave empty pages at the end; drop them
    while len(pages) > 1 and not pages[-1]:
        pages.pop()
    return pages


# ---------------------------------------------------------------------------
# PDF drawing
# ---------------------------------------------------------------------------


class _PdfPainter:
    """Draws laid-out pages onto ReportLab canvases, caching the tinted glyph masks."""

    def __init__(self, settings: Dict[str, Any]) -> None:
        page_w_px, page_h_px = page_size_px(settings)
        self.page_w_pt = page_w_px * _PX_TO_PT
        self.page_h_pt = page_h_px * _PX_TO_PT
        self._ink_rgb = _parse_hex_color(str(settings.get("ink_color", "")))
        self._tinted: Dict[int, Optional[ImageReader]] = {}

    def canvas(self, target: Any) -> rl_canvas.Canvas:
        """A canvas of the page size, writing to a path or a file-like object."""
        # invariant=1 drops the timestamp and random document ID, so the same
        # text, profile and seed always produce a byte-identical PDF
        return rl_canvas.Canvas(target, pagesize=(self.page_w_pt, self.page_h_pt), invariant=1)

    def draw(self, c: rl_canvas.Canvas, pages: List[List[PlacedGlyph]]) -> None:
        """Draw *pages* onto *c*, one PDF page each. Ink only: no paper background."""
        for page in pages:
            for placed in page:
                g = placed.glyph
                if g.id not in self._tinted:
                    self._tinted[g.id] = _tinted_mask(g, self._ink_rgb)
                image = self._tinted[g.id]
                if image is None:
                    continue

                # Same geometry as renderToCanvas: bottom of the mask sits
                # baseline_offset below the baseline, rotation about the centre
                draw_w = max(1.0, g.width * placed.scale)
                draw_h = max(1.0, g.height * placed.scale)
                center_y = placed.y - draw_h / 2 + g.baseline_offset * placed.scale

                c.saveState()
                # Page pixels are y-down; PDF points are y-up
                c.translate((placed.x + draw_w / 2) * _PX_TO_PT, self.page_h_pt - center_y * _PX_TO_PT)
                c.rotate(-math.degrees(placed.rotation))
                w_pt, h_pt = draw_w * _PX_TO_PT, draw_h * _PX_TO_PT
                c.drawImage(image, -w_pt / 2, -h_pt / 2, width=w_pt, height=h_pt, mask="auto")
                c.restoreState()
            c.showPage()


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
