"""Application configuration — base directories, database path, and CORS origins."""
from pathlib import Path

# Root of the backend package (one level above this file)
BASE_DIR: Path = Path(__file__).parent.parent

# All persistent data lives under data/
DATA_DIR: Path = BASE_DIR / "data"
DB_PATH: Path = DATA_DIR / "neverwrite.db"
UPLOAD_DIR: Path = DATA_DIR / "uploads"
GLYPH_DIR: Path = DATA_DIR / "glyphs"
EXPORT_DIR: Path = DATA_DIR / "exports"

# Origins allowed by the CORS middleware
CORS_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
