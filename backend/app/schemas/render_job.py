"""Pydantic schemas for the RenderJob and RenderBatch resources."""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# Upper bound on cards per batch; the course set is 10
MAX_BATCH_CARDS = 200


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


class BatchCardCreate(BaseModel):
    """One card of a batch: its merged text and seed."""

    label: str = Field(default="", max_length=200, description="Short name for the card, such as the recipient. Used in file names.")
    text: str = Field(..., min_length=1)
    seed: int = Field(default=0)


class RenderBatchCreate(BaseModel):
    """Request body for POST /profiles/{id}/render-batch."""

    settings: Dict[str, Any] = Field(default_factory=dict, description="Render settings shared by every card.")
    cards: List[BatchCardCreate] = Field(..., min_length=1, max_length=MAX_BATCH_CARDS)


class BatchCardRead(BatchCardCreate):
    """One card of a finished batch, with the characters it had no glyph for."""

    missing_chars: List[str] = Field(default_factory=list)


class RenderBatchRead(BaseModel):
    """Full RenderBatch representation returned by the API."""

    id: int
    profile_id: int
    settings_json: str
    cards: List[BatchCardRead] = Field(validation_alias="cards_json")
    status: str
    output_path: Optional[str] = None
    zip_path: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime

    _decode_cards = field_validator("cards", mode="before")(_json_list)

    class Config:
        from_attributes = True
