"""Printable capture sheets: the layout shared by the PDF and the extractor.

Every measurement here is in millimetres on an A4 page, origin at the top-left
corner, y pointing down. ``build_template_pdf`` draws the sheet from this
layout, and the extractor warps each photo onto the same coordinates at
``PX_PER_MM`` pixels per millimetre, so it can read every box by position.

Each sheet has
- four ArUco markers (DICT_4X4_50), one per corner; the marker id encodes both
  the sheet and the corner, so a photo of the wrong sheet is caught;
- three boxes per character (one per variant) with a grey label above them;
- a baseline and a dashed x-height line inside every box, printed in a light
  cyan that the extractor removes by colour (or by lightness on a
  black-and-white print).
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Tuple

import cv2
import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

# Sheet-specific character sets, in the order they appear on the sheet
SHEET_CONFIGS: Dict[int, Dict[str, str]] = {
    1: {"name": "Lowercase", "charset": "abcdefghijklmnopqrstuvwxyz"},
    2: {"name": "Uppercase", "charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    3: {"name": "Digits & Punctuation", "charset": "0123456789.,!?;:'\"-()"},
}

PAGE_W_MM, PAGE_H_MM = 210.0, 297.0

# Resolution the extractor warps photos to. A full-resolution phone photo of
# an A4 sheet has roughly this many pixels per millimetre.
PX_PER_MM = 12
CANVAS_W_PX = int(PAGE_W_MM * PX_PER_MM)
CANVAS_H_PX = int(PAGE_H_MM * PX_PER_MM)

ARUCO_DICT = cv2.aruco.DICT_4X4_50
MARKER_SIZE_MM = 14.0
MARKER_INSET_MM = 10.0   # from the page edge; clear of most printers' unprintable margin
CORNERS = ("top-left", "top-right", "bottom-right", "bottom-left")

VARIANTS = 3
GROUPS_PER_ROW = 3
GRID_LEFT_MM = 12.0
GRID_TOP_MM = 28.0
ROW_PITCH_MM = 26.9
LABEL_H_MM = 4.0         # grey character label above each group of boxes
BOX_W_MM = 18.0
BOX_H_MM = 21.0
BOX_GAP_MM = 2.0         # between the variant boxes of one character
GROUP_GAP_MM = 6.0       # between characters
BASELINE_FROM_TOP_MM = 13.0
X_HEIGHT_MM = 4.0

# Light cyan: near white in the green channel the extractor reads ink from,
# and light enough to fall under the ink threshold on a black-and-white print
GUIDE_RGB = (0.60, 0.87, 0.97)
LABEL_GREY = 0.45

_PUNCTUATION_NAMES = {
    ".": "full stop", ",": "comma", "!": "exclamation", "?": "question",
    ";": "semicolon", ":": "colon", "'": "apostrophe", '"': "quote",
    "-": "hyphen", "(": "open bracket", ")": "close bracket",
}


@dataclass(frozen=True)
class Box:
    """One writing box on a sheet, in millimetres."""

    char: str
    variant: int
    x: float
    y: float
    w: float
    h: float

    @property
    def baseline_y(self) -> float:
        return self.y + BASELINE_FROM_TOP_MM

    @property
    def x_height_y(self) -> float:
        return self.baseline_y - X_HEIGHT_MM


@dataclass(frozen=True)
class Marker:
    """One corner marker: its ArUco id and the top-left of its square, in millimetres."""

    marker_id: int
    corner: int
    x: float
    y: float
    size: float = MARKER_SIZE_MM

    def corner_points(self) -> List[Tuple[float, float]]:
        """Corners in the order ArUco reports them: clockwise from top-left."""
        s = self.size
        return [(self.x, self.y), (self.x + s, self.y), (self.x + s, self.y + s), (self.x, self.y + s)]


def sheet_name(sheet_number: int) -> str:
    return SHEET_CONFIGS[sheet_number]["name"]


def marker_id(sheet_number: int, corner: int) -> int:
    """ArUco id for a sheet's corner: sheet 1 uses 0-3, sheet 2 uses 4-7, sheet 3 uses 8-11."""
    return (sheet_number - 1) * 4 + corner


def decode_marker_id(mid: int) -> Tuple[int, int] | None:
    """Return ``(sheet_number, corner)`` for one of our marker ids, or ``None``."""
    sheet, corner = mid // 4 + 1, mid % 4
    return (sheet, corner) if sheet in SHEET_CONFIGS else None


def sheet_markers(sheet_number: int) -> List[Marker]:
    far_x = PAGE_W_MM - MARKER_INSET_MM - MARKER_SIZE_MM
    far_y = PAGE_H_MM - MARKER_INSET_MM - MARKER_SIZE_MM
    origins = [(MARKER_INSET_MM, MARKER_INSET_MM), (far_x, MARKER_INSET_MM),
               (far_x, far_y), (MARKER_INSET_MM, far_y)]
    return [Marker(marker_id(sheet_number, c), c, x, y) for c, (x, y) in enumerate(origins)]


