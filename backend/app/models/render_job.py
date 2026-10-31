"""RenderJob ORM model.

A RenderJob tracks a single request to render a piece of text using a
particular Profile.  Its status progresses from ``pending`` → ``running`` →
``done`` (or ``error``).
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database import Base


class RenderJob(Base):
    """Persistent record of a handwriting-render job.

    Attributes
    ----------
    id:
        Auto-incrementing primary key.
    profile_id:
        Foreign key referencing the :class:`~app.models.profile.Profile` whose
        glyphs will be used for rendering.
    text:
        The plain-text string to render.
    settings_json:
        JSON-serialised render settings (``font_size``, ``line_spacing``,
        ``ink_color``, ``margins``, ``paper_type``).
    seed:
        Integer seed for the PRNG used during variant selection / jitter,
        enabling reproducible renders.
    status:
        One of ``"pending"``, ``"running"``, ``"done"``, ``"error"``.
    output_path:
        Relative path (from DATA_DIR) to the generated PDF once complete.
        ``None`` until the job finishes successfully.
    error:
        Human-readable error message populated when ``status == "error"``.
    missing_chars:
        JSON list of characters in ``text`` with no accepted glyph, which the
        render leaves as gaps. ``None`` until the layout has run.
    created_at:
        UTC timestamp of record creation.
    """

    __tablename__ = "render_jobs"

    id: int = Column(Integer, primary_key=True, index=True)
    profile_id: int = Column(Integer, ForeignKey("profiles.id"), nullable=False, index=True)
    text: str = Column(String, nullable=False)
    settings_json: str = Column(String, nullable=False, default="{}")
    seed: int = Column(Integer, nullable=False, default=0)
    status: str = Column(String(16), nullable=False, default="pending")
    output_path: str = Column(String, nullable=True)
    error: str = Column(String, nullable=True)
    missing_chars: str = Column(String, nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
