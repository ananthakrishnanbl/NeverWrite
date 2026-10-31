"""Pydantic schemas for the RenderJob resource."""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

def _json_list(value: Any) -> Any:
    """Decode a JSON-encoded list column; ``None`` becomes an empty list."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value


class RenderJobCreate(BaseModel):
    """Request body for POST /profiles/{id}/render."""

    text: str = Field(..., min_length=1, description="Plain text to render in the target handwriting style.")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Render settings in the frontend RenderSettings shape (page_size, page_width_mm, page_height_mm, align, font_size, line_spacing, ink_color, margin_*, messiness, slant_correction).")
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
    missing_chars: List[str] = Field(default_factory=list, description="Characters drawn as gaps because the profile has no accepted glyph for them.")
    created_at: datetime

    _decode_missing = field_validator("missing_chars", mode="before")(_json_list)

    class Config:
        from_attributes = True
