"""Plot files for pen plotters: SVG, G-code and HPGL.

A plotter can only drag a pen along lines: it can't print a picture or fill a
shape. So plot files are built from each glyph's pen paths, never from its
image: the path the pen took for a letter drawn on the Draw page, or a line
traced through the middle of the ink for a scanned one (``workers/tracing.py``).

Each card is laid out with ``layout_text``, exactly as the preview and the
PDF are, so a given seed gives the same card on paper, on screen and on the
plotter. Every placed glyph's strokes are moved onto the card in millimetres,
cut at the card edge, and ordered so the pen travels as little as possible
with the pen up (a stroke is drawn backwards when its far end is closer).

Card coordinates are millimetres from the card's top-left corner, y down.
G-code and HPGL use the machine's y-up axes: the card's bottom-left corner
sits at ``offset_x``, ``offset_y`` with its top edge away from the origin.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

import numpy as np

from workers.renderer import _PX_PER_MM, PlacedGlyph, layout_text

Point = Tuple[float, float]
Path = List[Point]

FORMATS = ("svg", "gcode", "hpgl")
EXTENSIONS = {"svg": "svg", "gcode": "gcode", "hpgl": "plt"}
MEDIA_TYPES = {"svg": "image/svg+xml", "gcode": "text/plain", "hpgl": "application/vnd.hp-hpgl"}

HPGL_UNITS_PER_MM = 40

# A stroke that starts this close to where the last one ended is drawn without
# lifting the pen; the joining line is far thinner than the pen
JOIN_MM = 0.1

_DEFAULT_INK = "#1a1a2e"


@dataclass(frozen=True)
class MachineSettings:
    """How the plotter moves. Speeds are in mm per minute, as G-code uses them.

    The defaults are a guess for a 3D printer holding a fineliner. Time a real
    card on the machine and set the speeds to match.
    """

    pen_width_mm: float = 0.4      # line width in the SVG
    draw_speed: float = 2400.0     # pen down
    travel_speed: float = 6000.0   # pen up
    pen_up_z: float = 2.0          # G-code only
    pen_down_z: float = 0.0        # G-code only: Z where the pen just touches the card
    z_speed: float = 300.0         # G-code only; many printers limit Z to about 5 mm/s
    lift_time_s: float = 0.15      # one pen lift and drop on a plotter or cutter (SVG and HPGL estimates)
    offset_x: float = 0.0          # card's bottom-left corner on the machine, mm
    offset_y: float = 0.0


@dataclass
class CardPlot:
    """One page of a card as pen paths, in plotting order."""

    width_mm: float
    height_mm: float
    runs: List[Path]          # pen-down paths, card millimetres, y down
    strokes: int              # strokes before short gaps were joined
    unplottable: List[str]    # characters placed on the card that have no strokes
    traced: List[str]         # characters placed on the card whose strokes were traced from a scan
    drawn: List[str]          # characters placed on the card that were drawn on the Draw page
    clipped: int              # strokes cut at the card edge
    page: int
    page_count: int


@dataclass
class PlotStats:
    strokes: int
    pen_lifts: int
    draw_mm: float
    travel_mm: float
    seconds: float


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plottable(glyphs: Sequence[Any]) -> bool:
    """Whether any accepted glyph has strokes, so there is anything to plot."""
    return any(g.accepted and getattr(g, "strokes_json", None) for g in glyphs)


def plan_card(
    text: str,
    glyphs: Sequence[Any],
    settings: Dict[str, Any],
    profile_x_height: float,
    seed: int,
    page: int = 1,
) -> CardPlot:
    """Lay out *text* and turn page *page* (1-based) into ordered pen paths.

    Raises
    ------
    ValueError
        If *page* is past the last page.
    """
    pages = layout_text(text, list(glyphs), settings, profile_x_height, seed)
    if not 1 <= page <= len(pages):
        raise ValueError(f"Page {page} doesn't exist; this text has {len(pages)} page(s).")

    width = float(settings["page_width_mm"])
    height = float(settings["page_height_mm"])

    strokes: List[Path] = []
    unplottable: List[str] = []
    traced: List[str] = []
    drawn: List[str] = []
    clipped = 0
    cache: Dict[int, Optional[List[Path]]] = {}
    for placed in pages[page - 1]:
        g = placed.glyph
        if id(g) not in cache:
            cache[id(g)] = g.strokes() if getattr(g, "strokes_json", None) else None
        own = cache[id(g)]
        if own is None:
            if g.char not in unplottable:
                unplottable.append(g.char)
            continue
        source = getattr(g, "stroke_source", None)
        if source == "traced" and g.char not in traced:
            traced.append(g.char)
        elif source == "drawn" and g.char not in drawn:
            drawn.append(g.char)
        for stroke in own:
            on_card = [_to_card(placed, x, y) for x, y in stroke]
            pieces = clip_path(on_card, width, height)
            if len(pieces) != 1 or pieces[0] != on_card:
                clipped += 1
            strokes.extend(pieces)

    ordered = order_paths(strokes, start=(0.0, height))
    return CardPlot(
        width_mm=width,
        height_mm=height,
        runs=join_paths(ordered),
        strokes=len(strokes),
        unplottable=unplottable,
        traced=traced,
        drawn=drawn,
        clipped=clipped,
        page=page,
        page_count=len(pages),
    )


def _to_card(placed: PlacedGlyph, u: float, v: float) -> Point:
    """A point of a glyph's strokes (mask pixels) to card millimetres.

    Same geometry as ``renderToCanvas`` and the PDF painter: the mask is drawn
    ``width * scale`` wide with its bottom ``baseline_offset`` below the
    baseline, rotated about its centre.
    """
    g = placed.glyph
    draw_w = max(1.0, g.width * placed.scale)
    draw_h = max(1.0, g.height * placed.scale)
    cx = placed.x + draw_w / 2
    cy = placed.y - draw_h / 2 + g.baseline_offset * placed.scale
    lx = u / g.width * draw_w - draw_w / 2
    ly = v / g.height * draw_h - draw_h / 2
    cos, sin = math.cos(placed.rotation), math.sin(placed.rotation)
    x = cx + lx * cos - ly * sin
    y = cy + lx * sin + ly * cos
    return x / _PX_PER_MM, y / _PX_PER_MM


def clip_path(path: Path, width: float, height: float) -> List[Path]:
    """Cut *path* to the rectangle ``[0, width] x [0, height]``; parts outside are dropped."""
    def inside(p: Point) -> bool:
        return 0.0 <= p[0] <= width and 0.0 <= p[1] <= height

    if len(path) == 1:
        return [path] if inside(path[0]) else []
    if all(inside(p) for p in path):
        return [path]

    pieces: List[Path] = []
    current: Path = []
    for p, q in zip(path, path[1:]):
        segment = _clip_segment(p, q, width, height)
        if segment is None:
            if current:
                pieces.append(current)
                current = []
            continue
        a, b = segment
        if current and current[-1] == a:
            current.append(b)
        else:
            if current:
                pieces.append(current)
            current = [a, b]
    if current:
        pieces.append(current)
    return pieces


def _clip_segment(p: Point, q: Point, width: float, height: float) -> Optional[Tuple[Point, Point]]:
    """Liang-Barsky: the part of segment *p*-*q* inside the rectangle, or ``None``."""
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0), (dx, width - x0), (-dy, y0), (dy, height - y0)):
        if pk == 0:
            if qk < 0:
                return None
            continue
        t = qk / pk
        if pk < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 > t1:
        return None
    a = p if t0 == 0.0 else (x0 + t0 * dx, y0 + t0 * dy)
    b = q if t1 == 1.0 else (x0 + t1 * dx, y0 + t1 * dy)
    return a, b


def order_paths(paths: List[Path], start: Point) -> List[Path]:
    """Greedy nearest neighbour: from the pen's position, draw whichever path end is closest next.

    A path whose far end is the closer one is drawn backwards. Ties go to the
    earliest path, so the order is deterministic.
    """
    if not paths:
        return []
    starts = np.array([p[0] for p in paths], dtype=np.float64)
    ends = np.array([p[-1] for p in paths], dtype=np.float64)
    used = np.zeros(len(paths), dtype=bool)
    pos = np.array(start, dtype=np.float64)
    ordered: List[Path] = []
    for _ in range(len(paths)):
        d_start = np.sum((starts - pos) ** 2, axis=1)
        d_end = np.sum((ends - pos) ** 2, axis=1)
        d_start[used] = np.inf
        d_end[used] = np.inf
        i_start, i_end = int(np.argmin(d_start)), int(np.argmin(d_end))
        if d_end[i_end] < d_start[i_start]:
            used[i_end] = True
            path = paths[i_end][::-1]
        else:
            used[i_start] = True
            path = paths[i_start]
        ordered.append(path)
        pos = np.array(path[-1], dtype=np.float64)
    return ordered


def join_paths(paths: List[Path]) -> List[Path]:
    """Merge a path into the previous one when it starts within ``JOIN_MM`` of its end."""
    runs: List[Path] = []
    for path in paths:
        if runs and _dist(runs[-1][-1], path[0]) <= JOIN_MM:
            runs[-1].extend(path[1:] if runs[-1][-1] == path[0] else path)
        else:
            runs.append(list(path))
    return runs


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def path_length(path: Path) -> float:
    return sum(_dist(a, b) for a, b in zip(path, path[1:]))


def plot_stats(plot: CardPlot, machine: MachineSettings, fmt: str) -> PlotStats:
    """Drawing and travel distance, pen lifts and an estimated plot time.

    The pen starts and ends at the card's bottom-left corner. The estimate
    ignores acceleration, so a real machine takes somewhat longer.
    """
    draw = sum(path_length(run) for run in plot.runs)
    home = (0.0, plot.height_mm)
    travel, pos = 0.0, home
    for run in plot.runs:
        travel += _dist(pos, run[0])
        pos = run[-1]
    travel += _dist(pos, home)

    if fmt == "gcode":
        lift_s = 2 * abs(machine.pen_up_z - machine.pen_down_z) / machine.z_speed * 60
    else:
        lift_s = machine.lift_time_s
    lifts = len(plot.runs)
    seconds = draw / machine.draw_speed * 60 + travel / machine.travel_speed * 60 + lifts * lift_s
    return PlotStats(strokes=plot.strokes, pen_lifts=lifts, draw_mm=draw, travel_mm=travel, seconds=seconds)


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _num(value: float) -> str:
    """Two decimals (0.01 mm), never ``-0.00``."""
    text = f"{value:.2f}"
    return "0.00" if text == "-0.00" else text


def _dedupe(points: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Drop points that repeat the previous one once formatted."""
    out: List[Tuple[str, str]] = []
    for p in points:
        if not out or p != out[-1]:
            out.append(p)
    return out


