"""SQLAlchemy engine, session factory, declarative base, and FastAPI dependency."""
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import DATA_DIR, DB_PATH, UPLOAD_DIR, GLYPH_DIR, EXPORT_DIR

# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session and ensure it is closed after use."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Initialisation helper called at startup
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables and required data subdirectories if they don't exist."""
    # Import models so that Base knows about them before create_all
    from app.models import profile, glyph, render_job  # noqa: F401

    # Ensure the data directories exist
    for directory in (DATA_DIR, UPLOAD_DIR, GLYPH_DIR, EXPORT_DIR):
        Path(directory).mkdir(parents=True, exist_ok=True)

    # Create tables
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Add columns introduced after a database was created.

    ``create_all`` only creates missing tables, so an existing data/inktwin.db
    needs new nullable columns added by hand.
    """
    with engine.begin() as conn:
        glyph_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(glyphs)")}
        if "issue" not in glyph_cols:
            conn.exec_driver_sql("ALTER TABLE glyphs ADD COLUMN issue VARCHAR")
