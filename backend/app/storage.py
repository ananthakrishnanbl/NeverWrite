"""Helpers for persisting files to the local data directory.

All paths stored in the database are *relative* to DATA_DIR so the project
remains portable if the data directory is moved.
"""
from pathlib import Path

from app.config import DATA_DIR, UPLOAD_DIR, GLYPH_DIR


def save_upload(data: bytes, filename: str, subdir: str = "uploads") -> str:
    """Save raw bytes to ``DATA_DIR/<subdir>/<filename>`` and return the relative path.

    Parameters
    ----------
    data:
        Raw file bytes to write.
    filename:
        Target filename (basename only; directory traversal is stripped).
    subdir:
        Sub-directory under DATA_DIR, defaults to ``"uploads"``.

    Returns
    -------
    str
        Relative path from DATA_DIR, e.g. ``"uploads/scan_001.png"``.
    """
    safe_name = Path(filename).name  # strip any directory components
    target_dir = DATA_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / safe_name
    dest.write_bytes(data)
    # Always return forward slashes for URLs and browser compatibility
    return f"{subdir}/{safe_name}"


def save_glyph_mask(data: bytes, profile_id: int, char: str, variant: int) -> str:
    """Save a glyph mask PNG and return its relative path.

    Files are stored at ``DATA_DIR/glyphs/<profile_id>/<case_prefix>_<char>_<variant>.png``.
    On Windows NTFS, filenames are case-insensitive, so we prefix with
    ``lower_``, ``upper_``, or ``char_<hex>`` to prevent uppercase and lowercase
    glyphs (e.g. 'A' and 'a') from overwriting each other.

    Parameters
    ----------
    data:
        PNG bytes of the glyph mask.
    profile_id:
        Owning profile's primary key.
    char:
        The single character this glyph represents.
    variant:
        Zero-based variant index.

    Returns
    -------
    str
        Relative path from DATA_DIR with forward slashes, e.g. ``"glyphs/1/lower_a_0.png"``.
    """
    if char.isupper():
        char_key = f"upper_{char}"
    elif char.islower():
        char_key = f"lower_{char}"
    elif char.isalnum():
        char_key = f"digit_{char}"
    else:
        char_key = f"char_{ord(char):04x}"

    subdir = Path("glyphs") / str(profile_id)
    target_dir = DATA_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{char_key}_{variant}.png"
    dest = target_dir / filename
    dest.write_bytes(data)
    # Return forward slashes for web serving
    return f"glyphs/{profile_id}/{filename}"


def get_file_path(relative_path: str) -> Path:
    """Resolve a relative (to DATA_DIR) path to an absolute :class:`~pathlib.Path`.

    Parameters
    ----------
    relative_path:
        A path string as stored in the database, e.g. ``"exports/job_42.pdf"``.

    Returns
    -------
    pathlib.Path
        Absolute path on the local filesystem.
    """
    return DATA_DIR / relative_path
