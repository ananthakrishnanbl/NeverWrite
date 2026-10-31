"""Router: /api/profiles

CRUD endpoints for handwriting Profile resources.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.profile import Profile
from app.models.glyph import Glyph
from app.schemas.profile import ProfileCreate, ProfileRead
from app.defaults import seed_profile_default_glyphs

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post(
    "",
    summary="Create a new handwriting profile",
    response_model=ProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db)) -> ProfileRead:
    """Create a new Profile record and seed it with default sample glyphs.

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

    # Seed profile with default sample glyphs so it is immediately usable
    glyph_count = seed_profile_default_glyphs(profile.id, db)

    result = ProfileRead.model_validate(profile)
    result.glyph_count = glyph_count
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
