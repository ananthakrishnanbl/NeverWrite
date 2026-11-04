"""Pydantic schemas for the Glyph resource."""
from typing import List, Optional

from pydantic import BaseModel, Field


class GlyphRead(BaseModel):
    """Full Glyph representation returned by the API."""

    id: int
    profile_id: int
    char: str
    variant_index: int
    mask_path: str
    svg_path: Optional[str] = None
    width: float
    height: float
    baseline_offset: float
    bearing_left: float
    bearing_right: float
    accepted: bool
    issue: Optional[str] = None
    has_strokes: bool = Field(default=False, description="True for a glyph drawn on the Draw page, which a pen plotter can write.")

    class Config:
        from_attributes = True


class GlyphUpdate(BaseModel):
    """Partial update body for PATCH /glyphs/{id}.

    All fields are optional — only the provided fields are updated.
    """

    accepted: Optional[bool] = Field(default=None, description="Set to false to exclude glyph from renders.")
    bearing_left: Optional[float] = Field(default=None, description="Left side-bearing in pixels.")
    bearing_right: Optional[float] = Field(default=None, description="Right side-bearing in pixels.")
    baseline_offset: Optional[float] = Field(default=None, description="Pixels the bottom of the mask sits below the baseline (negative = above).")


class DrawnGlyphCreate(BaseModel):
    """Body for POST /profiles/{id}/drawn-glyphs: one character drawn on the Draw page."""

    char: str = Field(..., min_length=1, max_length=1, description="The character drawn.")
    variant_index: int = Field(..., ge=0, description="Which of the character's slots to fill (0-based).")
    strokes: List[List[List[float]]] = Field(
        ...,
        description="Strokes in millimetres of the 18 x 21 mm writing box, y down: a list of strokes, "
                    "each a list of [x, y] points in the order the pen moved. One point is a dot.",
    )