def _summary_line(stats: PlotStats, fmt: str, machine: MachineSettings) -> str:
    return (
        f"{stats.strokes} strokes, {stats.pen_lifts} pen lifts, {stats.draw_mm:.0f} mm drawn, "
        f"{stats.travel_mm:.0f} mm of travel. Estimated plot time {format_duration(stats.seconds)} "
        f"at {machine.draw_speed:.0f} mm/min drawing and {machine.travel_speed:.0f} mm/min travel."
    )


def to_svg(plot: CardPlot, machine: MachineSettings, title: str, ink_color: str = _DEFAULT_INK) -> str:
    """Plain SVG in millimetres: one unfilled path per pen-down run, in plotting order."""
    ink = ink_color if re.fullmatch(r"#[0-9a-fA-F]{6}", ink_color or "") else _DEFAULT_INK
    w, h = _num(plot.width_mm), _num(plot.height_mm)
    stats = plot_stats(plot, machine, "svg")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">',
        f"  <title>{escape(title)}</title>",
        f"  <desc>{escape(_summary_line(stats, 'svg', machine))} Single-line pen paths; nothing is filled.</desc>",
        f'  <g fill="none" stroke="{ink}" stroke-width="{_num(machine.pen_width_mm)}" '
        'stroke-linecap="round" stroke-linejoin="round">',
    ]
    for run in plot.runs:
        pts = _dedupe([(_num(x), _num(y)) for x, y in run])
        if len(pts) == 1:
            pts = pts * 2  # a dot: zero-length line with round caps
        d = f"M{pts[0][0]} {pts[0][1]}L" + " ".join(f"{x} {y}" for x, y in pts[1:])
        lines.append(f'    <path d="{d}"/>')
    lines += ["  </g>", "</svg>"]
    return "\n".join(lines) + "\n"


