import os
import logging
from typing import Dict

from fastapi import FastAPI, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# -------------------------------------------------------------
# Logging Configuration
# -------------------------------------------------------------
logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

# -------------------------------------------------------------
# FastAPI App Initialization (with global prefix /api/v1)
# -------------------------------------------------------------
app = FastAPI(
    title="Google Business API",
    version="v1",
)

# -------------------------------------------------------------
# CORS Middleware
# -------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://192.168.13.46:5173",
        "http://192.168.13.46:3000",
        "http://localhost:3000",
        "http://localhost:5173",
        "https://smm-dev.poorvika.workers.dev",
        "https://dev-soc-media.poorvika.in",
        "https://soc-media.poorvika.in",
        ####
        "https://dev-soc-media.poorvika.com",
        "https://stage-soc-media.poorvika.com",
        "https://soc-media.poorvika.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)

# -------------------------------------------------------------
# Security Header Middleware
# -------------------------------------------------------------
class ReferrerPolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(ReferrerPolicyMiddleware)

# -------------------------------------------------------------
# Health Check Endpoint
# -------------------------------------------------------------
@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    """Health check endpoint."""
    settings = getattr(app.state, "settings", None)

    if settings is None:
        return {"status": "error", "detail": "settings not loaded"}

    try:
        from app.connections import db
        result = db.execute_query("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        logger.exception("Health check database query failed")
        return {"status": "error", "detail": str(e)}

# OPTIONS Handler - Handle CORS preflight globally
# -------------------------------------------------------
@app.options("/{full_path:path}")
async def preflight_handler(full_path: str) -> Dict[str, str]:
    """Handle CORS preflight requests globally."""
    return {}

# -------------------------------------------------------------
# Startup Event
# -------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """
    Initialize application resources on startup:
    - Load configuration settings
    - Set up PlanetScale database connection
    - Include all routers once
    """
    global logger

    # Load application settings
    try:
        from app.config import load_settings  # type: ignore
    except Exception:
        logger.exception("Failed to import app.config.load_settings")
        app.state.settings = None
        app.state.routers_included = False
        return

    settings, errors = load_settings()
    if errors:
        logger.error("Configuration errors during startup:")
        for e in errors:
            logger.error("  - %s", e)
        app.state.settings = None
        app.state.routers_included = False
        return

    app.state.settings = settings

    # Apply dynamic log level
    try:
        level = getattr(settings, "LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO"))
        numeric_level = getattr(logging, level.upper(), None)
        if isinstance(numeric_level, int):
            logging.getLogger().setLevel(numeric_level)
            logger.info("Log level set to %s", level)
    except Exception:
        logger.exception("Unable to set log level from settings")

    # Initialize PlanetScale database
    try:
        from app.connections import init_db  # type: ignore
        await init_db()
        logger.info("✅ PlanetScale database initialized")
    except Exception:
        logger.exception("Failed to initialize PlanetScale database")
        return

    # Include Routers (with /api/v1 prefix automatically applied)
    try:
        if not getattr(app.state, "routers_included", False):
            from app.routers import router as main_router  # type: ignore
            from app.metrics_router import router as metrics_router  # type: ignore
            from app.event_post_router import router as event_post_router  # type: ignore
            from app.utils_review_gem import router as gemini_router  # type: ignore
            from app.regions import router as regions_router  # type: ignore
            from app.reviews import router as review_router
            from app.auth import router as auth_router

            # ✅ Routers will inherit the /api/v1 prefix automatically
            app.include_router(main_router,prefix="/api/v1")
            app.include_router(metrics_router,prefix="/api/v1")
            app.include_router(event_post_router,prefix="/api/v1")
            app.include_router(gemini_router,prefix="/api/v1")
            app.include_router(regions_router,prefix="/api/v1")
            app.include_router(review_router,prefix="/api/v1")
            app.include_router(auth_router,prefix="/api/v1")
            app.state.routers_included = True
            logger.info("All routers included successfully")
        else:
            logger.info("Routers already included; skipping inclusion")
    except Exception:
        logger.exception("Failed to import/include one or more routers; some endpoints may be unavailable")

# -------------------------------------------------------------
# Root Endpoint (optional convenience)
# -------------------------------------------------------------
@app.get("/")
async def root():
    """Base route to confirm API availability."""
    return {"message": "Google Business API is running under /api/v1"}
