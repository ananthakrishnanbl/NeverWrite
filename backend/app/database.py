"""SQLAlchemy engine, session factory, declarative base, and FastAPI dependency."""
import logging
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import DATA_DIR, DB_PATH, UPLOAD_DIR, GLYPH_DIR, EXPORT_DIR

logger = logging.getLogger(__name__)

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

# Columns that must exist on each table: {table: [(column, sql_type)]}
_MIGRATIONS: dict = {
    "render_jobs": [
        ("gcode_path", "TEXT"),
    ],
}


def _run_migrations() -> None:
    """Add any missing columns to existing tables (safe no-op if already present)."""
    with engine.connect() as conn:
        for table, columns in _MIGRATIONS.items():
            # Get existing columns via PRAGMA
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result}  # row[1] is the column name

            for col_name, col_type in columns:
                if col_name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                    )
                    conn.commit()
                    logger.info("Migration: added column %s.%s (%s)", table, col_name, col_type)
                else:
                    logger.debug("Migration: column %s.%s already exists", table, col_name)


def init_db() -> None:
    """Create all tables, run column migrations, and ensure data subdirectories exist."""
    # Import models so that Base knows about them before create_all
    from app.models import profile, glyph, render_job  # noqa: F401

    # Ensure the data directories exist
    for directory in (DATA_DIR, UPLOAD_DIR, GLYPH_DIR, EXPORT_DIR):
        Path(directory).mkdir(parents=True, exist_ok=True)

    # Create tables (no-op for tables that already exist)
    Base.metadata.create_all(bind=engine)

    # Add any missing columns to existing tables
    _run_migrations()
