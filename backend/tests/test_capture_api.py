"""The template download endpoint and how rescans update stored variants."""
from __future__ import annotations

import numpy as np
import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import storage
from app.database import Base
from app.main import app
from app.models.glyph import Glyph
from app.models.profile import Profile
from workers.extraction import ISSUE_EDGE, ExtractedGlyph, _save_glyphs


@pytest.mark.parametrize("sheet", [1, 2, 3])
def test_template_pdf_download(sheet: int) -> None:
    res = TestClient(app).get(f"/api/templates/{sheet}/pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert f"sheet-{sheet}" in res.headers["content-disposition"]
    assert len(pdfium.PdfDocument(res.content)) == 1


def test_unknown_template_is_404() -> None:
    assert TestClient(app).get("/api/templates/4/pdf").status_code == 404


def test_rescan_replaces_only_the_variants_it_contains(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    profile = Profile(name="test")
    db.add(profile)
    db.commit()

    def glyph(variant: int, issue=None) -> ExtractedGlyph:
        alpha = np.zeros((20, 10), np.float32)
        alpha[5:15, 3:7] = 1.0
        return ExtractedGlyph("a", variant, alpha, 2.0, issue)

    _save_glyphs([glyph(0), glyph(1), glyph(2)], profile.id, db, bearing=6.0, tag="scan1")
    before = {g.variant_index: g.mask_path for g in db.query(Glyph)}

    # A second sheet with only the middle box filled in, and that one over the edge
    _save_glyphs([glyph(1, ISSUE_EDGE)], profile.id, db, bearing=6.0, tag="scan2")
    rows = {g.variant_index: g for g in db.query(Glyph).filter(Glyph.char == "a")}

    assert sorted(rows) == [0, 1, 2]
    assert rows[0].mask_path == before[0] and rows[2].mask_path == before[2]
    assert rows[1].mask_path != before[1] and "scan2" in rows[1].mask_path
    assert rows[1].accepted is False and rows[1].issue == ISSUE_EDGE
    assert rows[0].accepted is True and rows[0].issue is None
    # The replaced image is gone; the new one and the untouched ones are on disk
    assert not (tmp_path / before[1]).exists()
    assert all((tmp_path / g.mask_path).exists() for g in rows.values())
