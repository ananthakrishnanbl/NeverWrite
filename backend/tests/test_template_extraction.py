"""End-to-end check of the printed capture sheets.

Each test prints a template (renders the PDF), writes a known character in
boxes with PIL, turns the page into a fake phone photo (perspective, slight
rotation, uneven light, blur, noise, JPEG) and reads it back with the
extractor.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, Iterable, Optional, Tuple

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from workers.extraction import ISSUE_EDGE, SheetError, extract_sheet
from workers.template import (
    PX_PER_MM,
    SHEET_CONFIGS,
    VARIANTS,
    Box,
    build_template_pdf,
    sheet_boxes,
    sheet_markers,
)

RENDER_DPI = 300
RENDER_PX_PER_MM = RENDER_DPI / 25.4
# Font size per variant, in render pixels: x-heights of roughly 3 to 4 mm.
# Each variant is a different size, so a variant read from the wrong box shows.
VARIANT_FONT_PX = (64, 78, 92)
INK_RGB = (28, 32, 70)  # blue-black
GLYPH_PAD_PX = 2        # the extractor's padding around the ink


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.FreeTypeFont:
    # Pillow's bundled font, so the test doesn't depend on system fonts
    return ImageFont.load_default(size=size)


def _render_template(sheet: int, grayscale: bool = False) -> Image.Image:
    pdf = pdfium.PdfDocument(build_template_pdf(sheet))
    page = pdf[0].render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
    if grayscale:  # a black-and-white printer
        page = page.convert("L").convert("RGB")
    return page


def _pen_position(box: Box) -> Tuple[float, float]:
    """Where the pen starts: a little in from the left, on the printed baseline (render px)."""
    return (box.x + 4.0) * RENDER_PX_PER_MM, box.baseline_y * RENDER_PX_PER_MM


@lru_cache(maxsize=None)
def _reference(char: str, variant: int) -> Tuple[np.ndarray, float]:
    """The character as the extractor should return it: a soft mask at the
    canonical resolution, and the ink bottom's distance below the baseline in mm."""
    size = VARIANT_FONT_PX[variant]
    canvas = Image.new("L", (4 * size, 4 * size), 0)
    base = 2.5 * size
    ImageDraw.Draw(canvas).text((size, base), char, font=_font(size), fill=255, anchor="ls")
    a = np.asarray(canvas, dtype=np.float32) / 255
    ys, xs = np.nonzero(a > 0.1)
    bottom_mm = (ys.max() + 1 - base) / RENDER_PX_PER_MM
    tight = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    f = PX_PER_MM / RENDER_PX_PER_MM
    small = cv2.resize(tight, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return small, bottom_mm


def _write_sheet(
    page: Image.Image,
    sheet: int,
    skip: Iterable[Tuple[str, int]] = (),
    cross_edge: Iterable[Tuple[str, int]] = (),
) -> Image.Image:
    """Write each box's character in that box's variant size."""
    page = page.copy()
    draw = ImageDraw.Draw(page)
    skip, cross_edge = set(skip), set(cross_edge)
    for box in sheet_boxes(sheet):
        key = (box.char, box.variant)
        if key in skip:
            continue
        x, y = _pen_position(box)
        draw.text((x, y), box.char, font=_font(VARIANT_FONT_PX[box.variant]), fill=INK_RGB, anchor="ls")
        if key in cross_edge:
            # A tail that runs out of the box into the gap beside it
            end_x = (box.x + box.w + 1.5) * RENDER_PX_PER_MM
            draw.line([(x, y - 20), (end_x, y - 20)], fill=INK_RGB, width=6)
    return page


def _cover_markers(page: Image.Image, sheet: int, corners: Iterable[int]) -> Image.Image:
    page = page.copy()
    draw = ImageDraw.Draw(page)
    for m in sheet_markers(sheet):
        if m.corner in corners:
            x0, y0 = (m.x - 2) * RENDER_PX_PER_MM, (m.y - 2) * RENDER_PX_PER_MM
            x1, y1 = (m.x + m.size + 2) * RENDER_PX_PER_MM, (m.y + m.size + 2) * RENDER_PX_PER_MM
            draw.rectangle([x0, y0, x1, y1], fill=(255, 255, 255))
    return page


def _phone_photo(page: Image.Image, seed: int) -> np.ndarray:
    """A 12 MP portrait photo of the page lying on a table, as BGR."""
    rng = np.random.default_rng(seed)
    src = cv2.cvtColor(np.asarray(page), cv2.COLOR_RGB2BGR)
    h, w = src.shape[:2]
    out_w, out_h = 3000, 4000

    # Page fills most of the frame, turned a few degrees, seen slightly from the side
    scale = 0.86 * out_h / h
    angle = np.deg2rad(rng.uniform(-4, 4))
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64) - [w / 2, h / 2]
    dst = corners * scale @ rot.T + [out_w / 2, out_h / 2]
    dst += rng.uniform(-0.035, 0.035, size=(4, 2)) * out_w
    M = cv2.getPerspectiveTransform(corners.astype(np.float32) + np.float32([w / 2, h / 2]), dst.astype(np.float32))
    photo = cv2.warpPerspective(src, M, (out_w, out_h), flags=cv2.INTER_AREA, borderValue=(70, 105, 140))

    # Uneven light: brighter at one side, a soft shadow across a corner
    yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    light = 0.78 + 0.22 * (xx / out_w)
    light *= 1 - 0.18 * np.exp(-(((xx - 0.2 * out_w) ** 2 + (yy - 0.85 * out_h) ** 2) / (0.3 * out_w) ** 2))
    photo = photo.astype(np.float32) * light[..., None]

    photo = cv2.GaussianBlur(photo, (0, 0), 1.1)
    photo += rng.normal(0, 2.5, photo.shape).astype(np.float32)
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    ok, jpeg = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 72])
    assert ok
    return cv2.imdecode(jpeg, cv2.IMREAD_COLOR)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    """Soft overlap (sum of min over sum of max) of two masks after lining up
    their centres of mass, best over ±1 px. Soft, so a thin stroke a pixel
    thinner after blurring still matches its own size best."""
    if a.sum() == 0 or b.sum() == 0:
        return 0.0
    size = (max(a.shape[0], b.shape[0]) * 2 + 8, max(a.shape[1], b.shape[1]) * 2 + 8)

    def place(m: np.ndarray, dy: int = 0, dx: int = 0) -> np.ndarray:
        ys, xs = np.mgrid[0:m.shape[0], 0:m.shape[1]]
        cy, cx = (ys * m).sum() / m.sum(), (xs * m).sum() / m.sum()
        out = np.zeros(size, np.float32)
        oy, ox = int(round(size[0] / 2 - cy)) + dy, int(round(size[1] / 2 - cx)) + dx
        out[oy:oy + m.shape[0], ox:ox + m.shape[1]] = m
        return out

    pa = place(a)
    best = 0.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            pb = place(b, dy, dx)
            best = max(best, np.minimum(pa, pb).sum() / np.maximum(pa, pb).sum())
    return float(best)


