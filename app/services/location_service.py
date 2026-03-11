import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings
from app.services.auth_service import get_google_credentials, save_google_token
from app.schemas.location import (
    LocationNormalized,
    LocationWithRatings,
    ReviewSummary,
    SyncLocationsResponse,
    ReviewSummaryResponse,
)
from app.timezone_utils import now_utc

logger = logging.getLogger(__name__)

INDIAN_PIN_RE = re.compile(r"\b\d{6}\b")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{6,10}")
GOOGLE_READ_MASK = "name,title,storeCode,openInfo,phoneNumbers,storefrontAddress,metadata,labels"

sync_lock = asyncio.Semaphore(1)


def _extract_primary_phone(phone_numbers: Optional[Any]) -> str:
    if not phone_numbers:
        return ""
    if isinstance(phone_numbers, (str, int)):
        s = str(phone_numbers).strip()
        return s if PHONE_RE.search(s) else s
    if isinstance(phone_numbers, dict):
        for key in ("primaryPhone", "primary_phone", "primary", "phoneNumber", "phone_number", "value"):
            val = phone_numbers.get(key)
            if val and PHONE_RE.search(str(val)):
                return str(val)
        for v in phone_numbers.values():
            if isinstance(v, (str, int)) and PHONE_RE.search(str(v)):
                return str(v)
    if isinstance(phone_numbers, list):
        for p in phone_numbers:
            if isinstance(p, (str, int)) and PHONE_RE.search(str(p)):
                return str(p)
            if isinstance(p, dict):
                phone_val = p.get("phoneNumber") or p.get("value") or p.get("number") or p.get("phone")
                if phone_val and PHONE_RE.search(str(phone_val)):
                    return str(phone_val)
    return ""


def _extract_place_id_from_metadata(metadata: Optional[dict]) -> Optional[str]:
    if not metadata or not isinstance(metadata, dict):
        return None
    for key in ("mapsPlaceId", "placeId", "googlePlaceId", "place_id", "placeid"):
        v = metadata.get(key)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    maps_url = metadata.get("mapsUrl") or metadata.get("maps_uri") or metadata.get("mapsUri") or metadata.get("maps_url")
    if maps_url and isinstance(maps_url, str):
        try:
            parsed = urlparse(maps_url)
            qs = parse_qs(parsed.query)
            for k in ("placeid", "place_id", "cid"):
                if k in qs and qs[k]:
                    return qs[k][0]
            path_parts = [p for p in parsed.path.split("/") if p]
            for part in path_parts:
                if part.startswith("ChI"):
                    return part
        except Exception:
            pass
    return None


def normalize_location(loc: Dict[str, Any]) -> LocationNormalized:
    labels = loc.get("labels") or []
    if isinstance(labels, str):
        labels = [x.strip() for x in labels.split(",") if x.strip()]

    storefront = loc.get("storefrontAddress") or loc.get("address") or {}
    administrative_area = (
        storefront.get("administrativeArea")
        or storefront.get("administrative_area")
        or storefront.get("state")
        or ""
    ).strip()
    locality = (storefront.get("locality") or storefront.get("city") or "").strip()
    postal_code = (storefront.get("postalCode") or storefront.get("postal_code") or "").strip()
    region_code = (storefront.get("regionCode") or storefront.get("region_code") or "").strip()

    if not postal_code:
        for label in labels:
            m = INDIAN_PIN_RE.search(label)
            if m:
                postal_code = m.group(0)
                break

    title = (loc.get("title") or "").strip()
    lower_labels = " ".join([l.lower() for l in labels])
    if not administrative_area:
        for st in (
            "tamil nadu",
            "karnataka",
            "maharashtra",
            "delhi",
            "uttar pradesh",
            "kerala",
            "andhra pradesh",
            "telangana",
            "gujarat",
        ):
            if st in lower_labels or st in title.lower():
                administrative_area = st.title()
                break

    if not locality:
        for lab in labels:
            m = re.search(r"\bin\s+([A-Za-z\s\-]+)", lab, re.IGNORECASE)
            if m:
                locality = m.group(1).strip()
                break

    primary_phone = _extract_primary_phone(loc.get("phoneNumbers") or loc.get("primaryPhone") or [])

    status = ""
    open_info = loc.get("openInfo") or {}
    if isinstance(open_info, dict):
        status = open_info.get("status") or status
    if not status:
        meta_status = (loc.get("metadata") or {}).get("status") or loc.get("status") or ""
        if meta_status:
            status = meta_status
    if not status:
        low_labels = [l.lower() for l in labels]
        if any("closed" in l for l in low_labels) or any("inactive" in l for l in low_labels):
            status = "CLOSED"
        elif any("temporarily" in l for l in low_labels):
            status = "TEMPORARILY_CLOSED"

    place_id = _extract_place_id_from_metadata(loc.get("metadata"))
    if not place_id:
        place_id = loc.get("placeId") or loc.get("mapsPlaceId") or loc.get("googlePlaceId") or ""

    fetched_at_src = loc.get("fetchedAt") or loc.get("updateTime") or loc.get("createTime")
    try:
        if isinstance(fetched_at_src, str) and fetched_at_src.endswith("Z"):
            fetched_at_dt = datetime.fromisoformat(fetched_at_src.replace("Z", "+00:00"))
        elif isinstance(fetched_at_src, datetime):
            fetched_at_dt = fetched_at_src
        else:
            fetched_at_dt = now_utc()
    except Exception:
        fetched_at_dt = now_utc()

    return LocationNormalized(
        title=title[:1024],
        name=loc.get("name") or "",
        storeCode=loc.get("storeCode") or "",
        status=status or "",
        primaryPhone=primary_phone or "",
        regionCode=region_code or "",
        administrativeArea=administrative_area or "",
        locality=locality or "",
        postalCode=postal_code or "",
        placeId=place_id or "",
        labels=labels,
        fetchedAt=fetched_at_dt,
    )


