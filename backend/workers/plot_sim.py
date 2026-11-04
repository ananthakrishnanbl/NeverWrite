"""Replay a G-code or HPGL plot file and draw what the machine would put on paper.

This is how to check a plot file without a plotter: every pen-down move
becomes a line on an image, and anything a pen plotter must never see (a
heater or extruder command, a move before the pen is lifted) is reported.

From backend/::

    python -m workers.plot_sim card.gcode card.png
    python -m workers.plot_sim card.plt card.png --travel

Only the commands NeverWrite writes are understood; anything else is
reported as a problem rather than guessed at.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

Point = Tuple[float, float]
Segment = Tuple[Point, Point]

_FORBIDDEN_M = {"M104", "M109", "M140", "M190", "M106", "M107", "M141", "M191"}
_PEN_TOLERANCE = 1e-6


@dataclass
class Simulation:
    """What a plot file makes the machine do, in machine millimetres (y up)."""

    drawn: List[Segment] = field(default_factory=list)
    dots: List[Point] = field(default_factory=list)
    travel: List[Segment] = field(default_factory=list)
    pen_downs: int = 0
    pen_up_at_start: bool = False
    pen_up_at_end: bool = False
    problems: List[str] = field(default_factory=list)

    @property
    def draw_mm(self) -> float:
        return sum(math.dist(a, b) for a, b in self.drawn)

    @property
    def travel_mm(self) -> float:
        return sum(math.dist(a, b) for a, b in self.travel)

    def drawn_points(self) -> List[Point]:
        return [p for seg in self.drawn for p in seg] + list(self.dots)


def simulate_gcode(text: str) -> Simulation:
    """Replay G-code written by ``workers.plotter.to_gcode``.

    The pen counts as down at or below the Z that ``G92`` declared (the
    height where the pen touches the card).
    """
    sim = Simulation()
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    down_z: Optional[float] = None
    absolute = True
    moved_since_down = False
    first_xy_seen = False

    def pen_down() -> bool:
        return z is not None and down_z is not None and z <= down_z + _PEN_TOLERANCE

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip().upper()
        if not line:
            continue
        words = dict((w[0], w[1:]) for w in line.split())
        code = line.split()[0]
        if "E" in words:
            sim.problems.append(f"line {number}: extruder move {raw.strip()!r}")
        if code in _FORBIDDEN_M:
            sim.problems.append(f"line {number}: heater or fan command {raw.strip()!r}")
            continue
        if code in ("G21", "M84"):
            continue
        if code == "G90":
            absolute = True
            continue
        if code == "G91":
            absolute = False
            sim.problems.append(f"line {number}: relative positioning is not expected")
            continue
        if code == "G92":
            if "Z" in words:
                z = down_z = float(words["Z"])
            continue
        if code == "G28":
            if "Z" in words or len(words) == 1:
                sim.problems.append(f"line {number}: homes Z, which can drive the pen into the bed")
            if "X" in words or len(words) == 1:
                x = 0.0
            if "Y" in words or len(words) == 1:
                y = 0.0
            continue
        if code not in ("G0", "G1"):
            sim.problems.append(f"line {number}: unexpected command {raw.strip()!r}")
            continue
        if not absolute:
            continue

        if "Z" in words:
            was_down = pen_down()
            z = float(words["Z"])
            if pen_down() and not was_down:
                sim.pen_downs += 1
                moved_since_down = False
            elif was_down and not pen_down() and not moved_since_down and x is not None and y is not None:
                sim.dots.append((x, y))
        if "X" in words or "Y" in words:
            nx = float(words["X"]) if "X" in words else x
            ny = float(words["Y"]) if "Y" in words else y
            if nx is None or ny is None:
                sim.problems.append(f"line {number}: move from an unknown position")
                x, y = nx, ny
                continue
            if not first_xy_seen:
                first_xy_seen = True
                sim.pen_up_at_start = z is not None and not pen_down()
            if x is not None and y is not None and (nx, ny) != (x, y):
                if pen_down():
                    if code == "G0":
                        sim.problems.append(f"line {number}: rapid move with the pen down")
                    sim.drawn.append(((x, y), (nx, ny)))
                    moved_since_down = True
                else:
                    sim.travel.append(((x, y), (nx, ny)))
            x, y = nx, ny

    if not first_xy_seen:
        sim.pen_up_at_start = True
    sim.pen_up_at_end = not pen_down()
    return sim


_HPGL_COMMAND = re.compile(r"([A-Z]{2})([^;A-Z]*)")


def simulate_hpgl(text: str, units_per_mm: float = 40.0) -> Simulation:
    """Replay HP-GL written by ``workers.plotter.to_hpgl``."""
    sim = Simulation(pen_up_at_start=True)
    x, y = 0.0, 0.0
    down = False
    moved_since_down = False
    first_command = True
    for command, args in _HPGL_COMMAND.findall(text.upper()):
        values = [float(v) / units_per_mm for v in re.split(r"[,\s]+", args.strip()) if v]
        if command in ("IN", "SP", "VS"):
            continue
        if command not in ("PU", "PD"):
            sim.problems.append(f"unexpected command {command}")
            continue
        if first_command and command == "PD":
            sim.pen_up_at_start = False
        first_command = False
        lowering = command == "PD" and not down
        lifting = command == "PU" and down
        if lifting and not moved_since_down:
            sim.dots.append((x, y))
        down = command == "PD"
        if lowering:
            sim.pen_downs += 1
            moved_since_down = False
        for nx, ny in zip(values[0::2], values[1::2]):
            if (nx, ny) != (x, y):
                (sim.drawn if down else sim.travel).append(((x, y), (nx, ny)))
                if down:
                    moved_since_down = True
            x, y = nx, ny
    sim.pen_up_at_end = not down
    return sim


def render(sim: Simulation, pen_mm: float = 0.4, px_per_mm: float = 10.0, show_travel: bool = False,
           bounds: Optional[Tuple[float, float, float, float]] = None) -> np.ndarray:
    """Draw the simulation as a white image, machine y up. *bounds* is ``(x0, y0, x1, y1)`` in mm."""
    points = sim.drawn_points() + ([p for seg in sim.travel for p in seg] if show_travel else [])
    if bounds is None:
        if not points:
            bounds = (0.0, 0.0, 10.0, 10.0)
        else:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            bounds = (min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2)
    x0, y0, x1, y1 = bounds
    w = max(1, int(math.ceil((x1 - x0) * px_per_mm)))
    h = max(1, int(math.ceil((y1 - y0) * px_per_mm)))
    image = np.full((h, w, 3), 255, np.uint8)

    def to_px(p: Point) -> Tuple[int, int]:
        return int(round((p[0] - x0) * px_per_mm)), int(round((y1 - p[1]) * px_per_mm))

    if show_travel:
        for a, b in sim.travel:
            cv2.line(image, to_px(a), to_px(b), (200, 200, 255), 1, cv2.LINE_AA)
    thickness = max(1, int(round(pen_mm * px_per_mm)))
    for a, b in sim.drawn:
        cv2.line(image, to_px(a), to_px(b), (40, 26, 26), thickness, cv2.LINE_AA)
    for p in sim.dots:
        cv2.circle(image, to_px(p), max(1, thickness // 2), (40, 26, 26), -1, cv2.LINE_AA)
    return image


def simulate_file(path: Path) -> Simulation:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".plt", ".hpgl", ".hpg"):
        return simulate_hpgl(text)
    return simulate_gcode(text)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Draw what a G-code or HPGL plot file would put on paper.")
    parser.add_argument("plot_file", type=Path)
    parser.add_argument("image", type=Path, help="PNG to write")
    parser.add_argument("--pen", type=float, default=0.4, help="pen width in mm (default 0.4)")
    parser.add_argument("--px-per-mm", type=float, default=10.0)
    parser.add_argument("--travel", action="store_true", help="also draw pen-up travel in light red")
    args = parser.parse_args(argv)

    sim = simulate_file(args.plot_file)
    cv2.imwrite(str(args.image), render(sim, args.pen, args.px_per_mm, args.travel))
    print(f"{sim.pen_downs} pen downs, {sim.draw_mm:.0f} mm drawn, {sim.travel_mm:.0f} mm of travel")
    print(f"pen up at start: {sim.pen_up_at_start}, pen up at end: {sim.pen_up_at_end}")
    for problem in sim.problems:
        print("problem:", problem)
    return 1 if sim.problems or not (sim.pen_up_at_start and sim.pen_up_at_end) else 0


if __name__ == "__main__":
    sys.exit(main())
