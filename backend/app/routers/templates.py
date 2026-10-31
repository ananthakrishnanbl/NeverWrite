"""Router: /api/templates

Describes the three handwriting capture sheets and serves each one as a
printable PDF.
"""
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Response, status

from workers.template import (
    GROUPS_PER_ROW,
    SHEET_CONFIGS,
    VARIANTS,
    build_template_pdf,
    sheet_name,
    template_filename,
)

router = APIRouter(prefix="/templates", tags=["templates"])


def _describe(sheet_number: int) -> Dict[str, Any]:
    charset = SHEET_CONFIGS[sheet_number]["charset"]
    return {
        "id": f"sheet-{sheet_number}",
        "sheet_number": sheet_number,
        "name": f"Sheet {sheet_number}: {sheet_name(sheet_number)}",
        "description": (
            f"{len(charset)} characters, {VARIANTS} boxes each, "
            f"{GROUPS_PER_ROW} characters per row on plain A4."
        ),
        "paper_size": "A4",
        "variants": VARIANTS,
        "characters": charset,
        "download_url": f"/api/templates/{sheet_number}/pdf",
    }


@router.get(
    "",
    summary="List available template sheets",
    response_model=List[Dict[str, Any]],
)
def list_templates() -> List[Dict[str, Any]]:
    """Return metadata for every capture sheet, including its PDF download URL."""
    return [_describe(n) for n in sorted(SHEET_CONFIGS)]


@router.get(
    "/{sheet_number}/pdf",
    summary="Download a printable capture sheet",
    response_class=Response,
)
def download_template(sheet_number: int) -> Response:
    """Return the A4 PDF for one sheet: corner markers, three boxes per
    character, and light cyan guides the extractor removes by colour.

    Raises
    ------
    HTTPException
        404 if there is no sheet with that number.
    """
    if sheet_number not in SHEET_CONFIGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Sheet {sheet_number} not found.")
    return Response(
        content=build_template_pdf(sheet_number),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{template_filename(sheet_number)}"'},
    )
