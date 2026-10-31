"""FastAPI application factory — CORS, router registration, startup hook."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS, DATA_DIR
from app.database import init_db
from app.routers.templates import router as templates_router
from app.routers.profiles import router as profiles_router
from app.routers.scans import router as scans_router
from app.routers.glyphs import router as glyphs_router
from app.routers.renders import router as renders_router
from app.routers.jobs import router as jobs_router

# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="InkTwin API",
    description=(
        "Backend for InkTwin — a handwriting digitisation and synthetic "
        "handwriting generation system.  Upload scanned template sheets, "
        "review extracted glyphs, and render personalised handwritten text."
    ),
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


@app.on_event("startup")
def startup() -> None:
    """Initialise the database and ensure data directories exist on startup."""
    init_db()


# ---------------------------------------------------------------------------
# Static file mount — serves data/ directory for direct image/PDF access
# ---------------------------------------------------------------------------

# Mount lazily after startup so DATA_DIR exists
@app.on_event("startup")
def mount_static() -> None:
    """Mount the data directory as a static file server at /files."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(DATA_DIR)), name="files")


# ---------------------------------------------------------------------------
# Routers (all prefixed under /api)
# ---------------------------------------------------------------------------

API_PREFIX = "/api"

app.include_router(templates_router, prefix=API_PREFIX)
app.include_router(profiles_router, prefix=API_PREFIX)
app.include_router(scans_router, prefix=API_PREFIX)
app.include_router(glyphs_router, prefix=API_PREFIX)
app.include_router(renders_router, prefix=API_PREFIX)
app.include_router(jobs_router, prefix=API_PREFIX)


# ---------------------------------------------------------------------------
# Root health check
# ---------------------------------------------------------------------------


@app.get("/", tags=["health"])
def root() -> dict:
    """Health-check endpoint — returns the API name and version."""
    return {"name": "InkTwin API", "version": "0.1.0", "status": "ok"}
