"""Glyph ORM model.

Each Glyph represents one extracted instance of a single character from a
handwriting scan.  Multiple variants of the same character can exist for a
single profile (one per occurrence on the sheet).
"""
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String

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
