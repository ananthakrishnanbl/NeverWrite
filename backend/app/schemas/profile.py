"""Pydantic schemas for the Profile resource."""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ProfileCreate(BaseModel):
    """Request body for creating a new Profile."""

    name: str = Field(..., min_length=1, max_length=128, description="Display name for this handwriting profile.")


class ProfileRead(BaseModel):
    """Full Profile representation returned by the API."""

    id: int
    name: str
    created_at: datetime
    metrics_json: str = Field(default="{}", description="JSON blob of typographic metrics.")
    glyph_count: Optional[int] = Field(default=None, description="Number of glyphs associated with this profile (populated on detail endpoint).")

    class Config:
        from_attributes = True