async def fetch_locations_batch(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    page_token: Optional[str] = None,
    page_size: int = 100,
    max_attempts: Optional[int] = None,
    base_backoff: Optional[float] = None,
) -> Dict[str, Any]:
    max_attempts = max_attempts or settings.SYNC_MAX_ATTEMPTS
    base_backoff = base_backoff or settings.SYNC_BASE_BACKOFF

    params = {"readMask": GOOGLE_READ_MASK, "pageSize": page_size}
    if page_token:
        params["pageToken"] = page_token

    attempt = 0
    last_error: Optional[str] = None
    last_status: Optional[int] = None

    while attempt < max_attempts:
        attempt += 1
        try:
            resp = await client.get(url, headers=headers, params=params, timeout=60.0)
        except httpx.RequestError as exc:
            last_error = str(exc)
            backoff = base_backoff * (2 ** (attempt - 1))
            jitter = random.uniform(-0.25 * backoff, 0.25 * backoff)
            sleep_for = max(0.1, backoff + jitter)
            await asyncio.sleep(sleep_for)
            continue

        status = resp.status_code
        last_status = status
        raw = b""
        try:
            raw = await resp.aread()
            text_preview = raw.decode("utf-8", errors="replace")[:1000]
        except Exception:
            text_preview = "<body-read-failed>"

        if status == 200:
            try:
                return json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                raise Exception("Failed to parse Google API response")

        if status == 429 or 500 <= status < 600:
            retry_after = None
            if "Retry-After" in resp.headers:
                try:
                    retry_after = float(resp.headers["Retry-After"])
                except Exception:
                    try:
                        retry_after = int(resp.headers["Retry-After"])
                    except Exception:
                        retry_after = None

            if retry_after:
                sleep_for = retry_after + random.uniform(0, 0.5)
                await asyncio.sleep(sleep_for)
            else:
                backoff = base_backoff * (2 ** (attempt - 1))
                jitter = random.uniform(-0.25 * backoff, 0.25 * backoff)
                sleep_for = max(0.1, backoff + jitter)
                await asyncio.sleep(sleep_for)

            last_error = f"status={status} body_preview={text_preview[:1000]}"
            continue

        try:
            err_body = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            err_body = text_preview
        raise Exception(f"Non-retryable error from Google API: {status}")

    raise Exception(
        f"Failed to fetch locations after retries (last_status={last_status}, last_error={last_error})"
    )


