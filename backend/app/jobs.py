"""In-memory job store backed by a plain dict protected with a threading lock.

Jobs are ephemeral — they live only for the lifetime of the server process.
For each long-running background task a job is created here so the client can
poll its progress without hitting the database.
"""
import threading
import uuid
from typing import Any, Callable, Dict, Optional


_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_job(job_type: str, meta: dict) -> str:
    """Create a new job entry and return its UUID string.

    Parameters
    ----------
    job_type:
        A short string identifying the kind of work (e.g. ``"extraction"``).
    meta:
        Arbitrary metadata dict stored alongside the job (e.g. profile_id).

    Returns
    -------
    str
        The newly assigned job ID.
    """
    jid = str(uuid.uuid4())
    with _lock:
        _jobs[jid] = {
            "id": jid,
            "type": job_type,
            "status": "pending",
            "progress": 0,
            "stage": "",
            "meta": meta,
            "error": None,
            "warning": None,
        }
    return jid


def update_job(jid: str, **kwargs: Any) -> None:
    """Update one or more fields of an existing job.

    Parameters
    ----------
    jid:
        Job ID returned by :func:`create_job`.
    **kwargs:
        Key-value pairs to merge into the job dict (e.g. ``status="running"``,
        ``progress=50``, ``stage="Extracting cells"``).
    """
    with _lock:
        if jid in _jobs:
            _jobs[jid].update(kwargs)


def get_job(jid: str) -> Optional[Dict[str, Any]]:
    """Return a shallow copy of the job dict, or ``None`` if not found.

    Parameters
    ----------
    jid:
        Job ID to look up.
    """
    with _lock:
        job = _jobs.get(jid)
        return dict(job) if job else None


def run_in_thread(fn: Callable, *args: Any) -> None:
    """Launch *fn* in a daemon thread, passing *args* positionally.

    The thread is daemonised so it does not prevent the interpreter from
    shutting down when the main process exits.

    Parameters
    ----------
    fn:
        The callable to run in a background thread.
    *args:
        Positional arguments forwarded to *fn*.
    """
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()
