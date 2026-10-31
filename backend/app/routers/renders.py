"""Router: /api/profiles/{id}/render, /api/renders/{id}, /api/renders/{id}/file,
/api/profiles/{id}/render-batch, /api/render-batches/{id}[/file|/zip]

Create render jobs and mail-merge batches, and retrieve their status / output files.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import jobs as job_store
from app.database import SessionLocal, get_db
from app.models.profile import Profile
from app.models.render_batch import RenderBatch
from app.models.render_job import RenderJob
from app.schemas.render_job import RenderBatchCreate, RenderBatchRead, RenderJobCreate, RenderJobRead
from app.storage import get_file_path
from workers.renderer import run_batch, run_render

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
    return _output_file(render_job.status, render_job.output_path, f"RenderJob {render_id}", "application/pdf")


@router.post(
    "/profiles/{profile_id}/render-batch",
    summary="Submit a mail-merge batch of cards",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_batch(
    profile_id: int,
    body: RenderBatchCreate,
    db: Session = Depends(get_db),
) -> dict:
    """Create a RenderBatch and render every card in a background thread.

    The client merges the template with each CSV row and sends finished card
    texts, each with its own seed, so the server never needs the CSV.

    Returns
    -------
    dict
        ``{"job_id": str, "batch_id": int}``

    Raises
    ------
    HTTPException
        404 if the Profile does not exist.
    """
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile {profile_id} not found.")

    batch = RenderBatch(
        profile_id=profile_id,
        settings_json=json.dumps(body.settings),
        cards_json=json.dumps([card.model_dump() for card in body.cards]),
        status="pending",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    job_id = job_store.create_job(
        job_type="render_batch",
        meta={"profile_id": profile_id, "batch_id": batch.id},
    )
    job_store.run_in_thread(run_batch, job_id, batch.id, SessionLocal)

    return {"job_id": job_id, "batch_id": batch.id}


@router.get(
    "/render-batches/{batch_id}",
    summary="Get batch status and its cards",
    response_model=RenderBatchRead,
)
def get_batch(batch_id: int, db: Session = Depends(get_db)) -> RenderBatchRead:
    """Return the RenderBatch identified by *batch_id*, with each card's text, seed and missing characters."""
    return _batch_or_404(batch_id, db)


@router.get(
    "/render-batches/{batch_id}/file",
    summary="Download the PDF with every card",
)
def get_batch_file(batch_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream the combined PDF of a finished batch, one card per page."""
    batch = _batch_or_404(batch_id, db)
    return _output_file(batch.status, batch.output_path, f"RenderBatch {batch_id}", "application/pdf")


@router.get(
    "/render-batches/{batch_id}/zip",
    summary="Download a zip with one PDF per card",
)
def get_batch_zip(batch_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream the zip of single-card PDFs of a finished batch."""
    batch = _batch_or_404(batch_id, db)
    return _output_file(batch.status, batch.zip_path, f"RenderBatch {batch_id}", "application/zip")


def _batch_or_404(batch_id: int, db: Session) -> RenderBatch:
    """Load a RenderBatch or raise 404."""
    batch = db.query(RenderBatch).filter(RenderBatch.id == batch_id).first()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"RenderBatch {batch_id} not found.")
    return batch


def _output_file(job_status: str, rel_path: str | None, what: str, media_type: str) -> FileResponse:
    """Serve a finished job's output file.

    Raises
    ------
    HTTPException
        409 if the job has not finished successfully.
        404 if the file is missing on disk.
    """
    if job_status != "done" or rel_path is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{what} is not complete yet (status={job_status}).",
        )

    abs_path = get_file_path(rel_path)
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output file not found on disk; it may have been deleted.",
        )

    return FileResponse(path=str(abs_path), media_type=media_type, filename=abs_path.name)
