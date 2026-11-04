"""Glyph ORM model.

Each Glyph represents one extracted instance of a single character from a
handwriting scan.  Multiple variants of the same character can exist for a
single profile (one per occurrence on the sheet).
"""
import json
from typing import List, Optional, Tuple

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text

from app.database import Base


class Glyph(Base):
    """A single extracted glyph (character image + metrics).

    Attributes
    ----------
    id:
        Auto-incrementing primary key.
    profile_id:
        Foreign key referencing the owning :class:`~app.models.profile.Profile`.
    char:
        The Unicode character this glyph represents (single codepoint string).
    variant_index:
        Zero-based index distinguishing multiple occurrences of the same
        character within a profile.
    mask_path:
        Relative path (from DATA_DIR) to the binary mask PNG.
    svg_path:
        Optional relative path to a vectorised SVG version of the glyph.
    width:
        Bounding-box width in pixels (at extraction resolution).
    height:
        Bounding-box height in pixels.
    baseline_offset:
        How far the bottom edge of the mask sits below the writing baseline,
        in pixels. Positive for descenders, negative for marks above the
        line such as apostrophes, measured from the scanned row.
    bearing_left:
        Left side-bearing in pixels (space before the ink starts).
    bearing_right:
        Right side-bearing in pixels (space after the ink ends).
    accepted:
        Whether this glyph has been approved for use in rendering.  Defaults
        to ``True``; the user can reject bad extractions via the API.
    issue:
        Why extraction held this glyph back for review (for example ink
        touching the box edge), or ``None`` when nothing looked wrong.
    strokes_json:
        The pen paths a plotter follows: a JSON list of strokes, each a list
        of ``[x, y]`` points in the mask's pixel coordinates (origin at its
        top-left corner, y down). ``None`` until a scanned glyph is traced.
    stroke_source:
        ``"drawn"`` when the strokes are the path the pen took on the Draw
        page, ``"traced"`` when they were traced through the middle of a
        scanned glyph's ink (see ``workers/tracing.py``), ``None`` when there
        are none yet.
    trace_version:
        Which version of the tracer made traced strokes, so they are traced
        again after it improves (``TRACE_VERSION`` in ``workers/tracing.py``).
    """

    __tablename__ = "glyphs"

    id: int = Column(Integer, primary_key=True, index=True)
    profile_id: int = Column(Integer, ForeignKey("profiles.id"), nullable=False, index=True)
    char: str = Column(String(8), nullable=False)
    variant_index: int = Column(Integer, nullable=False, default=0)
    mask_path: str = Column(String, nullable=False)
    svg_path: str = Column(String, nullable=True)
    width: float = Column(Float, nullable=False, default=0.0)
    height: float = Column(Float, nullable=False, default=0.0)
    baseline_offset: float = Column(Float, nullable=False, default=0.0)
    bearing_left: float = Column(Float, nullable=False, default=0.0)
    bearing_right: float = Column(Float, nullable=False, default=0.0)
    accepted: bool = Column(Boolean, nullable=False, default=True)
    issue: str = Column(String, nullable=True)
    strokes_json: str = Column(Text, nullable=True)
    stroke_source: str = Column(String(16), nullable=True)
    trace_version: int = Column(Integer, nullable=True)

    @property
    def has_strokes(self) -> bool:
        """Whether this glyph has pen paths, drawn or traced, so a plotter can write it."""
        return bool(self.strokes_json)

    def strokes(self) -> Optional[List[List[Tuple[float, float]]]]:
        """The decoded strokes, or ``None`` for a scanned glyph."""
        if not self.strokes_json:
            return None
        return [[(float(x), float(y)) for x, y in stroke] for stroke in json.loads(self.strokes_json)]