def to_gcode(plot: CardPlot, machine: MachineSettings, title: str) -> str:
    """Marlin-style G-code for a 3D printer with a pen in place of the hot end.

    Units are millimetres with absolute positions. Z is never homed: the file
    takes the height the pen is at when it starts as ``pen_down_z`` (the pen
    just touching the card), lifts it, then homes X and Y only. There are no
    heater, fan or extruder commands, so the nozzle and bed stay cold.
    """
    stats = plot_stats(plot, machine, "gcode")
    up, down = _num(machine.pen_up_z), _num(machine.pen_down_z)
    f_draw, f_travel, f_z = (f"{v:.0f}" for v in (machine.draw_speed, machine.travel_speed, machine.z_speed))
    ox, oy = machine.offset_x, machine.offset_y

    def xy(p: Point) -> Tuple[str, str]:
        return _num(ox + p[0]), _num(oy + plot.height_mm - p[1])

    safe_title = re.sub(r"[\r\n;()]+", " ", title).strip()
    out = [
        f"; {safe_title}",
        f"; Card {_num(plot.width_mm)} x {_num(plot.height_mm)} mm. Its bottom-left corner sits at "
        f"X{_num(ox)} Y{_num(oy)}, top edge towards +Y.",
        f"; {_summary_line(stats, 'gcode', machine)}",
        "; For a 3D printer with a pen fixed beside the nozzle. No heater, fan or extruder commands:",
        "; the nozzle and bed stay cold.",
        f"; Before starting, lower the pen until it just touches the card. This file calls that height Z{down},",
        "; lifts the pen, and homes X and Y only, so the pen is never driven into the bed.",
        "G21 ; millimetres",
        "G90 ; absolute positions",
        f"G92 Z{down} ; the pen is touching the card",
        f"G0 Z{up} F{f_z} ; pen up before anything else moves",
        "G28 X Y ; home X and Y only",
    ]
    for run in plot.runs:
        pts = _dedupe([xy(p) for p in run])
        out.append(f"G0 X{pts[0][0]} Y{pts[0][1]} F{f_travel}")
        out.append(f"G1 Z{down} F{f_z}")
        for i, (x, y) in enumerate(pts[1:]):
            out.append(f"G1 X{x} Y{y} F{f_draw}" if i == 0 else f"G1 X{x} Y{y}")
        out.append(f"G0 Z{up} F{f_z}")
    out += [
        f"G0 X{_num(ox)} Y{_num(oy)} F{f_travel} ; back to the card's corner",
        "M84 X Y ; release X and Y so the card can be taken out",
        "; end",
    ]
    return "\n".join(out) + "\n"


