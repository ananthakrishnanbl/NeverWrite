"""RenderBatch ORM model.

A RenderBatch is one mail-merge export: the same settings applied to a list
of cards, each with its own text and seed. Its status progresses from
``pending`` → ``running`` → ``done`` (or ``error``).
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base


class RenderBatch(Base):
    """Persistent record of a batch of cards rendered together.

    Attributes
    ----------
    id:
        Auto-incrementing primary key.
    profile_id:
        Foreign key referencing the :class:`~app.models.profile.Profile` whose
        glyphs will be used for rendering.
    settings_json:
        JSON-serialised render settings shared by every card.
    cards_json:
        JSON list of ``{"label", "text", "seed"}`` objects, one per card.
        The renderer adds ``missing_chars`` to each. Text and seed are enough
        to regenerate any card exactly.
    status:
        One of ``"pending"``, ``"running"``, ``"done"``, ``"error"``.
    output_path:
        Relative path (from DATA_DIR) to the PDF with every card.
    zip_path:
        Relative path (from DATA_DIR) to the zip with one PDF per card.
    error:
        Human-readable error message populated when ``status == "error"``.
    created_at:
        UTC timestamp of record creation.
    """

    __tablename__ = "render_batches"

    id: int = Column(Integer, primary_key=True, index=True)
    profile_id: int = Column(Integer, ForeignKey("profiles.id"), nullable=False, index=True)
    settings_json: str = Column(String, nullable=False, default="{}")
    cards_json: str = Column(String, nullable=False, default="[]")
    status: str = Column(String(16), nullable=False, default="pending")
    output_path: str = Column(String, nullable=True)
    zip_path: str = Column(String, nullable=True)
    error: str = Column(String, nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