def _core_size(a: np.ndarray) -> Tuple[int, int]:
    """Height and width of the solid part of a mask, which blur barely changes."""
    ys, xs = np.nonzero(a > 0.5)
    return int(ys.max() - ys.min() + 1), int(xs.max() - xs.min() + 1)


def _identify(alpha: np.ndarray, offset_px: float, sheet: int) -> Optional[Tuple[str, int]]:
    """Which (character, variant) of the sheet this glyph is: the best-overlapping
    reference among those with a matching size and baseline position."""
    h, w = _core_size(alpha)
    ink_bottom_mm = (offset_px - GLYPH_PAD_PX) / PX_PER_MM
    best, best_score = None, 0.0
    for box in sheet_boxes(sheet):
        ref, ref_bottom_mm = _reference(box.char, box.variant)
        if abs(ref_bottom_mm - ink_bottom_mm) > 0.6:
            continue
        ref_h, ref_w = _core_size(ref)
        if abs(ref_h - h) > max(2, 0.1 * ref_h) or abs(ref_w - w) > max(2, 0.1 * ref_w):
            continue
        score = _iou(alpha, ref)
        if score > best_score:
            best, best_score = (box.char, box.variant), score
    return best


def _glyphs_by_box(result) -> Dict[Tuple[str, int], object]:
    return {(g.char, g.variant): g for g in result.glyphs}


@pytest.mark.parametrize("sheet,grayscale", [(1, False), (2, False), (3, False), (1, True)],
                         ids=["lowercase", "uppercase", "digits-punctuation", "lowercase-bw-print"])
