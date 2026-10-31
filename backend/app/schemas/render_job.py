"""Pydantic schemas for the RenderJob resource."""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RenderJobCreate(BaseModel):
    """Request body for POST /profiles/{id}/render."""

    text: str = Field(..., min_length=1, description="Plain text to render in the target handwriting style.")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Render settings in the frontend RenderSettings shape (font_size, line_spacing, ink_color, margin_*, messiness, slant_correction).")
    seed: int = Field(default=0, description="PRNG seed for reproducible renders.")


class RenderJobRead(BaseModel):
    """Full RenderJob representation returned by the API."""

    id: int
    profile_id: int
    text: str
    settings_json: str
    seed: int
    status: str
    output_path: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
