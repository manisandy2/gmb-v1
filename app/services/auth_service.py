from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from google.oauth2.credentials import Credentials

from app.connections import save_google_token, get_google_credentials, db
from app.config import settings

logger = logging.getLogger(__name__)


async def get_user_profile(access_token: str) -> Dict[str, Any]:
    import httpx

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://www.googleapis.com/oauth2/v3/userinfo", headers=headers)
        if resp.status_code >= 400:
            body = (await resp.aread())[:1000]
            logger.error(f"Failed to fetch userinfo: status={resp.status_code}")
            raise RuntimeError(f"Failed to fetch userinfo: {resp.status_code}")
        return resp.json()


async def lookup_location_metadata(short_id: str) -> Dict[str, str]:
    try:
        sql = """
        SELECT name, storeCode, title FROM locations
        WHERE name LIKE %s OR name RLIKE %s
        LIMIT 1
        """
        results = db.execute_query(sql, (f"%{short_id}%", f".*/{short_id}$"))
        if results:
            rd = results[0]
            return {
                "storeCode": rd.get("storeCode") or "",
                "title": rd.get("title") or "",
                "name": rd.get("name") or ""
            }

    except Exception as exc:
        logger.exception(f"Error looking up location metadata for {short_id}: {exc}")

    return {}

if __name__ == "__main__":
    import asyncio
    asyncio.run(get_user_profile(""))