def to_hpgl(plot: CardPlot, machine: MachineSettings) -> str:
    """HP-GL for plotters and vinyl cutters: 40 units per millimetre, y up.

    On a cutter, turn blade offset compensation off, or curves come out
    distorted.
    """
    ox, oy = machine.offset_x, machine.offset_y

    def xy(p: Point) -> Tuple[str, str]:
        return (str(int(round((ox + p[0]) * HPGL_UNITS_PER_MM))),
                str(int(round((oy + plot.height_mm - p[1]) * HPGL_UNITS_PER_MM))))

    velocity = max(1, int(round(machine.draw_speed / 600)))  # mm/min to cm/s
    out = ["IN;", "SP1;", f"VS{velocity};"]
    for run in plot.runs:
        pts = _dedupe([xy(p) for p in run])
        out.append(f"PU{pts[0][0]},{pts[0][1]};")
        if len(pts) == 1:
            out.append("PD;")
        else:
            out.append("PD" + ",".join(f"{x},{y}" for x, y in pts[1:]) + ";")
        out.append("PU;")
    home = xy((0.0, plot.height_mm))
    out += [f"PU{home[0]},{home[1]};", "SP0;"]
    return "\n".join(out) + "\n"


def write_plot(plot: CardPlot, fmt: str, machine: MachineSettings, title: str, ink_color: str = _DEFAULT_INK) -> bytes:
    """The plot file for *fmt* (``svg``, ``gcode`` or ``hpgl``) as bytes."""
    if fmt == "svg":
        text = to_svg(plot, machine, title, ink_color)
    elif fmt == "gcode":
        text = to_gcode(plot, machine, title)
    elif fmt == "hpgl":
        text = to_hpgl(plot, machine)
    else:
        raise ValueError(f"Unknown plot format {fmt!r}")
    return text.encode("utf-8")


def zip_files(files: List[Tuple[str, bytes]]) -> bytes:
    """Zip *files* with a fixed timestamp, so the same cards give the same zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()