def test_every_box_lands_on_its_character_and_variant(sheet: int, grayscale: bool) -> None:
    photo = _phone_photo(_write_sheet(_render_template(sheet, grayscale), sheet), seed=sheet)
    result = extract_sheet(photo, sheet)

    assert result.used_markers
    assert result.empty == []
    charset = SHEET_CONFIGS[sheet]["charset"]
    got = _glyphs_by_box(result)
    assert set(got) == {(ch, v) for ch in charset for v in range(VARIANTS)}

    wrong = {}
    for key, g in got.items():
        assert g.issue is None, (key, g.issue)
        _, bottom_mm = _reference(*key)
        expected_offset = bottom_mm * PX_PER_MM + GLYPH_PAD_PX
        assert abs(g.baseline_offset - expected_offset) < 0.4 * PX_PER_MM, (key, g.baseline_offset, expected_offset)
        found = _identify(g.alpha, g.baseline_offset, sheet)
        if found != key:
            wrong[key] = found
    assert not wrong, f"boxes read as the wrong character or variant: {wrong}"


def test_glyphs_are_soft_alpha_masks() -> None:
    photo = _phone_photo(_write_sheet(_render_template(1), 1), seed=11)
    g = _glyphs_by_box(extract_sheet(photo, 1))[("o", 2)]
    a = g.alpha
    assert a.dtype == np.float32 and a.min() >= 0 and a.max() <= 1
    # Solid stroke cores, and a real anti-aliased edge rather than a 0/1 threshold
    assert (a > 0.99).sum() > 50
    assert ((a > 0.05) & (a < 0.95)).sum() > 0.3 * (a > 0.5).sum()
    # The counter of the 'o' stays open
    assert a[a.shape[0] // 2, a.shape[1] // 2] < 0.05


def test_wrong_sheet_is_rejected() -> None:
    photo = _phone_photo(_write_sheet(_render_template(2), 2), seed=5)
    with pytest.raises(SheetError, match=r"Sheet 2 \(uppercase\).*uploaded as Sheet 1"):
        extract_sheet(photo, 1)


def test_empty_boxes_and_ink_over_the_edge_are_flagged() -> None:
    page = _write_sheet(_render_template(1), 1, skip=[("q", 2), ("z", 0)], cross_edge=[("g", 0)])
    result = extract_sheet(_phone_photo(page, seed=7), 1)

    assert sorted(result.empty) == [("q", 2), ("z", 0)]
    got = _glyphs_by_box(result)
    assert ("q", 2) not in got and ("z", 0) not in got
    assert got[("g", 0)].issue == ISSUE_EDGE
    assert [k for k, g in got.items() if g.issue] == [("g", 0)]
    assert "q box 3" in result.warning and "z box 1" in result.warning and "g box 1" in result.warning


def test_three_corner_markers_are_enough() -> None:
    page = _cover_markers(_write_sheet(_render_template(3), 3), 3, corners=[2])
    result = extract_sheet(_phone_photo(page, seed=9), 3)
    assert result.used_markers and result.empty == []


def test_sheet_without_markers_falls_back_to_reading_order() -> None:
    # A hand-made sheet (the lowercase alphabet in two rows on plain paper),
    # photographed full-frame and sent as a compressed chat image: the case
    # the reading-order path was built for
    page = Image.new("RGB", (2480, 3508), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    for i, ch in enumerate(SHEET_CONFIGS[1]["charset"]):
        row, col = divmod(i, 13)
        draw.text((250 + col * 150, 900 + row * 400), ch, font=_font(110), fill=INK_RGB, anchor="ls")
    photo = cv2.cvtColor(np.asarray(page.resize((1131, 1600), Image.LANCZOS)), cv2.COLOR_RGB2BGR)
    ok, jpeg = cv2.imencode(".jpg", cv2.GaussianBlur(photo, (0, 0), 0.8), [cv2.IMWRITE_JPEG_QUALITY, 70])
    result = extract_sheet(cv2.imdecode(jpeg, cv2.IMREAD_COLOR), 1)

    assert not result.used_markers
    assert result.warning is None
    assert [g.char for g in result.glyphs] == list(SHEET_CONFIGS[1]["charset"])
    assert all(g.variant == 0 for g in result.glyphs)
    soft = result.glyphs[0].alpha
    assert ((soft > 0.05) & (soft < 0.95)).any()


def test_two_corner_markers_are_not() -> None:
    page = _cover_markers(_write_sheet(_render_template(3), 3), 3, corners=[0, 2])
    with pytest.raises(SheetError, match=r"Only 2 of the 4 corner squares.*top-left, bottom-right"):
        extract_sheet(_phone_photo(page, seed=9), 3)
