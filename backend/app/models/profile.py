"""Profile ORM model.

A Profile represents a single person's handwriting style.  It stores the
computed typographic metrics derived from one or more scanned sheets.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


class Profile(Base):
    """Handwriting profile with aggregate typographic metrics.

    Attributes
    ----------
    id:
        Auto-incrementing primary key.
    name:
        Human-readable label given by the user (e.g. ``"Alice"``).
    created_at:
        UTC timestamp of record creation.
    metrics_json:
        JSON-serialised dict containing computed metrics:
        ``x_height``, ``stroke_w``, ``slant``, ``avg_char_gap``,
        ``avg_word_gap``.  Empty string until at least one scan has been
        fully processed.
    """

    __tablename__ = "profiles"

    id: int = Column(Integer, primary_key=True, index=True)
    name: str = Column(String, nullable=False)
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    metrics_json: str = Column(String, nullable=False, default="{}")
