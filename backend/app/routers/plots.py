"""Router: plot files for pen plotters.

GET /api/renders/{id}/plot                  summary: strokes, pen lifts, distances, estimated time
GET /api/renders/{id}/plot/file             the SVG, G-code or HPGL file
GET /api/render-batches/{id}/plot           summary per card and for the whole set
GET /api/render-batches/{id}/plot/file      one card's file (``card`` is 1-based)
GET /api/render-batches/{id}/plot/zip       a zip with one file per card

Files are generated on request from the render's text, settings and seed, so
nothing is stored and the machine settings can change freely. Machine
settings are query parameters (see ``PlotQuery``).
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Tuple

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.render_batch import RenderBatch
from app.models.render_job import RenderJob
from workers.plotter import (
    EXTENSIONS,
    MEDIA_TYPES,
    CardPlot,
    MachineSettings,
    plan_card,
    plot_stats,
    plottable,
    write_plot,
    zip_files,
)
from workers.renderer import _card_filename, _doesnt_fit_message, _load_inputs, is_single_page, missing_chars
from workers.tracing import trace_missing

router = APIRouter(tags=["plots"])

_NOTHING_TO_PLOT = (
    "This profile has no letters a plotter can follow yet. Upload a scanned sheet or draw the "
    "letters on the Draw page."
)


class PlotQuery(BaseModel):
    """Plot format, page and machine settings, as query parameters."""

    format: Literal["svg", "gcode", "hpgl"] = "svg"
    page: int = Field(1, ge=1, description="Page to plot, for an A4 text that runs onto more pages.")
    pen_width_mm: float = Field(0.4, gt=0, le=5, description="Line width in the SVG.")
    draw_speed: float = Field(2400, gt=0, le=60000, description="Pen-down speed, mm/min.")
    travel_speed: float = Field(6000, gt=0, le=60000, description="Pen-up speed, mm/min.")
    pen_up_z: float = Field(2.0, ge=-50, le=200, description="G-code: Z with the pen lifted.")
    pen_down_z: float = Field(0.0, ge=-50, le=200, description="G-code: Z with the pen touching the card.")
    z_speed: float = Field(300, gt=0, le=60000, description="G-code: Z speed, mm/min.")
    lift_time_s: float = Field(0.15, ge=0, le=10, description="SVG and HPGL estimate: seconds per pen lift and drop.")
    offset_x: float = Field(0, ge=0, le=5000, description="Card's bottom-left corner on the machine, mm.")
    offset_y: float = Field(0, ge=0, le=5000)

    @model_validator(mode="after")
    def _pen_up_above_down(self) -> "PlotQuery":
        if self.pen_up_z <= self.pen_down_z:
            raise ValueError("Pen up Z must be higher than pen down Z.")
        return self

    def machine(self) -> MachineSettings:
        return MachineSettings(
            pen_width_mm=self.pen_width_mm,
            draw_speed=self.draw_speed,
            travel_speed=self.travel_speed,
            pen_up_z=self.pen_up_z,
            pen_down_z=self.pen_down_z,
            z_speed=self.z_speed,
            lift_time_s=self.lift_time_s,
            offset_x=self.offset_x,
            offset_y=self.offset_y,
        )


PlotParams = Annotated[PlotQuery, Query()]


# ---------------------------------------------------------------------------
# Single render
# ---------------------------------------------------------------------------


@router.get("/renders/{render_id}/plot", summary="Plot summary for a render")
def render_plot_summary(render_id: int, q: PlotParams, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Strokes, pen lifts, distances and estimated plot time for one page of a render."""
    render, plot, settings, glyphs = _render_plot(render_id, q, db)
    return _summary(plot, q, missing_chars(render.text, glyphs))


@router.get("/renders/{render_id}/plot/file", summary="Download a render as a plot file")
def render_plot_file(render_id: int, q: PlotParams, db: Session = Depends(get_db)) -> Response:
    """The SVG, G-code or HPGL file for one page of a render."""
    render, plot, settings, _ = _render_plot(render_id, q, db)
    suffix = f"_page{q.page}" if plot.page_count > 1 else ""
    # The title names the seed, not the render, so the same card always gives the same file
    title = f"NeverWrite card, seed {render.seed & 0xFFFFFFFF:08x}" + (f", page {q.page}" if plot.page_count > 1 else "")
    data = write_plot(plot, q.format, q.machine(), title, str(settings.get("ink_color", "")))
    return _file_response(data, q.format, f"neverwrite_card_{render_id}{suffix}.{EXTENSIONS[q.format]}")


def _render_plot(render_id: int, q: PlotQuery, db: Session) -> Tuple[RenderJob, CardPlot, Dict[str, Any], List[Any]]:
    render = db.query(RenderJob).filter(RenderJob.id == render_id).first()
    if render is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"RenderJob {render_id} not found.")
    glyphs, settings, x_height = _plot_inputs(db, render.profile_id, render.settings_json)
    plot = _plan(render.text, glyphs, settings, x_height, render.seed, q.page)
    if is_single_page(settings) and plot.page_count > 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_doesnt_fit_message(settings, "The text"))
    return render, plot, settings, glyphs


# ---------------------------------------------------------------------------
# Mail-merge batch
# ---------------------------------------------------------------------------