async def sync_locations_from_google(
    batch_size: int = 100,
    account_id: Optional[str] = None,
    stop_on_error: bool = False,
) -> SyncLocationsResponse:
    from app.connections import write_locations_to_db

    acct = account_id or settings.GOOGLE_ACCOUNT_ID
    if not acct:
        raise ValueError("GOOGLE_ACCOUNT_ID is not configured")

    async with sync_lock:
        credentials = await get_google_credentials()
        headers = {"Authorization": f"Bearer {credentials.token}"}
        location_url = f"https://mybusinessbusinessinformation.googleapis.com/v1/accounts/{acct}/locations"

        total_synced = 0
        pages = 0
        next_token: Optional[str] = None

        async with httpx.AsyncClient(timeout=60.0) as client:
            while True:
                try:
                    data = await fetch_locations_batch(
                        client, location_url, headers, page_token=next_token, page_size=batch_size
                    )
                except Exception as e:
                    if stop_on_error:
                        raise
                    return SyncLocationsResponse(
                        message="Partial sync - stopped due to error while fetching from Google API",
                        locations_synced=total_synced,
                        error=str(e),
                    )

                pages += 1
                locations_page = data.get("locations", []) or []

                if locations_page:
                    normalized = [normalize_location(loc).dict() for loc in locations_page]

                    for i, rec in enumerate(normalized):
                        if not rec.get("name"):
                            rec["name"] = f"locations/unknown-{pages}-{i}"

                    try:
                        written = await write_locations_to_db(normalized)
                        total_synced += written or len(normalized)
                    except Exception as exc:
                        if stop_on_error:
                            raise
                        logger.exception("Skipping page %d due to database error: %s", pages, exc)

                next_token = data.get("nextPageToken")
                if not next_token:
                    break

    return SyncLocationsResponse(
        message="Sync complete",
        locations_synced=total_synced,
        pages=pages,
    )


async def _fetch_aggregates_for_location(
    client: httpx.AsyncClient,
    acct: str,
    loc_name: str,
    headers: Dict[str, str],
    page_size: int = 1,
) -> Dict[str, Any]:
    if loc_name.startswith("accounts/"):
        parent = loc_name
    elif loc_name.startswith("locations/"):
        parent = f"accounts/{acct}/{loc_name}"
    else:
        parent = f"accounts/{acct}/locations/{loc_name}"

    url = f"https://mybusiness.googleapis.com/v4/{parent}/reviews"
    params = {"pageSize": page_size, "fields": "averageRating,totalReviewCount"}

    try:
        resp = await client.get(url, headers=headers, params=params, timeout=30.0)
    except httpx.RequestError:
        raise

    if resp.status_code == 200:
        try:
            data = resp.json()
        except Exception:
            data = {}
        avg = data.get("averageRating")
        cnt = data.get("totalReviewCount") or data.get("reviewCount") or data.get("review_count")

        rating_average: Optional[float] = None
        review_count: Optional[int] = None

        try:
            if avg is not None:
                rating_average = float(avg)
        except Exception:
            rating_average = None

        try:
            if cnt is not None:
                review_count = int(cnt)
        except Exception:
            review_count = None

        return {"review_count": review_count, "rating_average": rating_average, "raw": data}

    text = ""
    try:
        text = (await resp.aread()).decode("utf-8", errors="replace")
    except Exception:
        text = "<body-read-failed>"
    raise Exception(f"Failed to fetch aggregates: {resp.status_code}")


async def fetch_reviews_for_location(
    client: httpx.AsyncClient,
    acct: str,
    location_name: str,
    headers: Dict[str, str],
    page_size: int = 200,
) -> List[dict]:
    if location_name.startswith("accounts/"):
        parent = location_name
    elif location_name.startswith("locations/"):
        parent = f"accounts/{acct}/{location_name}"
    else:
        parent = f"accounts/{acct}/locations/{location_name}"

    url = f"https://mybusiness.googleapis.com/v4/{parent}/reviews"
    reviews: List[dict] = []
    next_page_token: Optional[str] = None

    while True:
        params = {"pageSize": page_size}
        if next_page_token:
            params["pageToken"] = next_page_token
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            data = resp.json()
            page_reviews = data.get("reviews", [])
            reviews.extend(page_reviews)
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break
        elif resp.status_code in (429, 500, 502, 503, 504):
            await asyncio.sleep(settings.SYNC_BASE_BACKOFF)
            continue
        else:
            resp.raise_for_status()

    return reviews


