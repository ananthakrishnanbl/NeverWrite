"""Pydantic schemas for the Glyph resource."""
from typing import Optional

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

    class Config:
        from_attributes = True


class GlyphUpdate(BaseModel):
    """Partial update body for PATCH /glyphs/{id}.

    All fields are optional — only the provided fields are updated.
    """

    accepted: Optional[bool] = Field(default=None, description="Set to false to exclude glyph from renders.")
    bearing_left: Optional[float] = Field(default=None, description="Left side-bearing in pixels.")
    bearing_right: Optional[float] = Field(default=None, description="Right side-bearing in pixels.")
    baseline_offset: Optional[float] = Field(default=None, description="Distance from top of bounding box to baseline.")
