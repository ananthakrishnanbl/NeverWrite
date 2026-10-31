"""The PDF layout must place every glyph exactly where the browser preview does.

``layout_text`` in workers/renderer.py is a port of ``layoutText`` in
frontend/src/lib/layout.ts. These tests run both on the same fixture glyphs,
settings and seeds (the TypeScript through ``node --experimental-strip-types``)
and require identical placements, down to the last bit of every float.

Run from backend/: ``python -m pytest tests``
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from workers.renderer import is_single_page, layout_text, missing_chars, page_size_px

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "frontend" / "src" / "lib"
RUNNER = Path(__file__).with_name("layout_runner.mjs")

PROFILE_X_HEIGHT = 40.0

CARD = {
    "page_size": "A6",
    "page_width_mm": 105,
    "page_height_mm": 148,
    "align": "left",
    "font_size": 10,
    "line_spacing": 2.8,
    "ink_color": "#1a1a2e",
    "margin_top": 12,
    "margin_bottom": 12,
    "margin_left": 12,
    "margin_right": 12,
    "messiness": 40,
    "slant_correction": 0,
}


def settings(**overrides: Any) -> Dict[str, Any]:
    return {**CARD, **overrides}


NOTE = (
    "Dear Meera,\n\nThank you so much for the lovely brass lamp. It already "
    "lights up our new home, and we think of you every evening.\n\nWith love,\nAnu"
)

LONG_WORD = "Supercalifragilisticexpialidociousandthensomemoreletters"

CASES: List[Dict[str, Any]] = [
    # A4 runs onto a second page
    {"text": (NOTE + " ") * 12, "settings": settings(page_size="A4", page_width_mm=210, page_height_mm=297,
                                                    margin_top=20, margin_bottom=20, margin_left=25, margin_right=20,
                                                    font_size=12), "seed": 1},
    {"text": NOTE, "settings": settings(), "seed": 12345},
    {"text": NOTE, "settings": settings(align="center"), "seed": 777},
    {"text": "Mr and Mrs Nair\n14 Beach Road\nKozhikode 673001\nKerala",
     "settings": settings(page_size="DL", page_width_mm=220, page_height_mm=110, align="center", font_size=12),
     "seed": 99},
    # A word wider than the line breaks between characters, left and centred
    {"text": f"To {LONG_WORD} and {LONG_WORD}!", "settings": settings(page_size="A7", page_width_mm=74,
                                                                     page_height_mm=105), "seed": 5},
    {"text": f"{LONG_WORD}\n{LONG_WORD}", "settings": settings(page_size="A7", page_width_mm=74,
                                                              page_height_mm=105, align="center"), "seed": 6},
    # Missing glyphs, including a word made only of them, on a custom size
    {"text": "Anu & Rahul && café   déjà vu\n\n  Indented line", "seed": 42,
     "settings": settings(page_size="custom", page_width_mm=90.5, page_height_mm=60, margin_left=8.5)},
    # Overflows the card; trailing newlines must not add an empty page
    {"text": NOTE * 3 + "\n\n\n\n\n\n\n\n", "settings": settings(page_size="A7", page_width_mm=74,
                                                                   page_height_mm=105), "seed": 2024},
    {"text": NOTE + "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n", "settings": settings(), "seed": 3},
    {"text": NOTE, "settings": settings(messiness=0, slant_correction=-10, font_size=14, line_spacing=2.2),
     "seed": 0},
    {"text": NOTE, "settings": settings(messiness=100, slant_correction=7.5), "seed": 0xFFFFFFFF},
    {"text": "", "settings": settings(), "seed": 8},
    {"text": "\n\n", "settings": settings(), "seed": 9},
]


def make_glyphs() -> List[Dict[str, Any]]:
    """Deterministic fake glyphs with varied metrics, several variants, some rejected.

    Lowercase letters, A to M in uppercase (N to Z fall back to lowercase),
    digits and some punctuation. ``&`` and accented letters have no glyph.
    """
    rnd = random.Random(20261031)
    chars = "abcdefghijklmnopqrstuvwxyz" + "ABCDEFGHIJKLM" + "0123456789" + ".,!'?-"
    glyphs: List[Dict[str, Any]] = []
    for ch in chars:
        for variant in range(rnd.randint(1, 3)):
            glyphs.append({
                "id": len(glyphs) + 1,
                "profile_id": 1,
                "char": ch,
                "variant_index": variant,
                "mask_path": f"glyphs/1/{ord(ch)}_{variant}.png",
                "svg_path": None,
                "width": rnd.randint(18, 60),
                "height": rnd.choice([rnd.randint(34, 46), rnd.randint(60, 80)]),
                "baseline_offset": round(rnd.uniform(-4, 18), 3),
                "bearing_left": round(rnd.uniform(0, 6), 2),
                "bearing_right": rnd.randint(0, 6),
                "accepted": rnd.random() > 0.15 or variant == 0,
            })
    return glyphs


def python_results(glyphs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    objs = [SimpleNamespace(**g) for g in glyphs]
    results = []
    for case in CASES:
        pages = layout_text(case["text"], objs, case["settings"], PROFILE_X_HEIGHT, case["seed"])
        results.append({
            "size": list(page_size_px(case["settings"])),
            "pages": [[[p.glyph.id, p.x, p.y, p.scale, p.rotation] for p in page] for page in pages],
            "missing": missing_chars(case["text"], objs),
        })
    return results


def node_results(glyphs: List[Dict[str, Any]], tmp_path: Path) -> List[Dict[str, Any]]:
    """Run layoutText from a copy of layout.ts and prng.ts that Node can import directly."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    lib = tmp_path / "lib"
    lib.mkdir()
    layout_src = (LIB / "layout.ts").read_text(encoding="utf-8")
    # The ./api import is types only; Node also needs the file extension on ./prng
    layout_src = re.sub(r"^import (type )?\{[^}]*\} from '\./api'\n", "", layout_src, flags=re.M)
    layout_src = layout_src.replace("from './prng'", "from './prng.ts'")
    (lib / "layout.ts").write_text(layout_src, encoding="utf-8")
    shutil.copy(LIB / "prng.ts", lib / "prng.ts")
    (lib / "package.json").write_text('{"type": "module"}', encoding="utf-8")

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({
        "glyphs": glyphs,
        "profile_x_height": PROFILE_X_HEIGHT,
        "cases": CASES,
    }), encoding="utf-8")

    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", str(RUNNER), str(lib / "layout.ts"), str(fixture)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def glyphs() -> List[Dict[str, Any]]:
    return make_glyphs()