async def fetch_review_summary(
    account_id: Optional[str] = None,
    page_size: int = 1,
    concurrency: int = 40,
) -> ReviewSummaryResponse:
    from app.connections import upsert_rating_summary, db

    start_time = now_utc()
    acct = account_id or settings.GOOGLE_ACCOUNT_ID
    if not acct or str(acct).strip().lower() == "none":
        raise ValueError("GOOGLE_ACCOUNT_ID is not configured")

    credentials = await get_google_credentials()
    if not getattr(credentials, "refresh_token", None):
        raise ValueError("Missing refresh_token. Re-auth required.")
    headers = {"Authorization": f"Bearer {credentials.token}"}

    try:
        rows = db.execute_query("SELECT name, title FROM locations")
        location_rows: List[Dict[str, Any]] = [
            {"name": r.get("name"), "title": r.get("title")} for r in rows
        ]
    except Exception as exc:
        logger.exception("Failed to read locations from catalog: %s", exc)
        raise

    if not location_rows:
        return ReviewSummaryResponse(
            message="No locations found in catalog",
            locations_processed=0,
            rating_summaries_upserted=0,
        )

    sem = asyncio.Semaphore(concurrency)
    summaries_for_upsert: List[Dict[str, Any]] = []
    results_sample: List[Dict[str, Any]] = []
    total_upserts = 0

    async with httpx.AsyncClient(timeout=30.0) as client:

        async def fetch_and_build(loc_row: Dict[str, Any]) -> None:
            loc_name = loc_row.get("name") or ""
            title = loc_row.get("title") or ""
            try:
                agg = await _fetch_aggregates_for_location(client, acct, loc_name, headers, page_size=page_size)
                review_count = agg.get("review_count")
                rating_average = agg.get("rating_average")

                if review_count is None and rating_average is None:
                    try:
                        reviews = await fetch_reviews_for_location(client, acct, loc_name, headers, page_size=200)
                        reviews_list = reviews if isinstance(reviews, list) else []
                        rating_values: List[int] = []
                        for r in reviews_list:
                            star = r.get("starRating") or r.get("rating") or ""
                            rating_int: Optional[int] = None
                            if isinstance(star, (int, float)):
                                rating_int = int(star)
                            elif isinstance(star, str):
                                if star.isdigit():
                                    try:
                                        rating_int = int(star)
                                    except Exception:
                                        rating_int = None
                                else:
                                    mapping = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
                                    rating_int = mapping.get(star.upper())
                            if rating_int is not None:
                                rating_values.append(rating_int)

                        review_count = len(reviews_list)
                        rating_average = round(float(sum(rating_values)) / len(rating_values), 2) if rating_values else None
                    except Exception as exc:
                        logger.exception("Fallback full-review fetch failed for %s: %s", loc_name, exc)
                        review_count = 0
                        rating_average = None

                fetched_at = now_utc()
                summaries_for_upsert.append(
                    {
                        "name": loc_name,
                        "review_count": int(review_count or 0),
                        "rating_average": float(rating_average) if rating_average is not None else None,
                        "fetchedAt": fetched_at,
                    }
                )

                if len(results_sample) < 10:
                    results_sample.append(
                        {
                            "name": loc_name,
                            "title": title,
                            "review_count": int(review_count or 0),
                            "rating_average": rating_average,
                            "fetchedAt": fetched_at.isoformat(),
                        }
                    )
            except Exception as exc:
                logger.exception("Unexpected error for %s: %s", loc_name, exc)

        tasks = []
        for lr in location_rows:

            async def sem_task(lr_inner: Dict[str, Any]) -> None:
                async with sem:
                    await fetch_and_build(lr_inner)

            tasks.append(sem_task(lr))

        await asyncio.gather(*tasks)

    batch_size = 1000
    try:
        for i in range(0, len(summaries_for_upsert), batch_size):
            batch = summaries_for_upsert[i : i + batch_size]
            if batch:
                await upsert_rating_summary(batch)
                total_upserts += len(batch)
    except Exception as exc:
        logger.exception("Failed to upsert rating summaries to Iceberg: %s", exc)

    duration = round((now_utc() - start_time).total_seconds(), 2)
    return ReviewSummaryResponse(
        message="Reviews summary synced to Iceberg catalog (aggregates-only)",
        locations_processed=len(location_rows),
        rating_summaries_upserted=total_upserts,
        sample=results_sample,
        duration_seconds=duration,
    )
