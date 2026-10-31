"""Routers package — re-exports all router instances for convenient import."""
from app.routers.templates import router as templates_router  # noqa: F401
from app.routers.profiles import router as profiles_router  # noqa: F401
from app.routers.scans import router as scans_router  # noqa: F401
from app.routers.glyphs import router as glyphs_router  # noqa: F401
from app.routers.renders import router as renders_router  # noqa: F401
from app.routers.jobs import router as jobs_router  # noqa: F401
