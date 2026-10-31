"""Router: /api/profiles

CRUD endpoints for handwriting Profile resources.
"""
import shutil
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import GLYPH_DIR, UPLOAD_DIR
from app.database import get_db
from app.models.profile import Profile
from app.models.glyph import Glyph
from app.models.render_batch import RenderBatch
from app.models.render_job import RenderJob
from app.schemas.profile import ProfileCreate, ProfileRead
from app.storage import get_file_path

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post(
    "",
    summary="Create a new handwriting profile",
    response_model=ProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db)) -> ProfileRead:
    """Create a new, empty Profile record.

    Glyphs only ever come from the person's own scans. Missing characters stay
    missing (the composer leaves a gap) instead of falling back to a font,
    which would put typeset letters into supposedly handwritten text.

    Parameters
    ----------
    body:
        JSON body containing the ``name`` field.
    db:
        Injected database session.
    """
    profile = Profile(name=body.name)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    result = ProfileRead.model_validate(profile)
    result.glyph_count = 0
    return result


@router.get(
    "",
    summary="List all handwriting profiles",
    response_model=List[ProfileRead],
)
def list_profiles(db: Session = Depends(get_db)) -> List[ProfileRead]:
    """Return a list of all Profile records, ordered by creation date descending.

    Parameters
    ----------
    db:
        Injected database session.
    """
    profiles = db.query(Profile).order_by(Profile.created_at.desc()).all()
    results: List[ProfileRead] = []
    for profile in profiles:
        glyph_count = db.query(Glyph).filter(Glyph.profile_id == profile.id).count()
        read = ProfileRead.model_validate(profile)
        read.glyph_count = glyph_count
        results.append(read)
    return results


@router.get(
    "/{profile_id}",
    summary="Get a single handwriting profile",
    response_model=ProfileRead,
)
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> ProfileRead:
    """Return a single Profile by its ID, including the total glyph count.

    Parameters
    ----------
    profile_id:
        Primary key of the Profile to retrieve.
    db:
        Injected database session.

    Raises
    ------
    HTTPException
        404 if no Profile with the given ID exists.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")
    glyph_count = db.query(Glyph).filter(Glyph.profile_id == profile_id).count()
    read = ProfileRead.model_validate(profile)
    read.glyph_count = glyph_count
    return read


@router.delete(
    "/{profile_id}",
    summary="Delete a profile and all of its handwriting data",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_profile(profile_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a Profile, its glyphs, render jobs and batches, and every file they own.

    Removes the uploaded sheet photos, the glyph masks and any exported PDFs,
    since handwriting samples are personal data.

    Raises
    ------
    HTTPException
        404 if no Profile with the given ID exists.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")

    for render_job in db.query(RenderJob).filter(RenderJob.profile_id == profile_id):
        if render_job.output_path:
            get_file_path(render_job.output_path).unlink(missing_ok=True)
    db.query(RenderJob).filter(RenderJob.profile_id == profile_id).delete()
    for batch in db.query(RenderBatch).filter(RenderBatch.profile_id == profile_id):
        for rel_path in (batch.output_path, batch.zip_path):
            if rel_path:
                get_file_path(rel_path).unlink(missing_ok=True)
    db.query(RenderBatch).filter(RenderBatch.profile_id == profile_id).delete()
    db.query(Glyph).filter(Glyph.profile_id == profile_id).delete()
    db.delete(profile)
    db.commit()

    shutil.rmtree(GLYPH_DIR / str(profile_id), ignore_errors=True)
    for upload in UPLOAD_DIR.glob(f"profile_{profile_id}_sheet_*"):
        upload.unlink(missing_ok=True)
