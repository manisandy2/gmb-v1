
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow

from app.config import settings
# from app.connections import save_google_token, get_google_credentials
from app.db.token_repo import save_google_token, get_google_credentials

from app.services.auth_service import (
    get_user_profile,
    lookup_location_metadata,
)



logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------- FastAPI app --------------------
router = APIRouter()

# -------------------- In-memory OAuth state --------------------
oauth_state_store: Dict[str, str] = {}

OAUTH_SCOPES = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/business.manage",

    ]
# -------------------- OAuth Routes --------------------
@router.get("/auth/google")
async def login_google() -> RedirectResponse:
    """Start Google OAuth flow and redirect the user to consent screen."""
    try:
        flow = Flow.from_client_secrets_file(
            settings.GOOGLE_CREDENTIALS_FILE,
            scopes=OAUTH_SCOPES,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent"
        )
        oauth_state_store[state] = "init"
        # logger.info("OAuth flow initiated - redirecting to Google (state: %s)", state[:10])
        return RedirectResponse(url=auth_url)
    except FileNotFoundError as exc:
        # logger.error("Google credentials file not found: %s", exc)
        raise HTTPException(status_code=500, detail="Server configuration error - credentials file missing")
    except Exception as exc:
        # logger.exception("Failed to create OAuth flow: %s", exc)
        raise HTTPException(status_code=500, detail="Server error creating OAuth flow")


@router.get("/auth/google/callback")
async def auth_callback(request: Request) -> RedirectResponse:
    """Handle OAuth callback, exchange code for tokens, and redirect to frontend."""
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    logger.info("OAuth callback hit. state=%s error=%s url=%s", state, error, request.url)

    # 1️⃣ User denied access
    if error:
        if state:
            oauth_state_store.pop(state, None)
        return RedirectResponse(url=f"{settings.FRONTEND_URL}?auth=denied")

    # 2️⃣ Invalid or missing state
    if not state or state not in oauth_state_store:
        logger.warning("Invalid OAuth state: %s", state)
        return RedirectResponse(url=f"{settings.FRONTEND_URL}?auth=error&reason=invalid_state")

    try:
        # 3️⃣ Initialize OAuth flow with same redirect URI used earlier
        flow = Flow.from_client_secrets_file(
            settings.GOOGLE_CREDENTIALS_FILE,
            scopes=OAUTH_SCOPES,
            state=state,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
        )
        
        # Disable strict scope validation to allow partial grants
        flow.oauth2session.scope = None

        # ✅ Use authorization_response instead of passing code + redirect_uri separately
        # This prevents the duplicate redirect_uri error
        # Include_granted_scopes allows partial scope grants without raising errors
        flow.fetch_token(
            authorization_response=str(request.url),
            include_granted_scopes="true"
        )

        credentials = flow.credentials

        if not credentials.token:
            raise ValueError("Missing access token from OAuth response")

        # 4️⃣ Save tokens to PlanetScale database
        await save_google_token(
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            expiry=credentials.expiry,
        )

        oauth_state_store.pop(state, None)
        logger.info("OAuth completed successfully.")
        return RedirectResponse(url=f"{settings.FRONTEND_URL}?auth=success")

    except Exception as exc:
        import traceback
        logger.error("OAuth callback failed: %s", exc)
        logger.error(traceback.format_exc())
        if state:
            oauth_state_store.pop(state, None)
        return RedirectResponse(url=f"{settings.FRONTEND_URL}?auth=error&reason=callback_failed")


@router.get("/auth/me")
async def get_current_user() -> Dict[str, Any]:
    try:
        creds = await get_google_credentials()
        return await get_user_profile(creds.token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Failed to fetch user profile")


@router.get("/auth/status")
async def auth_status():
    try:
        await get_google_credentials()
        return {"authenticated": True}
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated")