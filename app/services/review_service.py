from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple, Iterable
from zoneinfo import ZoneInfo
from llm.moderation import moderate_reply
import httpx

# from app.connections import db
from app.db.connection import PlanetScaleDB
from app.config import settings
from app.services.auth_service import get_google_credentials, lookup_location_metadata
from app.timezone_utils import now_utc, now_ist

logger = logging.getLogger(__name__)

TABLE_NAME = "location_reviews"
REGION_TABLE = "region"
IST = ZoneInfo("Asia/Kolkata")

cache_store = {}
cache_timestamps = {}


def deduplicate_before_write(reviews: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    if not reviews:
        return [], {"total_input": 0, "duplicates_found": 0, "new_reviews": 0}

    try:
        incoming_review_ids = [r["reviewId"] for r in reviews]
        logger.info(f"🔍 Checking {len(incoming_review_ids)} reviews for duplicates...")

        try:
            result = db.execute_query(f"SELECT COUNT(*) as cnt FROM {TABLE_NAME}")
            existing_count = result[0]["cnt"] if result else 0
            if existing_count == 0:
                logger.info("✅ Table is empty, all reviews are new")
                return reviews, {
                    "total_input": len(reviews),
                    "duplicates_found": 0,
                    "new_reviews": len(reviews)
                }
        except Exception:
            logger.info("✅ Table doesn't exist yet, all reviews are new")
            return reviews, {
                "total_input": len(reviews),
                "duplicates_found": 0,
                "new_reviews": len(reviews)
            }

        placeholders = ",".join(["%s"] * len(incoming_review_ids))
        existing_rows = db.execute_query(
            f"SELECT DISTINCT reviewId FROM {TABLE_NAME} WHERE reviewId IN ({placeholders})",
            tuple(incoming_review_ids)
        )
        existing_ids = {row["reviewId"] for row in existing_rows}
        new_reviews = [r for r in reviews if r["reviewId"] not in existing_ids]

        stats = {
            "total_input": len(reviews),
            "duplicates_found": len(existing_ids),
            "new_reviews": len(new_reviews)
        }

        logger.info(f"🔍 Dedup: {stats['total_input']} input, {stats['duplicates_found']} existing, {stats['new_reviews']} new")
        return new_reviews, stats

    except Exception as exc:
        logger.error(f"⚠️ Deduplication check failed, proceeding with all reviews: {exc}")
        return reviews, {
            "total_input": len(reviews),
            "duplicates_found": 0,
            "new_reviews": len(reviews),
            "error": str(exc)
        }


def _normalize_review_dict(review: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure review dict has all required fields from REVIEW_SCHEMA."""
    normalized = {
        "location_id": review.get("location_id"),
        "review_date": review.get("review_date"),
        "reviewId": review.get("reviewId"),
        "storeCode": review.get("storeCode"),
        "title": review.get("title"),
        "reviewer": review.get("reviewer"),
        "reviewer_displayName": review.get("reviewer_displayName"),
        "reviewer_isAnonymous": review.get("reviewer_isAnonymous"),
        "reviewer_profilePhotoUrl": review.get("reviewer_profilePhotoUrl"),
        "comment": review.get("comment"),
        "starRating": review.get("starRating"),
        "createTime": review.get("createTime"),
        "updateTime": review.get("updateTime"),
        "reviewReply": review.get("reviewReply"),
        "fetchedAt": review.get("fetchedAt"),
        "context_sentiment": review.get("context_sentiment"),
        "context_confidence": review.get("context_confidence"),
        "final_sentiment": review.get("final_sentiment"),
        "attributes": review.get("attributes"),
        "emotion": review.get("emotion"),
        "quality_score": review.get("quality_score"),
        "post_error": review.get("post_error"),
    }
    return normalized


def optimized_batch_write_with_dedup(
    reviews: List[Dict[str, Any]],
    operation: str = "merge",
    update_aggregates: bool = False
) -> Dict[str, Any]:
    if not reviews:
        return {"rows_written": 0}

    start_time = datetime.now()
    dedup_stats = None

    try:
        if operation == "append_dedup":
            logger.info("🔍 Pre-filtering duplicates before write...")
            reviews, dedup_stats = deduplicate_before_write(reviews)

            if not reviews:
                logger.info("✅ All reviews already exist - skipping write")
                return {
                    "status": "success",
                    "rows_processed": 0,
                    "operation": "append_dedup",
                    "skipped": "all duplicates",
                    "dedup_stats": dedup_stats,
                    "duration_seconds": (datetime.now() - start_time).total_seconds()
                }

        normalized_reviews = [_normalize_review_dict(r) for r in reviews]
        total_rows = len(normalized_reviews)
        
        unique_locations = set(r.get("location_id") for r in normalized_reviews if r.get("location_id"))
        num_locations = len(unique_locations)
        
        logger.info(f"📊 Batch write starting: {total_rows} rows, {num_locations} locations")

        if operation == "merge":
            logger.info(f"🔄 Using UPSERT operation for deduplication...")
            sql = f"""
            INSERT INTO {TABLE_NAME} 
            (name, reviewId, reviewer_displayName, reviewer_isAnonymous, 
             reviewer_profilePhotoUrl, starRating, rating, comment, 
             createTime, updateTime, fetchedAt, reviewReply, title)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                reviewer_displayName = VALUES(reviewer_displayName),
                reviewer_isAnonymous = VALUES(reviewer_isAnonymous),
                reviewer_profilePhotoUrl = VALUES(reviewer_profilePhotoUrl),
                rating = VALUES(rating),
                comment = VALUES(comment),
                fetchedAt = VALUES(fetchedAt),
                reviewReply = VALUES(reviewReply),
                title = VALUES(title)
            """
            rows_written = 0
            for review in normalized_reviews:
                try:
                    params = (
                        review.get("location_id"),
                        review.get("reviewId"),
                        review.get("reviewer_displayName"),
                        review.get("reviewer_isAnonymous"),
                        review.get("reviewer_profilePhotoUrl"),
                        review.get("starRating"),
                        review.get("starRating"),
                        review.get("comment"),
                        review.get("createTime"),
                        review.get("updateTime"),
                        review.get("fetchedAt"),
                        review.get("reviewReply"),
                        review.get("title"),
                    )
                    if rows_written == 0:
                        logger.info(f"🔧 MERGE INSERT params: location_id={params[0]}, reviewId={params[1][:20]}..., displayName={params[2]}, photoUrl={'present' if params[4] else 'None'}")
                    
                    db.execute_update(sql, params)
                    rows_written += 1
                except Exception as e:
                    logger.warning(f"Failed to write review {review.get('reviewId')}: {e}")
            operation_type = "merge (upsert with dedup)"

        elif operation in ("append", "append_dedup"):
            if operation == "append":
                logger.warning(f"⚠️ Using APPEND operation - duplicates may occur!")
            else:
                logger.info(f"✅ Using APPEND with pre-filtered data")

            sql = f"""
            INSERT INTO {TABLE_NAME} 
            (name, reviewId, reviewer_displayName, reviewer_isAnonymous, 
             reviewer_profilePhotoUrl, starRating, rating, comment, 
             createTime, updateTime, fetchedAt, reviewReply, title)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            rows_written = 0
            for review in normalized_reviews:
                try:
                    params = (
                        review.get("location_id"),
                        review.get("reviewId"),
                        review.get("reviewer_displayName"),
                        review.get("reviewer_isAnonymous"),
                        review.get("reviewer_profilePhotoUrl"),
                        review.get("starRating"),
                        review.get("starRating"),
                        review.get("comment"),
                        review.get("createTime"),
                        review.get("updateTime"),
                        review.get("fetchedAt"),
                        review.get("reviewReply"),
                        review.get("title"),
                    )
                    if rows_written == 0:
                        logger.info(f"🔧 APPEND INSERT params: location_id={params[0]}, reviewId={params[1][:20]}..., displayName={params[2]}, photoUrl={'present' if params[4] else 'None'}")
                    
                    db.execute_update(sql, params)
                    rows_written += 1
                except Exception as e:
                    logger.warning(f"Failed to write review {review.get('reviewId')}: {e}")
            operation_type = f"{operation} ({'no dedup' if operation == 'append' else 'pre-filtered'})"
        else:
            raise ValueError(f"Invalid operation: {operation}")

        duration = (datetime.now() - start_time).total_seconds()

        result = {
            "status": "success",
            "rows_processed": rows_written,
            "operation": operation_type,
            "locations": num_locations,
            "duration_seconds": round(duration, 2),
            "throughput_rows_per_sec": round(rows_written / duration if duration > 0 else 0, 2)
        }

        if dedup_stats:
            result["dedup_stats"] = dedup_stats

        logger.info(f"✅ {operation_type} completed: {total_rows} rows in {duration:.2f}s")
        return result

    except Exception as exc:
        logger.exception(f"❌ Batch write failed: {exc}")
        raise


def get_dashboard_summary(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    location_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    try:
        where_clauses = []
        params = []
        
        if start_date:
            where_clauses.append("DATE(createTime) >= %s")
            params.append(start_date)
        if end_date:
            where_clauses.append("DATE(createTime) <= %s")
            params.append(end_date)
        if location_ids:
            placeholders = ",".join(["%s"] * len(location_ids))
            where_clauses.append(f"location_id IN ({placeholders})")
            params.extend(location_ids)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        query = f"""
            SELECT
                COUNT(reviewId) as total_reviews,
                AVG(CAST(starRating AS DECIMAL(3,2))) as avg_rating,
                COUNT(CASE WHEN LOWER(final_sentiment) = 'positive' THEN 1 END) as positive_reviews,
                COUNT(CASE WHEN LOWER(final_sentiment) = 'negative' THEN 1 END) as negative_reviews,
                COUNT(CASE WHEN (reviewReply IS NULL OR reviewReply = '') THEN 1 END) as unreplied_count
            FROM {TABLE_NAME}
            WHERE {where_sql}
        """
        
        result = db.execute_query(query, tuple(params) if params else None)
        summary = result[0] if result else {}
        
        return {
            "total_reviews": summary.get("total_reviews", 0),
            "avg_rating": round(float(summary.get("avg_rating") or 0), 2),
            "positive_reviews": summary.get("positive_reviews", 0),
            "negative_reviews": summary.get("negative_reviews", 0),
            "unreplied_count": summary.get("unreplied_count", 0)
        }
    except Exception as exc:
        logger.exception(f"Dashboard summary failed: {exc}")
        raise


def get_location_performance(location_id: str) -> Dict[str, Any]:
    try:
        query = f"""
            SELECT
                COUNT(reviewId) as total_reviews,
                AVG(CAST(starRating AS DECIMAL(3,2))) as avg_rating,
                COUNT(CASE WHEN CAST(starRating AS UNSIGNED) >= 4 THEN 1 END) as positive_count,
                COUNT(CASE WHEN CAST(starRating AS UNSIGNED) <= 2 THEN 1 END) as negative_count
            FROM {TABLE_NAME}
            WHERE location_id = %s
        """
        
        result = db.execute_query(query, (location_id,))
        perf = result[0] if result else {}
        
        return {
            "location_id": location_id,
            "total_reviews": perf.get("total_reviews", 0),
            "avg_rating": round(float(perf.get("avg_rating") or 0), 2),
            "positive_count": perf.get("positive_count", 0),
            "negative_count": perf.get("negative_count", 0)
        }
    except Exception as exc:
        logger.exception(f"Location performance failed: {exc}")
        raise


def check_duplicates(limit: int = 100) -> Dict[str, Any]:
    try:
        logger.info("🔍 Checking for duplicate reviewIds...")

        query = f"""
            SELECT
                reviewId,
                COUNT(*) as count,
                GROUP_CONCAT(DISTINCT location_id) as locations,
                MIN(fetchedAt) as first_seen,
                MAX(fetchedAt) as last_seen
            FROM {TABLE_NAME}
            GROUP BY reviewId
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT {limit}
        """
        
        dupes = db.execute_query(query)

        duplicates = []
        total_duplicate_rows = 0

        for row in dupes:
            locations = row.get("locations", "").split(",") if row.get("locations") else []
            duplicates.append({
                "reviewId": row["reviewId"],
                "count": row["count"],
                "locations": locations,
                "first_seen": row["first_seen"].isoformat() if row.get("first_seen") else None,
                "last_seen": row["last_seen"].isoformat() if row.get("last_seen") else None
            })
            total_duplicate_rows += row["count"]

        duplicate_count = len(duplicates)
        logger.info(f"✅ Found {duplicate_count} unique reviewIds with duplicates")

        return {
            "duplicate_count": duplicate_count,
            "total_duplicate_rows": total_duplicate_rows,
            "duplicates": duplicates,
            "recommendation": "Use admin/remove-duplicates to clean up" if duplicate_count > 0 else "No duplicates found"
        }

    except Exception as exc:
        logger.exception(f"❌ Duplicate check failed: {exc}")
        raise


def get_table_health() -> Dict[str, Any]:
    try:
        count_query = f"SELECT COUNT(*) as total_rows FROM {TABLE_NAME}"
        total_result = db.execute_query(count_query)
        total_rows = total_result[0]["total_rows"] if total_result else 0
        
        locations_query = f"SELECT COUNT(DISTINCT location_id) as unique_locations FROM {TABLE_NAME}"
        locations_result = db.execute_query(locations_query)
        locations = locations_result[0]["unique_locations"] if locations_result else 0
        
        range_query = f"SELECT MIN(createTime) as earliest, MAX(createTime) as latest, AVG(CAST(starRating AS DECIMAL(3,2))) as avg_rating FROM {TABLE_NAME}"
        range_result = db.execute_query(range_query)
        
        earliest = None
        latest = None
        avg_rating = 0
        
        if range_result:
            earliest = range_result[0]["earliest"].isoformat() if range_result[0].get("earliest") else None
            latest = range_result[0]["latest"].isoformat() if range_result[0].get("latest") else None
            avg_rating = round(float(range_result[0].get("avg_rating") or 0), 2)

        return {
            "total_rows": total_rows,
            "unique_locations": locations,
            "date_range": {
                "earliest": earliest,
                "latest": latest
            },
            "avg_rating": avg_rating,
            "status": "healthy" if total_rows > 0 else "empty"
        }
    except Exception as exc:
        logger.exception(f"Table health check failed: {exc}")
        raise


def parse_google_time(iso_str: str) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def extract_review_id(review: dict) -> str:
    return review.get("name", "").split("/")[-1] or review.get("id", "")


def star_rating_to_int(star_str: str) -> Optional[int]:
    if not star_str:
        return None

    if isinstance(star_str, int):
        return star_str if 1 <= star_str <= 5 else None

    star_map = {
        "ONE": 1,
        "TWO": 2,
        "THREE": 3,
        "FOUR": 4,
        "FIVE": 5
    }
    
    if isinstance(star_str, str):
        upper_str = star_str.upper().strip()
        if upper_str in star_map:
            return star_map[upper_str]
    
    try:
        val = int(star_str)
        return val if 1 <= val <= 5 else None
    except (ValueError, TypeError):
        return None


async def _safe_get(client: httpx.AsyncClient, url: str, headers: dict, params: dict) -> Optional[dict]:
    max_retries = 3
    base_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            resp = await asyncio.wait_for(client.get(url, headers=headers, params=params), timeout=60.0)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited (429). Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
            logger.warning(f"API returned {resp.status_code}")
            return None
        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request timeout for {url}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            logger.warning(f"Request timeout (final) for {url}")
            return None
        except Exception as exc:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request failed: {exc}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            logger.warning(f"Request failed (final): {exc}")
            return None
    
    return None


async def fetch_reviews_for_locations(
    account_id: str,
    location_ids: Optional[List[str]],
    concurrency: int,
    max_reviews: int,
    only_unreplied: bool,
    include_inactive: bool,
    dedup_mode: str
) -> Dict[str, Any]:
    acct = account_id or settings.GOOGLE_ACCOUNT_ID
    if not acct or str(acct).strip().lower() == "none":
        raise ValueError("GOOGLE_ACCOUNT_ID not configured")

    if location_ids:
        target_locations = location_ids
    else:
        try:
            sql = "SELECT name, title, status FROM locations"
            if not include_inactive:
                sql += " WHERE status = 'OPEN' OR LOWER(status) = 'open'"
            
            results = db.execute_query(sql)
            target_locations = [row.get("name") if isinstance(row, dict) else getattr(row, "name", None) for row in results if row]
            target_locations = [loc for loc in target_locations if loc]

            if not target_locations:
                raise ValueError("No active locations found")

            logger.info(f"📍 Found {len(target_locations)} locations")

        except Exception as exc:
            logger.exception(f"Failed to fetch locations: {exc}")
            raise

    logger.info(f"🎯 Starting review fetch for {len(target_locations)} locations")

    try:
        creds = await get_google_credentials()
    except Exception as exc:
        logger.exception(f"Failed to get Google credentials: {exc}")
        raise

    headers = {"Authorization": f"Bearer {creds.token}"}
    sem = asyncio.Semaphore(concurrency)

    all_reviews: List[dict] = []
    per_location_summary: List[Dict[str, Any]] = []

    async def process_location(loc_id: str):
        async with sem:
            try:
                meta = await lookup_location_metadata(loc_id)
                store_code = meta.get("storeCode", "")
                title = meta.get("title", "")
                
                logger.info(f"📍 Location metadata: {meta}")
                logger.info(f"🔍 Processing location: {loc_id} ({title})")

                base_url = f"https://mybusiness.googleapis.com/v4/accounts/{acct}/{loc_id}/reviews"
                params = {"pageSize": min(max_reviews, 100)}

                all_fetched_reviews = []
                loc_reviews = []
                page_count = 0

                async with httpx.AsyncClient(timeout=60.0) as client:
                    while len(all_fetched_reviews) < max_reviews:
                        resp = await _safe_get(client, base_url, headers, params)

                        if resp is None:
                            per_location_summary.append({
                                "location_id": loc_id,
                                "storeCode": store_code,
                                "title": title,
                                "error": "network_failure",
                                "total_fetched": len(all_fetched_reviews),
                                "unreplied_stored": len(loc_reviews),
                                "stored": len(loc_reviews),
                                "pages": page_count,
                                "status": "error"
                            })
                            break

                        reviews_in_page = resp.get("reviews", [])
                        if not reviews_in_page:
                            break

                        all_fetched_reviews.extend(reviews_in_page)

                        for idx, review in enumerate(reviews_in_page):
                            review_id = extract_review_id(review)
                            if not review_id:
                                continue

                            try:
                                if idx == 0:
                                    logger.info(f"📋 Sample review keys: {list(review.keys())}")
                                    logger.info(f"📋 Reviewer object: {review.get('reviewer', 'NOT_FOUND')}")
                                
                                review_date = parse_google_time(review.get("createTime"))
                                if not review_date:
                                    continue

                                has_reply = bool(review.get("reviewReply"))
                                if only_unreplied and has_reply:
                                    continue

                                reviewer_obj = review.get("reviewer", {})
                                display_name = reviewer_obj.get("displayName", "") if reviewer_obj else ""
                                is_anonymous = reviewer_obj.get("isAnonymous", False) if reviewer_obj else False
                                photo_url = reviewer_obj.get("profilePhotoUrl", "") if reviewer_obj else ""
                                
                                logger.info(f"📝 Review {review_id[:20]}... | Reviewer: name='{display_name}', anonymous={is_anonymous}, photo={'present' if photo_url else 'none'}")

                                normalized = {
                                    "location_id": loc_id,
                                    "review_date": review_date.date(),
                                    "reviewId": review_id,
                                    "storeCode": store_code,
                                    "title": title,
                                    "reviewer": reviewer_obj,
                                    "reviewer_displayName": display_name,
                                    "reviewer_isAnonymous": is_anonymous,
                                    "reviewer_profilePhotoUrl": photo_url,
                                    "comment": review.get("comment", ""),
                                    "starRating": star_rating_to_int(review.get("starRating", "FIVE")),
                                    "createTime": review_date,
                                    "updateTime": None,
                                    "reviewReply": review.get("reviewReply", ""),
                                    "fetchedAt": now_utc(),
                                    "sentiment": None,
                                    "emotion": None,
                                    "attributes": None,
                                    "context_sentiment": None,
                                    "context_confidence": None,
                                    "final_sentiment": None,
                                    "quality_score": None,
                                    "post_error": None,
                                }

                                loc_reviews.append(normalized)
                                all_reviews.append(normalized)

                            except Exception as e:
                                logger.warning(f"Failed to normalize review {review_id}: {e}")
                                continue

                        page_count += 1
                        next_page_token = resp.get("nextPageToken")
                        if next_page_token:
                            params["pageToken"] = next_page_token
                        else:
                            break

                per_location_summary.append({
                    "location_id": loc_id,
                    "storeCode": store_code,
                    "title": title,
                    "total_fetched": len(all_fetched_reviews),
                    "unreplied_found": len(loc_reviews),
                    "stored": len(loc_reviews),
                    "pages": page_count,
                    "status": "success"
                })

            except Exception as exc:
                logger.exception(f"Failed to process location {loc_id}: {exc}")
                per_location_summary.append({
                    "location_id": loc_id,
                    "error": str(exc),
                    "status": "error"
                })

    tasks = [process_location(loc_id) for loc_id in target_locations]
    await asyncio.gather(*tasks, return_exceptions=True)

    total_unreplied = sum(r.get("unreplied_found", 0) for r in per_location_summary)

    if all_reviews:
        write_result = optimized_batch_write_with_dedup(all_reviews, operation=dedup_mode)
    else:
        write_result = {"status": "skipped", "reason": "no reviews fetched"}

    return {
        "total_fetched": len(all_reviews),
        "total_stored": write_result.get("rows_processed", 0),
        "total_unreplied": total_unreplied,
        "per_location": per_location_summary,
        "write_result": write_result
    }


def clear_all_reviews() -> Dict[str, Any]:
    try:
        query = f"TRUNCATE TABLE {TABLE_NAME}"
        db.execute_update(query)
        logger.info(f"✅ Cleared all reviews from {TABLE_NAME} table")
        return {
            "status": "success",
            "message": f"All reviews cleared from {TABLE_NAME}",
            "table": TABLE_NAME
        }
    except Exception as exc:
        logger.exception(f"Failed to clear reviews: {exc}")
        raise


def get_region_store_codes(region: str, region_table: str) -> List[str]:
    try:
        query = f"""
            SELECT DISTINCT storeCode FROM {region_table}
            WHERE LOWER(region) = %s
        """
        result = db.execute_query(query, (region.lower(),))
        return [r["storeCode"] for r in result if r.get("storeCode")]
    except Exception as e:
        logger.warning(f"Failed to fetch region store codes: {e}")
        return []


def list_reviews_optimized(
    store_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_stars: Optional[int] = None,
    max_stars: Optional[int] = None,
    sentiment: Optional[str] = None,
    has_reply: Optional[bool] = None,
    region: Optional[str] = None,
    reviewer_displayName: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    include_summary: bool = False,
) -> Dict[str, Any]:
    query_start_time = datetime.now()
    
    where_clauses = []
    params = []
    
    if store_code:
        where_clauses.append("l.storeCode = %s")
        params.append(store_code)
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date).date()
            where_clauses.append("DATE(lr.createTime) >= %s")
            params.append(start_dt)
        except Exception:
            raise ValueError("Invalid start_date format")
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date).date()
            where_clauses.append("DATE(lr.createTime) <= %s")
            params.append(end_dt)
        except Exception:
            raise ValueError("Invalid end_date format")
    
    if region:
        store_codes = get_region_store_codes(region, REGION_TABLE)
        if not store_codes:
            return {
                "reviews": [],
                "total": 0,
                "has_more": False,
                "limit": limit,
                "offset": offset,
                "pagination": {
                    "current_page": (offset // limit) + 1 if limit > 0 else 1,
                    "page_size": limit,
                    "offset": offset
                },
                "summary_counts": {"total_reviews": 0, "positive_count": 0, "negative_count": 0, "neutral_count": 0},
                "from_cache": False
            }
        placeholders = ",".join(["%s"] * len(store_codes))
        where_clauses.append(f"l.storeCode IN ({placeholders})")
        params.extend(store_codes)
    
    if min_stars is not None:
        where_clauses.append("CAST(lr.starRating AS UNSIGNED) >= %s")
        params.append(min_stars)
    
    if max_stars is not None:
        where_clauses.append("CAST(lr.starRating AS UNSIGNED) <= %s")
        params.append(max_stars)
    
    if sentiment:
        where_clauses.append("LOWER(lr.final_sentiment) = %s")
        params.append(sentiment.lower())
    
    if has_reply is not None:
        if has_reply:
            where_clauses.append("lr.reviewReply IS NOT NULL AND lr.reviewReply != ''")
        else:
            where_clauses.append("(lr.reviewReply IS NULL OR lr.reviewReply = '')")
    
    if reviewer_displayName:
        where_clauses.append("lr.reviewer_displayName = %s")
        params.append(reviewer_displayName)
    
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    
    summary_counts = {
        "total_reviews": 0,
        "positive_count": 0,
        "negative_count": 0,
        "neutral_count": 0
    }
    
    if include_summary:
        agg_start = datetime.now()
        try:
            summary_query = f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN LOWER(final_sentiment) = 'positive' THEN 1 END) as positive_count,
                    COUNT(CASE WHEN LOWER(final_sentiment) = 'negative' THEN 1 END) as negative_count,
                    COUNT(CASE WHEN LOWER(final_sentiment) = 'neutral' THEN 1 END) as neutral_count
                FROM {TABLE_NAME} lr
                LEFT JOIN locations l ON lr.name = l.name
                LEFT JOIN {REGION_TABLE} r ON l.storeCode = r.storeCode
                WHERE {where_sql}
            """
            summary_result = db.execute_query(summary_query, tuple(params) if params else None)
            
            if summary_result:
                summary_counts = {
                    "total_reviews": summary_result[0]["total"],
                    "positive_count": summary_result[0]["positive_count"],
                    "negative_count": summary_result[0]["negative_count"],
                    "neutral_count": summary_result[0]["neutral_count"]
                }
            
            agg_duration = (datetime.now() - agg_start).total_seconds()
            logger.info(f"✅ Aggregation completed in {agg_duration:.2f}s")
        except Exception as agg_err:
            logger.warning(f"⚠️ Aggregation failed after {(datetime.now() - agg_start).total_seconds():.1f}s: {agg_err}. Using empty summary.")
            summary_counts = {
                "total_reviews": 0,
                "positive_count": 0,
                "negative_count": 0,
                "neutral_count": 0,
                "note": "Aggregation timed out or failed - summary unavailable"
            }
    
    if offset > 10000:
        logger.warning(f"⚠️ High pagination offset: {offset}. Consider using /summary-stats for filtered counts instead.")
    
    fetch_size = min(offset + limit + 50, 5000)
    
    query = f"""
        SELECT 
            lr.id, lr.name, lr.reviewId, lr.reviewer_displayName, lr.reviewer_isAnonymous,
            lr.reviewer_profilePhotoUrl, lr.starRating, lr.rating, lr.comment, lr.createTime,
            lr.updateTime, lr.fetchedAt, lr.reviewReply, lr.title, lr.sentiment, lr.emotion,
            lr.attributes, lr.context_sentiment, lr.context_confidence, lr.final_sentiment,
            lr.quality_score, lr.post_error, lr.created_at,
            l.storeCode, r.region as review_region
        FROM {TABLE_NAME} lr
        LEFT JOIN locations l ON lr.name = l.name
        LEFT JOIN {REGION_TABLE} r ON l.storeCode = r.storeCode
        WHERE {where_sql}
        ORDER BY lr.createTime DESC, lr.reviewId DESC
        LIMIT %s
    """
    
    query_params = params + [fetch_size]
    paginated_rows = db.execute_query(query, tuple(query_params) if query_params else None)
    
    has_more = len(paginated_rows) > (offset + limit)
    paginated_rows = paginated_rows[offset:offset + limit]
    
    reviews = []
    for row in paginated_rows:
        if "title" in row and row["title"] and isinstance(row["title"], str) and "." in row["title"]:
            row["title"] = row["title"].split(".")[0] + "."
        if "createTime" in row and row["createTime"]:
            if isinstance(row["createTime"], str):
                row["createTime"] = row["createTime"]
            elif hasattr(row["createTime"], 'isoformat'):
                row["createTime"] = row["createTime"].isoformat()
        if "fetchedAt" in row and row["fetchedAt"]:
            if isinstance(row["fetchedAt"], str):
                row["fetchedAt"] = row["fetchedAt"]
            elif hasattr(row["fetchedAt"], 'isoformat'):
                row["fetchedAt"] = row["fetchedAt"].isoformat()
        if "updateTime" in row and row["updateTime"]:
            if isinstance(row["updateTime"], str):
                row["updateTime"] = row["updateTime"]
            elif hasattr(row["updateTime"], 'isoformat'):
                row["updateTime"] = row["updateTime"].isoformat()
        reviews.append(row)
    
    current_page = (offset // limit) + 1 if limit > 0 else 1
    
    query_duration = (datetime.now() - query_start_time).total_seconds()
    
    logger.info(f"📊 Query completed: {query_duration:.2f}s, results: {len(reviews)}, has_more: {has_more}")
    
    return {
        "reviews": reviews,
        "total": summary_counts["total_reviews"] if include_summary else len(paginated_rows),
        "has_more": has_more,
        "limit": limit,
        "offset": offset,
        "pagination": {
            "current_page": current_page,
            "page_size": limit,
            "offset": offset,
            "total_pages": (summary_counts["total_reviews"] + limit - 1) // limit if include_summary and limit > 0 else None
        },
        "summary_counts": summary_counts,
        "query_execution_seconds": round(query_duration, 2),
        "from_cache": False
    }

# async def generate_safe_reply(review: dict):

#     review_text = review.get("comment", "")
#     rating = review.get("starRating", 5)

#     # Generate AI reply
#     ai_reply = await generate_reply(review_text, rating)

#     # Run moderation
#     moderation_result = moderate_reply(ai_reply, rating)

#     if not moderation_result.allowed:
#         return fallback_reply(rating)

#     return ai_reply

############ Gemini call with moderation and fallback handling ##########
from llm.prompt_builder import build_prompt
from llm.gemini_client import call_gemini
from llm.response_parser import parse_response
from llm.reply_enforcer import enforce_reply
from llm.fallback_handler import fallback_response


async def process_review(data: dict):

    try:
        prompt = build_prompt(
            review_text=data.get("review_text"),
            rating=data.get("star_rating"),
            customer=data.get("customer_name"),
            store=data.get("store_location")
        )

        raw_response = await call_gemini(prompt)

        parsed = parse_response(raw_response)

        reply = enforce_reply(parsed)

        return {
            "sentiment": parsed.get("sentiment"),
            "emotion": parsed.get("emotion"),
            "attributes": parsed.get("attributes"),
            "reply": reply
        }

    except Exception:
        return fallback_response(data)