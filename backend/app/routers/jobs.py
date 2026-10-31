"""Router: /api/jobs/{id}

Exposes the in-memory job store so clients can poll background task progress.
"""
from fastapi import APIRouter, HTTPException, status
from typing import Any, Dict

from app import jobs as job_store

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    summary="Get background job status",
    response_model=Dict[str, Any],
)
def get_job(job_id: str) -> Dict[str, Any]:
    """Return the current state of a background job.

    Parameters
    ----------
    job_id:
        UUID string returned when the job was created.

    Returns
    -------
    dict
        Keys: ``id``, ``type``, ``status``, ``progress`` (0–100),
        ``stage`` (human-readable stage label), ``meta``, ``error``.

    Raises
    ------
    HTTPException
        404 if no job with the given ID exists in the in-memory store.
    """
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found.")
    return job
