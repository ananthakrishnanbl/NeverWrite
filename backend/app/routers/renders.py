"""Router: /api/profiles/{id}/render, /api/renders/{id}, /api/renders/{id}/file

Create render jobs and retrieve their status / output files.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import jobs as job_store
from app.database import SessionLocal, get_db
from app.models.profile import Profile
from app.models.render_job import RenderJob
from app.schemas.render_job import RenderJobCreate, RenderJobRead
from app.storage import get_file_path
from workers.renderer import run_render

router = APIRouter(tags=["renders"])


@router.post(
    "/profiles/{profile_id}/render",
    summary="Submit a new render job",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_render(
    profile_id: int,
    body: RenderJobCreate,
    db: Session = Depends(get_db),
) -> dict:
    """Create a RenderJob record and launch the renderer in a background thread.

    Parameters
    ----------
    profile_id:
        ID of the Profile whose glyphs will be used.
    body:
        Render parameters: ``text``, ``settings`` dict, and ``seed``.
    db:
        Injected database session.

    Returns
    -------
    dict
        ``{"job_id": str, "render_id": int}``

    Raises
    ------
    HTTPException
        404 if the Profile does not exist.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")

    # Persist the render job in the database
    render_job = RenderJob(
        profile_id=profile_id,
        text=body.text,
        settings_json=json.dumps(body.settings),
        seed=body.seed,
        status="pending",
    )
    db.add(render_job)
    db.commit()
    db.refresh(render_job)

    # Create in-memory progress entry
    job_id = job_store.create_job(
        job_type="render",
        meta={"profile_id": profile_id, "render_id": render_job.id},
    )

    # Launch renderer in background
    job_store.run_in_thread(run_render, job_id, render_job.id, SessionLocal)

    return {"job_id": job_id, "render_id": render_job.id}


@router.get(
    "/renders/{render_id}",
    summary="Get render job status and metadata",
    response_model=RenderJobRead,
)
def get_render(render_id: int, db: Session = Depends(get_db)) -> RenderJobRead:
    """Return the RenderJob record identified by *render_id*.

    Parameters
    ----------
    render_id:
        Primary key of the RenderJob.
    db:
        Injected database session.

    Raises
    ------
    HTTPException
        404 if the RenderJob does not exist.
    """
    render_job = db.query(RenderJob).filter(RenderJob.id == render_id).first()
    if render_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"RenderJob {render_id} not found.")
    return render_job


@router.get(
    "/renders/{render_id}/file",
    summary="Download the rendered PDF",
)
def get_render_file(render_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream the output PDF for a completed render job.

    Parameters
    ----------
    render_id:
        Primary key of the RenderJob.
    db:
        Injected database session.

    Raises
    ------
    HTTPException
        404 if the RenderJob does not exist or has no output yet.
        409 if the job has not finished successfully.
    """
    render_job = db.query(RenderJob).filter(RenderJob.id == render_id).first()
    if render_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"RenderJob {render_id} not found.")
    if render_job.status != "done" or render_job.output_path is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RenderJob {render_id} is not complete yet (status={render_job.status}).",
        )

    abs_path = get_file_path(render_job.output_path)
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output file not found on disk — it may have been deleted.",
        )

    return FileResponse(
        path=str(abs_path),
        media_type="application/pdf",
        filename=abs_path.name,
    )
