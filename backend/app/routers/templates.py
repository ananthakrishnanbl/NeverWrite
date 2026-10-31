"""Router: /api/templates

Serves metadata about available handwriting-sheet templates.  In a future
release this router will also vend the downloadable PDF template files.
"""
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(prefix="/templates", tags=["templates"])

# ---------------------------------------------------------------------------
# Stub template catalogue — replace with DB or file-system discovery later
# ---------------------------------------------------------------------------

_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "sheet-1-lowercase",
        "sheet_number": 1,
        "name": "Sheet 1: Lowercase (a–z)",
        "description": "2 rows × 13 columns. Row 1 has a–m, Row 2 has n–z. Generous box size for natural handwriting.",
        "paper_size": "A4",
        "rows": 2,
        "columns": 13,
        "characters": "abcdefghijklmnopqrstuvwxyz",
        "download_url": None,
    },
    {
        "id": "sheet-2-uppercase",
        "sheet_number": 2,
        "name": "Sheet 2: Uppercase (A–Z)",
        "description": "2 rows × 13 columns. Row 1 has A–M, Row 2 has N–Z.",
        "paper_size": "A4",
        "rows": 2,
        "columns": 13,
        "characters": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "download_url": None,
    },
    {
        "id": "sheet-3-digits-punct",
        "sheet_number": 3,
        "name": "Sheet 3: Digits & Punctuation",
        "description": "3 rows × 10 columns. Digits 0–9 and common punctuation.",
        "paper_size": "A4",
        "rows": 3,
        "columns": 10,
        "characters": "0123456789.,!?;:'\"-()",
        "download_url": None,
    },
]


@router.get(
    "",
    summary="List available template sheets",
    response_model=List[Dict[str, Any]],
)
def list_templates() -> List[Dict[str, Any]]:
    """Return metadata for all available handwriting-sheet templates.

    Each entry describes the paper size, grid layout, and the set of
    characters the sheet covers.  The ``download_url`` field will be
    populated once PDF generation is implemented.
    """
    return _TEMPLATES
