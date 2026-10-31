"""Router: /api/profiles/{id}/scans

Accepts handwriting sheet uploads, persists them to disk, and kicks off the
background extraction pipeline.
"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app import jobs as job_store
from app.database import SessionLocal, get_db
from app.models.profile import Profile
from app.storage import save_upload
from workers.extraction import run_extraction

router = APIRouter(tags=["scans"])


@router.post(
    "/profiles/{profile_id}/scans",
    summary="Upload a scan sheet and start glyph extraction",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_scan(
    profile_id: int,
    file: UploadFile = File(..., description="Scanned handwriting sheet image (PNG/JPEG/TIFF)."),
    sheet_number: int = Form(default=1, description="Sheet number (1-based) for multi-sheet profiles."),
    db: Session = Depends(get_db),
) -> dict:
    """Save an uploaded scan and launch the extraction pipeline in a background thread.

    Parameters
    ----------
    profile_id:
        ID of the Profile this scan belongs to.
    file:
        Uploaded image file.
    sheet_number:
        Which sheet of the template this scan corresponds to.
    db:
        Injected database session.

    Returns
    -------
    dict
        ``{"job_id": str}`` — poll ``GET /api/jobs/{job_id}`` for progress.

    Raises
    ------
    HTTPException
        404 if the Profile does not exist.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")

    # Persist the uploaded file
    raw = await file.read()
    safe_filename = f"profile_{profile_id}_sheet_{sheet_number}_{file.filename}"
    relative_path = save_upload(raw, safe_filename, subdir="uploads")

    # Create an in-memory job entry
    job_id = job_store.create_job(
        job_type="extraction",
        meta={"profile_id": profile_id, "sheet_number": sheet_number, "scan_path": relative_path},
    )

    # Launch extraction in a background daemon thread
    job_store.run_in_thread(
        run_extraction,
        job_id,
        relative_path,
        profile_id,
        sheet_number,
        SessionLocal,
    )

    return {"job_id": job_id}