@router.get("/render-batches/{batch_id}/plot", summary="Plot summary for every card of a batch")
def batch_plot_summary(batch_id: int, q: PlotParams, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Each card's strokes, pen lifts and estimated time, plus the total for the set."""
    _, cards, plots, _, glyphs = _batch_plots(batch_id, q, db)
    summaries = []
    for card, plot in zip(cards, plots):
        summary = _summary(plot, q, missing_chars(card["text"], glyphs))
        summary["label"] = str(card.get("label", ""))
        summaries.append(summary)
    return {
        "format": q.format,
        "cards": summaries,
        "total_seconds": round(sum(s["seconds"] for s in summaries), 1),
        "total_draw_mm": round(sum(s["draw_mm"] for s in summaries), 1),
        "total_pen_lifts": sum(s["pen_lifts"] for s in summaries),
    }


@router.get("/render-batches/{batch_id}/plot/file", summary="Download one card of a batch as a plot file")
def batch_plot_file(
    batch_id: int,
    q: PlotParams,
    card: Annotated[int, Query(ge=1, description="Card number, from 1.")] = 1,
    db: Session = Depends(get_db),
) -> Response:
    """The SVG, G-code or HPGL file for one card of a batch."""
    _, cards, plots, settings, _ = _batch_plots(batch_id, q, db)
    if card > len(cards):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"This set has {len(cards)} card(s).")
    label = str(cards[card - 1].get("label", ""))
    data = write_plot(plots[card - 1], q.format, q.machine(), _card_title(batch_id, card, label),
                      str(settings.get("ink_color", "")))
    return _file_response(data, q.format, _card_filename(card, label, EXTENSIONS[q.format]))


@router.get("/render-batches/{batch_id}/plot/zip", summary="Download every card of a batch as plot files")
def batch_plot_zip(batch_id: int, q: PlotParams, db: Session = Depends(get_db)) -> Response:
    """A zip with one SVG, G-code or HPGL file per card."""
    _, cards, plots, settings, _ = _batch_plots(batch_id, q, db)
    ink = str(settings.get("ink_color", ""))
    files = []
    for number, (card, plot) in enumerate(zip(cards, plots), start=1):
        label = str(card.get("label", ""))
        files.append((_card_filename(number, label, EXTENSIONS[q.format]),
                      write_plot(plot, q.format, q.machine(), _card_title(batch_id, number, label), ink)))
    return Response(
        content=zip_files(files),
        media_type="application/zip",
        headers={"Content-Disposition": _disposition(f"neverwrite_cards_{batch_id}_{q.format}.zip")},
    )


def _batch_plots(batch_id: int, q: PlotQuery, db: Session):
    batch = db.query(RenderBatch).filter(RenderBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"RenderBatch {batch_id} not found.")
    glyphs, settings, x_height = _plot_inputs(db, batch.profile_id, batch.settings_json)
    cards: List[Dict[str, Any]] = json.loads(batch.cards_json or "[]")
    if not cards:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This set has no cards.")

    plots: List[CardPlot] = []
    too_long: List[int] = []
    for number, card in enumerate(cards, start=1):
        plot = _plan(card["text"], glyphs, settings, x_height, int(card["seed"]), 1)
        if is_single_page(settings) and plot.page_count > 1:
            too_long.append(number)
        plots.append(plot)
    if too_long:
        which = "Card " if len(too_long) == 1 else "Cards "
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=_doesnt_fit_message(settings, which + ", ".join(map(str, too_long))))
    return batch, cards, plots, settings, glyphs


def _card_title(batch_id: int, number: int, label: str) -> str:
    return f"NeverWrite card {number} of set {batch_id}" + (f": {label}" if label else "")


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _plot_inputs(db: Session, profile_id: int, settings_json: str):
    try:
        glyphs, settings, x_height = _load_inputs(db, profile_id, settings_json)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # Scans from before tracing existed get their pen paths now, once
    trace_missing(glyphs, db)
    if not plottable(glyphs):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOTHING_TO_PLOT)
    return glyphs, settings, x_height


def _plan(text: str, glyphs: List[Any], settings: Dict[str, Any], x_height: float, seed: int, page: int) -> CardPlot:
    try:
        return plan_card(text, glyphs, settings, x_height, seed, page)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _summary(plot: CardPlot, q: PlotQuery, missing: List[str]) -> Dict[str, Any]:
    stats = plot_stats(plot, q.machine(), q.format)
    return {
        "format": q.format,
        "page": plot.page,
        "page_count": plot.page_count,
        "width_mm": plot.width_mm,
        "height_mm": plot.height_mm,
        "strokes": stats.strokes,
        "pen_lifts": stats.pen_lifts,
        "draw_mm": round(stats.draw_mm, 1),
        "travel_mm": round(stats.travel_mm, 1),
        "seconds": round(stats.seconds, 1),
        "unplottable_chars": plot.unplottable,
        "traced_chars": plot.traced,
        "drawn_chars": plot.drawn,
        "missing_chars": missing,
        "clipped_strokes": plot.clipped,
    }


def _file_response(data: bytes, fmt: str, filename: str) -> Response:
    return Response(content=data, media_type=MEDIA_TYPES[fmt], headers={"Content-Disposition": _disposition(filename)})


def _disposition(filename: str) -> str:
    """An attachment header that survives non-ASCII names (CSV labels can hold any name)."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