@pytest.fixture(scope="module")
def both(glyphs, tmp_path_factory):
    return python_results(glyphs), node_results(glyphs, tmp_path_factory.mktemp("parity"))


@pytest.mark.parametrize("index", range(len(CASES)))
def test_preview_and_pdf_place_glyphs_identically(both, index):
    py, js = both[0][index], both[1][index]
    assert py["size"] == js["size"]
    assert py["missing"] == js["missing"]
    assert len(py["pages"]) == len(js["pages"]), "page count differs"
    for page_no, (py_page, js_page) in enumerate(zip(py["pages"], js["pages"])):
        assert len(py_page) == len(js_page), f"glyph count differs on page {page_no}"
        for i, (a, b) in enumerate(zip(py_page, js_page)):
            assert a == b, f"page {page_no} glyph {i}: python {a} != typescript {b}"


def test_fixture_covers_the_cases_that_matter(both):
    py = both[0]
    assert len(py[0]["pages"]) > 1, "A4 case should run onto a second page"
    assert len(py[1]["pages"]) == 1, "the note should fit on one A6 card"
    assert len(py[7]["pages"]) > 1, "the long A7 case should overflow"
    assert py[6]["missing"] == ["&", "é", "à"]


def test_long_word_breaks_inside_the_margins(glyphs):
    objs = [SimpleNamespace(**g) for g in glyphs]
    for case in (CASES[4], CASES[5]):
        s = case["settings"]
        width_px, _ = page_size_px(s)
        left = s["margin_left"] * 96 / 25.4
        right = width_px - s["margin_right"] * 96 / 25.4
        pages = layout_text(case["text"], objs, s, PROFILE_X_HEIGHT, case["seed"])
        placed = [p for page in pages for p in page]
        assert len({round(p.y) for p in placed}) > 2, "the long word should span several lines"
        for p in placed:
            right_edge = p.x - p.glyph.bearing_left * p.scale + (
                p.glyph.bearing_left + p.glyph.width + p.glyph.bearing_right) * p.scale
            assert p.x >= left - 1e-9
            assert right_edge <= right + 1e-9


def test_trailing_newlines_add_no_page(glyphs):
    objs = [SimpleNamespace(**g) for g in glyphs]
    assert len(layout_text(CASES[8]["text"], objs, CASES[8]["settings"], PROFILE_X_HEIGHT, 3)) == 1
    assert layout_text("\n\n", objs, settings(), PROFILE_X_HEIGHT, 9) == [[]]


def test_only_a4_may_use_more_than_one_page():
    assert not is_single_page(settings(page_size="A4"))
    for size in ("A6", "A7", "DL", "custom"):
        assert is_single_page(settings(page_size=size))
