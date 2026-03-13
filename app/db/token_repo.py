import asyncio
import logging
from datetime import datetime
from typing import Optional

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from app.config import settings
from app.db.connection import db

logger = logging.getLogger(__name__)


async def save_google_token(
    access_token: str,
    refresh_token: Optional[str],
    expiry: Optional[datetime],
) -> None:
    """
    Save Google OAuth token asynchronously.
    """

    loop = asyncio.get_running_loop()

    sql = """
    INSERT INTO token (access_token, refresh_token, token_expiry)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE
        access_token = VALUES(access_token),
        refresh_token = VALUES(refresh_token),
        token_expiry = VALUES(token_expiry),
        updated_at = CURRENT_TIMESTAMP
    """

    await loop.run_in_executor(
        None,
        db.execute_update,
        sql,
        (access_token, refresh_token, expiry),
    )

    logger.info("Google OAuth token saved")


def save_google_token_blocking(
    access_token: str,
    refresh_token: Optional[str],
    expiry: Optional[datetime],
) -> None:
    """
    Blocking version of token save.
    Useful for synchronous flows.
    """

    sql = """
    INSERT INTO token (access_token, refresh_token, token_expiry)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE
        access_token = VALUES(access_token),
        refresh_token = VALUES(refresh_token),
        token_expiry = VALUES(token_expiry),
        updated_at = CURRENT_TIMESTAMP
    """

    db.execute_update(
        sql,
        (access_token, refresh_token, expiry),
    )

    logger.info("Google OAuth token saved (blocking)")


async def get_google_credentials() -> Credentials:
    """
    Load Google OAuth credentials from database.
    Automatically refresh token if expired.
    """

    loop = asyncio.get_running_loop()

    def _get_creds():

        result = db.execute_query(
            "SELECT * FROM token ORDER BY updated_at DESC LIMIT 1"
        )

        if not result:
            raise RuntimeError(
                "Google credentials not found. Please authenticate first."
            )

        token_row = result[0]

        credentials = Credentials(
            token=token_row["access_token"],
            refresh_token=token_row["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            expiry=token_row["token_expiry"],
        )

        if (not credentials.token) or getattr(credentials, "expired", False):

            logger.info("Refreshing Google OAuth token")

            credentials.refresh(GoogleRequest())

            db.execute_update(
                """
                INSERT INTO token (access_token, refresh_token, token_expiry)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    access_token = VALUES(access_token),
                    refresh_token = VALUES(refresh_token),
                    token_expiry = VALUES(token_expiry)
                """,
                (
                    credentials.token,
                    credentials.refresh_token,
                    credentials.expiry,
                ),
            )

            logger.info("Token refreshed and stored")

        return credentials

    return await loop.run_in_executor(None, _get_creds)