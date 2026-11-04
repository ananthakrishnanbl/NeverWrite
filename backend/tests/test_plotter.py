"""Letters drawn in the browser, scanned letters traced for plotting, and the plot files built from them.

A plot file must only ever ask the machine for single-line pen moves inside
the card: no filled shapes, no heater or extruder commands, the pen up before
the first move and after the last. Same text, settings and seed give the same
bytes. ``workers.plot_sim`` replays G-code and HPGL to check what would be
drawn without a plotter.

Run from backend/: ``python -m pytest tests``
"""
from __future__ import annotations

import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import storage
from app.database import Base, get_db
from app.main import app
from app.models.glyph import Glyph
from app.models.profile import Profile
from app.models.render_batch import RenderBatch
from app.models.render_job import RenderJob
from stroke_letters import CHARS, letter
from workers.extraction import ExtractedGlyph, _save_glyphs
from workers.plot_sim import simulate_gcode, simulate_hpgl
from workers.plotter import clip_path, order_paths
from workers.geometry import simplify
from workers.strokes import STROKE_WIDTH_MM, StrokeError, build_glyph, clean_strokes
from workers.template import BASELINE_FROM_TOP_MM, PX_PER_MM
from workers.tracing import TRACE_VERSION

NOTE = (
    "Dear Meera,\n\nThank you so much for the lovely brass lamp. It already lights up our new home, "
    "and we think of you every evening.\n\nWith love,\nAnu"
)
A6 = {"page_size": "A6", "page_width_mm": 105, "page_height_mm": 148}
FORMATS = ("svg", "gcode", "hpgl")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A test client on an empty in-memory database, with files under *tmp_path*."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app), Session()
    finally:
        app.dependency_overrides.pop(get_db, None)


def new_profile(db) -> int:
    profile = Profile(name="test")
    db.add(profile)
    db.commit()
    return profile.id


def draw(client: TestClient, profile_id: int, chars: str = CHARS) -> None:
    for ch in chars:
        for v in range(3):
            res = client.post(f"/api/profiles/{profile_id}/drawn-glyphs",
                              json={"char": ch, "variant_index": v, "strokes": letter(ch, v)})
            assert res.status_code == 201, res.text


@pytest.fixture
def drawn(env):
    """A profile with every character of the test alphabet drawn three times."""
    client, db = env
    pid = new_profile(db)
    draw(client, pid)
    return client, db, pid


def add_render(db, profile_id: int, text: str = NOTE, seed: int = 12345, **settings: Any) -> int:
    job = RenderJob(profile_id=profile_id, text=text, settings_json=json.dumps({**A6, **settings}),
                    seed=seed, status="done")
    db.add(job)
    db.commit()
    return job.id


def svg_points(svg: str) -> List[tuple]:
    return [(float(x), float(y)) for x, y in re.findall(r"(-?\d+\.\d+) (-?\d+\.\d+)", " ".join(
        p.get("d").replace("M", " ").replace("L", " ") for p in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}path")))]


# ---------------------------------------------------------------------------
# Drawn glyphs
# ---------------------------------------------------------------------------


def test_drawn_glyph_is_cropped_and_sits_on_the_baseline() -> None:
    # A down stroke from the x-height line to the baseline
    glyph = build_glyph(clean_strokes([[[9.0, 9.0], [9.0, 13.0]]]))
    half = STROKE_WIDTH_MM * PX_PER_MM / 2
    bottom = math.ceil(13.0 * PX_PER_MM + half + 2)
    top = math.floor(9.0 * PX_PER_MM - half - 2)
    assert glyph.alpha.shape == (bottom - top, math.ceil(9 * PX_PER_MM + half + 2) - math.floor(9 * PX_PER_MM - half - 2))
    assert glyph.baseline_offset == bottom - BASELINE_FROM_TOP_MM * PX_PER_MM
    # Strokes are stored in mask pixels, and the mask is solid along them and clear at the corners
    (x0, y0), (x1, y1) = glyph.strokes[0]
    assert x0 == x1 and y1 - y0 == pytest.approx(4 * PX_PER_MM)
    assert glyph.alpha[int(y0) + 5:int(y1) - 5, int(x0)].min() > 0.95
    assert glyph.alpha[0, 0] == 0 and glyph.alpha[-1, -1] == 0


def test_a_dot_is_kept_and_drawn() -> None:
    glyph = build_glyph(clean_strokes([[[9.0, 12.8]]]))
    assert len(glyph.strokes) == 1 and len(glyph.strokes[0]) == 1
    assert glyph.alpha.max() > 0.95


def test_clean_strokes() -> None:
    # Collinear points go, the ends stay
    line = [[float(i), 10.0] for i in range(2, 15)]
    assert clean_strokes([line]) == [[(2.0, 10.0), (14.0, 10.0)]]
    # A slight overshoot past the box edge is pulled back onto it
    assert clean_strokes([[[-1.0, 5.0], [3.0, 22.0]]]) == [[(0.0, 5.0), (3.0, 21.0)]]
    for bad in ([], [[]], [[[50.0, 5.0]]], [[[1.0]]], [[["x", 2]]], [[[float("nan"), 1.0]]]):
        with pytest.raises((StrokeError, ValueError)):
            clean_strokes(bad)


def test_simplify_keeps_curves() -> None:
    circle = [(9 + 2 * math.cos(t / 50 * 2 * math.pi), 11 + 2 * math.sin(t / 50 * 2 * math.pi)) for t in range(51)]
    kept = simplify(circle, 0.03)
    assert kept[0] == circle[0] and kept[-1] == circle[-1]
    assert 8 < len(kept) < len(circle)


def test_draw_endpoint_saves_and_replaces_a_slot(env, tmp_path) -> None:
    client, db = env
    pid = new_profile(db)
    first = client.post(f"/api/profiles/{pid}/drawn-glyphs",
                        json={"char": "a", "variant_index": 1, "strokes": letter("a", 1)}).json()
    assert first["has_strokes"] and first["accepted"] and first["variant_index"] == 1
    assert (tmp_path / first["mask_path"]).exists()

    second = client.post(f"/api/profiles/{pid}/drawn-glyphs",
                         json={"char": "a", "variant_index": 1, "strokes": letter("a", 2)}).json()
    assert second["id"] == first["id"] and second["mask_path"] != first["mask_path"]
    assert not (tmp_path / first["mask_path"]).exists() and (tmp_path / second["mask_path"]).exists()

    listed = client.get(f"/api/profiles/{pid}/glyphs").json()
    assert [(g["char"], g["variant_index"], g["has_strokes"], g["stroke_source"]) for g in listed] == [
        ("a", 1, True, "drawn")]
    metrics = json.loads(db.get(Profile, pid).metrics_json)
    assert metrics["x_height"] == second["height"]


@pytest.mark.parametrize("body", [
    {"char": "é", "variant_index": 0, "strokes": [[[9, 9], [9, 13]]]},
    {"char": "a", "variant_index": 3, "strokes": [[[9, 9], [9, 13]]]},
    {"char": "a", "variant_index": 0, "strokes": []},
    {"char": "a", "variant_index": 0, "strokes": [[[90, 9]]]},
])
def test_draw_endpoint_rejects_bad_drawings(env, body) -> None:
    client, db = env
    pid = new_profile(db)
    assert client.post(f"/api/profiles/{pid}/drawn-glyphs", json=body).status_code == 422
    assert client.post("/api/profiles/999/drawn-glyphs",
                       json={"char": "a", "variant_index": 0, "strokes": letter("a")}).status_code == 404


def scan(db, profile_id: int, char: str, variant: int = 0) -> None:
    """Save a scanned glyph: a 4 px wide upright bar, as extraction would."""
    alpha = np.zeros((20, 10), np.float32)
    alpha[5:15, 3:7] = 1.0
    _save_glyphs([ExtractedGlyph(char, variant, alpha, 2.0)], profile_id, db, bearing=6.0, tag="scan")


def test_a_rescan_replaces_drawn_strokes(env) -> None:
    client, db = env
    pid = new_profile(db)
    draw(client, pid, "a")
    scan(db, pid, "a")
    db.expire_all()
    rows = {g.variant_index: g for g in db.query(Glyph).filter(Glyph.char == "a")}
    # The scanned slot now holds a path traced down the middle of the bar
    assert rows[0].stroke_source == "traced"
    (stroke,) = rows[0].strokes()
    assert all(x == pytest.approx(5.0, abs=0.3) for x, _ in stroke) and stroke[-1][1] - stroke[0][1] > 3
    assert rows[1].stroke_source == "drawn" and rows[2].stroke_source == "drawn"


# ---------------------------------------------------------------------------
# Plot files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_plot_files_stay_on_the_card(drawn, fmt) -> None:
    client, db, pid = drawn
    rid = add_render(db, pid)
    summary = client.get(f"/api/renders/{rid}/plot", params={"format": fmt}).json()
    res = client.get(f"/api/renders/{rid}/plot/file", params={"format": fmt})
    assert res.status_code == 200
    text = res.text
    assert summary["unplottable_chars"] == [] and summary["clipped_strokes"] == 0
    assert summary["pen_lifts"] > 100 and summary["draw_mm"] > 500

    if fmt == "svg":
        root = ET.fromstring(text)
        assert root.get("width") == "105.00mm" and root.get("viewBox") == "0 0 105.00 148.00"
        tags = {el.tag.split("}")[1] for el in root.iter()}
        assert tags == {"svg", "title", "desc", "g", "path"}
        group = root.find("{http://www.w3.org/2000/svg}g")
        assert group.get("fill") == "none" and group.get("stroke-linecap") == "round"
        assert not any(p.get("fill") for p in root.iter("{http://www.w3.org/2000/svg}path"))
        assert len(list(root.iter("{http://www.w3.org/2000/svg}path"))) == summary["pen_lifts"]
        points = svg_points(text)
        assert all(0 <= x <= 105 and 0 <= y <= 148 for x, y in points)
        return

    sim = simulate_gcode(text) if fmt == "gcode" else simulate_hpgl(text)
    assert sim.problems == []
    assert sim.pen_up_at_start and sim.pen_up_at_end
    assert sim.pen_downs == summary["pen_lifts"]
    assert sim.draw_mm == pytest.approx(summary["draw_mm"], rel=0.005)
    assert all(-0.01 <= x <= 105.01 and -0.01 <= y <= 148.01 for x, y in sim.drawn_points())


def test_gcode_is_safe_for_a_printer(drawn) -> None:
    client, db, pid = drawn
    rid = add_render(db, pid)
    gcode = client.get(f"/api/renders/{rid}/plot/file",
                       params={"format": "gcode", "pen_up_z": 3, "pen_down_z": 0.5}).text
    commands = [line.split(";")[0].strip() for line in gcode.splitlines() if line.split(";")[0].strip()]
    assert commands[:5] == ["G21", "G90", "G92 Z0.50", "G0 Z3.00 F300", "G28 X Y"]
    assert commands[-2:] == ["G0 X0.00 Y0.00 F6000", "M84 X Y"]
    assert commands[-3] == "G0 Z3.00 F300"
    assert not re.search(r"\bM(104|109|140|190|106)\b", gcode)
    assert not re.search(r"^G[01]\b.*\bE-?\d", gcode, re.M)
    # Every drawing move follows a pen-down, at the drawing speed
    pen = "up"
    for cmd in commands:
        if cmd.startswith("G1 Z"):
            pen = "down"
        elif cmd.startswith("G0 Z"):
            pen = "up"
        elif cmd.startswith("G1 X"):
            assert pen == "down"
        elif cmd.startswith("G0 X"):
            assert pen == "up"


def test_offsets_move_the_card_on_the_bed(drawn) -> None:
    client, db, pid = drawn
    rid = add_render(db, pid)
    sim = simulate_gcode(client.get(f"/api/renders/{rid}/plot/file",
                                    params={"format": "gcode", "offset_x": 50, "offset_y": 20}).text)
    xs = [x for x, _ in sim.drawn_points()]
    ys = [y for _, y in sim.drawn_points()]
    assert min(xs) >= 50 and max(xs) <= 155 and min(ys) >= 20 and max(ys) <= 168


@pytest.mark.parametrize("fmt", FORMATS)
def test_same_seed_same_bytes(drawn, fmt) -> None:
    client, db, pid = drawn
    a = client.get(f"/api/renders/{add_render(db, pid, seed=7)}/plot/file", params={"format": fmt}).content
    b = client.get(f"/api/renders/{add_render(db, pid, seed=7)}/plot/file", params={"format": fmt}).content
    c = client.get(f"/api/renders/{add_render(db, pid, seed=8)}/plot/file", params={"format": fmt}).content
    assert a == b and a != c


def test_ordering_cuts_travel() -> None:
    rng = np.random.default_rng(1)
    paths = [[(float(x), float(y)), (float(x) + 1, float(y) + 1)] for x, y in rng.uniform(0, 100, (200, 2))]

    def travel(ps):
        pos, total = (0.0, 100.0), 0.0
        for p in ps:
            total += math.dist(pos, p[0])
            pos = p[-1]
        return total

    ordered = order_paths(paths, (0.0, 100.0))
    assert len(ordered) == len(paths)
    assert travel(ordered) < travel(paths) / 3
    assert any(p not in paths for p in ordered)  # some drawn backwards


def test_clip_path() -> None:
    assert clip_path([(5.0, 5.0), (15.0, 5.0)], 10, 10) == [[(5.0, 5.0), (10.0, 5.0)]]
    # Out and back in: two pieces
    assert clip_path([(5.0, 5.0), (5.0, -5.0), (8.0, 5.0)], 10, 10) == [
        [(5.0, 5.0), (5.0, 0.0)], [(5.0 + 3 * 0.5, 0.0), (8.0, 5.0)]]
    assert clip_path([(20.0, 20.0)], 10, 10) == []


def test_ink_past_the_edge_is_cut(drawn) -> None:
    client, db, pid = drawn
    # No top margin: capitals on the first line reach above the card
    rid = add_render(db, pid, margin_top=0)
    summary = client.get(f"/api/renders/{rid}/plot").json()
    assert summary["clipped_strokes"] > 0
    sim = simulate_gcode(client.get(f"/api/renders/{rid}/plot/file", params={"format": "gcode"}).text)
    assert max(y for _, y in sim.drawn_points()) <= 148.0


def test_dots_are_plotted(drawn) -> None:
    client, db, pid = drawn
    rid = add_render(db, pid, text=".")
    svg = client.get(f"/api/renders/{rid}/plot/file").text
    (d,) = [p.get("d") for p in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}path")]
    x, y = d[1:].split("L")[0].split()
    assert d == f"M{x} {y}L{x} {y}"
    sim = simulate_gcode(client.get(f"/api/renders/{rid}/plot/file", params={"format": "gcode"}).text)
    assert len(sim.dots) == 1 and sim.drawn == []
    assert len(simulate_hpgl(client.get(f"/api/renders/{rid}/plot/file", params={"format": "hpgl"}).text).dots) == 1


def test_traced_letters_are_listed(env) -> None:
    client, db = env
    pid = new_profile(db)
    draw(client, pid, "abc")
    scan(db, pid, "d")
    summary = client.get(f"/api/renders/{add_render(db, pid, text='cab dab xy')}/plot").json()
    assert summary["traced_chars"] == ["d"] and summary["drawn_chars"] == ["c", "a", "b"]
    assert summary["unplottable_chars"] == []
    assert summary["missing_chars"] == ["x", "y"]


def test_a_scanned_profile_is_plotted(env) -> None:
    client, db = env
    pid = new_profile(db)
    scan(db, pid, "a")
    rid = add_render(db, pid, text="aaa")
    summary = client.get(f"/api/renders/{rid}/plot").json()
    assert summary["traced_chars"] == ["a"] and summary["pen_lifts"] == 3
    sim = simulate_gcode(client.get(f"/api/renders/{rid}/plot/file", params={"format": "gcode"}).text)
    assert sim.problems == [] and sim.pen_downs == 3 and sim.draw_mm > 0


def test_older_scans_are_traced_when_first_needed(env, tmp_path) -> None:
    client, db = env
    pid = new_profile(db)
    scan(db, pid, "a")
    # As if the glyph had been saved before tracing existed
    glyph = db.query(Glyph).one()
    glyph.strokes_json, glyph.stroke_source, glyph.trace_version = None, None, None
    db.commit()

    strokes = client.get(f"/api/profiles/{pid}/strokes").json()
    assert strokes[str(glyph.id)]["source"] == "traced" and len(strokes[str(glyph.id)]["strokes"]) == 1
    db.expire_all()
    assert db.get(Glyph, glyph.id).stroke_source == "traced"

    # A glyph whose image has gone can't be traced, and is listed as unplottable
    draw(client, pid, "b")
    glyph = db.get(Glyph, glyph.id)
    glyph.strokes_json, glyph.stroke_source, glyph.trace_version = None, None, None
    db.commit()
    (tmp_path / glyph.mask_path).unlink()
    summary = client.get(f"/api/renders/{add_render(db, pid, text='ab')}/plot").json()
    assert summary["unplottable_chars"] == ["a"] and summary["traced_chars"] == []


def test_paths_from_an_older_tracer_are_traced_again(env) -> None:
    client, db = env
    pid = new_profile(db)
    scan(db, pid, "a")
    glyph = db.query(Glyph).one()
    glyph.strokes_json, glyph.trace_version = "[[[1,1],[2,2]]]", 0
    db.commit()
    client.get(f"/api/profiles/{pid}/strokes")
    db.expire_all()
    glyph = db.get(Glyph, glyph.id)
    assert glyph.trace_version == TRACE_VERSION and glyph.strokes_json != "[[[1,1],[2,2]]]"


def test_drawn_strokes_are_never_retraced(env) -> None:
    client, db = env
    pid = new_profile(db)
    draw(client, pid, "a")
    before = {g.id: g.strokes_json for g in db.query(Glyph)}
    strokes = client.get(f"/api/profiles/{pid}/strokes").json()
    assert {v["source"] for v in strokes.values()} == {"drawn"}
    db.expire_all()
    assert {g.id: g.strokes_json for g in db.query(Glyph)} == before


def test_an_empty_profile_cant_be_plotted(env) -> None:
    client, db = env
    pid = new_profile(db)
    res = client.get(f"/api/renders/{add_render(db, pid, text='aaa')}/plot")
    assert res.status_code == 409 and "no letters" in res.json()["detail"]
    assert client.get("/api/profiles/999/strokes").status_code == 404


def test_pages_and_overflow(drawn) -> None:
    client, db, pid = drawn
    long_text = (NOTE + " ") * 6
    assert client.get(f"/api/renders/{add_render(db, pid, text=long_text)}/plot").status_code == 409
    rid = add_render(db, pid, text=long_text * 2, page_size="A4", page_width_mm=210, page_height_mm=297)
    first = client.get(f"/api/renders/{rid}/plot").json()
    assert first["page_count"] > 1
    second = client.get(f"/api/renders/{rid}/plot", params={"page": 2}).json()
    assert second["page"] == 2 and second["draw_mm"] > 0
    assert client.get(f"/api/renders/{rid}/plot", params={"page": 99}).status_code == 404
    res = client.get(f"/api/renders/{rid}/plot/file", params={"page": 2})
    assert "page2" in res.headers["content-disposition"]


def test_bad_machine_settings(drawn) -> None:
    client, db, pid = drawn
    rid = add_render(db, pid)
    assert client.get(f"/api/renders/{rid}/plot", params={"pen_up_z": 0, "pen_down_z": 1}).status_code == 422
    assert client.get(f"/api/renders/{rid}/plot", params={"draw_speed": 0}).status_code == 422
    assert client.get(f"/api/renders/{rid}/plot", params={"format": "pdf"}).status_code == 422


def test_plot_time_estimate(drawn) -> None:
    client, db, pid = drawn
    rid = add_render(db, pid)
    s = client.get(f"/api/renders/{rid}/plot", params={"format": "gcode", "pen_up_z": 2, "z_speed": 300}).json()
    expected = s["draw_mm"] / 2400 * 60 + s["travel_mm"] / 6000 * 60 + s["pen_lifts"] * (2 * 2 / 300 * 60)
    assert s["seconds"] == pytest.approx(expected, abs=0.2)
    faster = client.get(f"/api/renders/{rid}/plot", params={"format": "gcode", "draw_speed": 4800}).json()
    assert faster["seconds"] < s["seconds"]


# ---------------------------------------------------------------------------
# Mail-merge sets
# ---------------------------------------------------------------------------


def add_batch(db, profile_id: int, cards: List[Dict[str, Any]]) -> int:
    batch = RenderBatch(profile_id=profile_id, settings_json=json.dumps(A6), cards_json=json.dumps(cards), status="done")
    db.add(batch)
    db.commit()
    return batch.id


def test_batch_plots(drawn) -> None:
    client, db, pid = drawn
    cards = [{"label": name, "text": f"Dear {name},\nthank you!", "seed": i} for i, name in
             enumerate(["Meera", "Anu Nair", "Zoë"])]
    bid = add_batch(db, pid, cards)

    summary = client.get(f"/api/render-batches/{bid}/plot", params={"format": "gcode"}).json()
    assert [c["label"] for c in summary["cards"]] == ["Meera", "Anu Nair", "Zoë"]
    assert summary["total_seconds"] == pytest.approx(sum(c["seconds"] for c in summary["cards"]), abs=0.2)

    res = client.get(f"/api/render-batches/{bid}/plot/zip", params={"format": "gcode"})
    names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
    assert names == ["card_01_Meera.gcode", "card_02_Anu-Nair.gcode", "card_03_Zoë.gcode"]
    assert res.content == client.get(f"/api/render-batches/{bid}/plot/zip", params={"format": "gcode"}).content

    one = client.get(f"/api/render-batches/{bid}/plot/file", params={"format": "hpgl", "card": 3})
    assert one.status_code == 200 and "card_03_Zo%C3%AB.plt" in one.headers["content-disposition"]
    assert simulate_hpgl(one.text).problems == []
    assert client.get(f"/api/render-batches/{bid}/plot/file", params={"card": 4}).status_code == 404