def sheet_boxes(sheet_number: int) -> List[Box]:
    """Every box on the sheet, in charset order, three variants per character."""
    group_w = VARIANTS * BOX_W_MM + (VARIANTS - 1) * BOX_GAP_MM
    boxes: List[Box] = []
    for i, ch in enumerate(SHEET_CONFIGS[sheet_number]["charset"]):
        row, col = divmod(i, GROUPS_PER_ROW)
        group_x = GRID_LEFT_MM + col * (group_w + GROUP_GAP_MM)
        box_y = GRID_TOP_MM + row * ROW_PITCH_MM + LABEL_H_MM
        for v in range(VARIANTS):
            boxes.append(Box(ch, v, group_x + v * (BOX_W_MM + BOX_GAP_MM), box_y, BOX_W_MM, BOX_H_MM))
    return boxes


def mm_to_px(value_mm: float) -> float:
    """Millimetres to canonical pixel coordinates (pixel centres sit at integers)."""
    return value_mm * PX_PER_MM - 0.5


def char_label(ch: str) -> Tuple[str, str]:
    """The printed label for a character: the character itself and an optional name."""
    return ch, _PUNCTUATION_NAMES.get(ch, "")


def template_filename(sheet_number: int) -> str:
    slug = {1: "lowercase", 2: "uppercase", 3: "digits-punctuation"}[sheet_number]
    return f"neverwrite-sheet-{sheet_number}-{slug}.pdf"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _marker_bits(mid: int) -> np.ndarray:
    """The marker as a 6x6 grid (border included), 0 = black cell."""
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    return cv2.aruco.generateImageMarker(dictionary, mid, 6, borderBits=1)


@lru_cache(maxsize=None)
def build_template_pdf(sheet_number: int) -> bytes:
    """Render one capture sheet as a single-page A4 PDF."""
    if sheet_number not in SHEET_CONFIGS:
        raise ValueError(f"Unknown sheet {sheet_number}")

    buf = io.BytesIO()
    page_w, page_h = A4
    # invariant=1 keeps the bytes identical between calls (no timestamp or random id)
    c = rl_canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"NeverWrite handwriting sheet {sheet_number}: {sheet_name(sheet_number)}")

    def X(x_mm: float) -> float:
        return x_mm * mm

    def Y(y_mm: float) -> float:
        # Layout is y-down from the top; PDF is y-up from the bottom
        return page_h - y_mm * mm

    # Corner markers, drawn as vector cells so they stay sharp at any print size
    c.setFillColorRGB(0, 0, 0)
    for marker in sheet_markers(sheet_number):
        bits = _marker_bits(marker.marker_id)
        cell = marker.size / bits.shape[0]
        for r in range(bits.shape[0]):
            for col in range(bits.shape[1]):
                if bits[r, col] == 0:
                    c.rect(X(marker.x + col * cell), Y(marker.y + (r + 1) * cell),
                           cell * mm, cell * mm, stroke=0, fill=1)

    # Header and footer, between the markers
    text_x = MARKER_INSET_MM + MARKER_SIZE_MM + 6
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(X(text_x), Y(15.5), f"NeverWrite handwriting sheet {sheet_number} of {len(SHEET_CONFIGS)}: "
                                     f"{sheet_name(sheet_number).lower()}")
    c.setFillColorRGB(LABEL_GREY, LABEL_GREY, LABEL_GREY)
    c.setFont("Helvetica", 7.5)
    c.drawString(X(text_x), Y(20.0), "Write the character once in each of its three boxes, sitting on the solid "
                                     "blue line. The dashed line is the height of a small x.")
    c.drawString(X(text_x), Y(23.5), "Use a dark pen and your everyday style. Keep each letter inside its box "
                                     "and leave the black corner squares clear.")
    c.drawString(X(text_x), Y(PAGE_H_MM - 18.0), "Scan the sheet, or photograph it from straight above in good "
                                                  "light with all four corner squares in view.")
    c.drawString(X(text_x), Y(PAGE_H_MM - 14.5), "Upload the original file. Photos sent through chat apps are "
                                                  "compressed and lose the detail of your strokes.")

    # Boxes, labels and guides
    for box in sheet_boxes(sheet_number):
        if box.variant == 0:
            ch, name = char_label(box.char)
            c.setFillColorRGB(LABEL_GREY, LABEL_GREY, LABEL_GREY)
            c.setFont("Helvetica-Bold", 10)
            c.drawString(X(box.x + 0.5), Y(box.y - 1.0), ch)
            if name:
                c.setFont("Helvetica", 7)
                c.drawString(X(box.x + 0.5) + c.stringWidth(ch, "Helvetica-Bold", 10) + 4, Y(box.y - 1.0), name)

        c.setStrokeColorRGB(*GUIDE_RGB)
        c.setLineWidth(0.25 * mm)
        c.setDash()
        c.rect(X(box.x), Y(box.y + box.h), box.w * mm, box.h * mm, stroke=1, fill=0)
        c.setLineWidth(0.3 * mm)
        c.line(X(box.x), Y(box.baseline_y), X(box.x + box.w), Y(box.baseline_y))
        c.setLineWidth(0.2 * mm)
        c.setDash(1.2 * mm, 1.0 * mm)
        c.line(X(box.x), Y(box.x_height_y), X(box.x + box.w), Y(box.x_height_y))
        c.setDash()

    c.showPage()
    c.save()
    return buf.getvalue()
