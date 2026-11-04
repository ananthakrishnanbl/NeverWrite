"""Router: /api/profiles/{id}/glyphs, /api/profiles/{id}/drawn-glyphs and /api/glyphs/{id}

List, draw and update Glyph records belonging to a Profile.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.glyph import Glyph
from app.models.profile import Profile
from app.schemas.glyph import DrawnGlyphCreate, GlyphRead, GlyphUpdate
from workers.strokes import StrokeError, save_drawn_glyph

router = APIRouter(tags=["glyphs"])


@router.get(
    "/profiles/{profile_id}/glyphs",
    summary="List glyphs for a profile",
    response_model=List[GlyphRead],
)
def list_glyphs(
    profile_id: int,
    accepted: Optional[bool] = None,
    db: Session = Depends(get_db),
) -> List[GlyphRead]:
    """Return all Glyph records for the given Profile.

    Parameters
    ----------
    profile_id:
        ID of the owning Profile.
    accepted:
        Optional filter.  Pass ``true`` to return only accepted glyphs, or
        ``false`` for rejected ones.  Omit to return all.
    db:
        Injected database session.

    Raises
    ------
    HTTPException
        404 if the Profile does not exist.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")

    query = db.query(Glyph).filter(Glyph.profile_id == profile_id)
    if accepted is not None:
        query = query.filter(Glyph.accepted == accepted)
    return query.order_by(Glyph.char, Glyph.variant_index).all()


@router.post(
    "/profiles/{profile_id}/drawn-glyphs",
    summary="Save a character drawn in the browser",
    response_model=GlyphRead,
    status_code=status.HTTP_201_CREATED,
)
def create_drawn_glyph(
    profile_id: int,
    body: DrawnGlyphCreate,
    db: Session = Depends(get_db),
) -> GlyphRead:
    """Store one variant of a character from the strokes drawn on the Draw page.

    Replaces whatever that character's slot held, drawn or scanned, and keeps
    the strokes so a pen plotter can write the character.

    Raises
    ------
    HTTPException
        404 if the Profile does not exist, 422 if the drawing can't be used.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")
    try:
        return save_drawn_glyph(db, profile_id, body.char, body.variant_index, body.strokes)
    except StrokeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch(
    "/glyphs/{glyph_id}",
    summary="Update glyph metadata",
    response_model=GlyphRead,
)
def update_glyph(
    glyph_id: int,
    body: GlyphUpdate,
    db: Session = Depends(get_db),
) -> GlyphRead:
    """Partially update a Glyph record.

    Only fields explicitly provided in the request body are updated; omitted
    fields retain their current value.

    Parameters
    ----------
    glyph_id:
        Primary key of the Glyph to update.
    body:
        Partial update payload.
    db:
        Injected database session.

    Raises
    ------
    HTTPException
        404 if the Glyph does not exist.
    """
    glyph = db.query(Glyph).filter(Glyph.id == glyph_id).first()
    if glyph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Glyph {glyph_id} not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(glyph, field, value)

    db.commit()
    db.refresh(glyph)
    return glyph
