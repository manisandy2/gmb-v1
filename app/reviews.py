import csv
import io
import json
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Query, HTTPException, Body, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel

from app.auth import logger
from app.config import settings
from app.services.review_service import (
    check_duplicates, get_table_health,
    get_dashboard_summary, get_location_performance, fetch_reviews_for_locations,
    list_reviews_optimized, clear_all_reviews, optimized_batch_write_with_dedup,
    cache_store, cache_timestamps, TABLE_NAME
)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class BulkReviewItem(BaseModel):
    location_id: str
    review_date: Optional[str] = None
    reviewId: str
    storeCode: Optional[str] = None
    title: Optional[str] = None
    reviewer: Optional[str] = None
    reviewer_displayName: Optional[str] = None
    reviewer_isAnonymous: Optional[bool] = None
    reviewer_profilePhotoUrl: Optional[str] = None
    comment: Optional[str] = None
    starRating: Optional[int] = None
    createTime: Optional[str] = None
    updateTime: Optional[str] = None
    reviewReply: Optional[str] = None
    fetchedAt: Optional[str] = None
    context_sentiment: Optional[str] = None
    context_confidence: Optional[float] = None
    final_sentiment: Optional[str] = None
    attributes: Optional[str] = None
    emotion: Optional[str] = None
    quality_score: Optional[int] = None
    post_error: Optional[str] = None

    class Config:
        from_attributes = True


router = APIRouter(tags=["Reviews_Mgmt"])
logger_instance = logging.getLogger(__name__)


@router.post("/bulk-upload")
async def bulk_upload_reviews(
    reviews: List[BulkReviewItem] = Body(..., description="List of reviews to upload"),
    operation: str = Query("append_dedup", pattern="^(merge|append|append_dedup)$", description="Insert operation mode: merge (upsert), append_dedup (no duplicates), append (may duplicate)"),
    skip_duplicates: bool = Query(True, description="Skip duplicate reviewIds instead of failing")
):
    start_time = time.time()
    try:
        from app.connections import db
        
        if not reviews:
            raise ValueError("Reviews list cannot be empty")
        
        logger.info(f"📥 Bulk upload started: {len(reviews)} reviews with operation={operation}, skip_duplicates={skip_duplicates}")
        
        batch_duplicates = []
        review_ids_in_batch = {}
        normalized_reviews = []
        
        for idx, review in enumerate(reviews):
            try:
                normalized = review.dict()
                review_id = normalized.get("reviewId")
                
                if not review_id:
                    raise ValueError("reviewId is required")
                
                if review_id in review_ids_in_batch:
                    batch_duplicates.append({
                        "reviewId": review_id,
                        "index": idx,
                        "first_occurrence_index": review_ids_in_batch[review_id],
                        "reason": "Duplicate within submitted batch"
                    })
                    logger.warning(f"⚠️ Duplicate reviewId in batch at index {idx}: {review_id}")
                    if skip_duplicates:
                        continue
                    else:
                        raise ValueError(f"Duplicate reviewId in batch: {review_id}")
                
                review_ids_in_batch[review_id] = idx
                
                if isinstance(normalized.get("createTime"), str):
                    try:
                        normalized["createTime"] = datetime.fromisoformat(normalized["createTime"])
                    except (ValueError, TypeError):
                        normalized["createTime"] = datetime.now(timezone.utc)
                
                if isinstance(normalized.get("updateTime"), str):
                    try:
                        normalized["updateTime"] = datetime.fromisoformat(normalized["updateTime"])
                    except (ValueError, TypeError):
                        normalized["updateTime"] = None
                
                if isinstance(normalized.get("fetchedAt"), str):
                    try:
                        normalized["fetchedAt"] = datetime.fromisoformat(normalized["fetchedAt"])
                    except (ValueError, TypeError):
                        normalized["fetchedAt"] = datetime.now(timezone.utc)
                
                if isinstance(normalized.get("review_date"), str):
                    try:
                        normalized["review_date"] = datetime.fromisoformat(normalized["review_date"]).date()
                    except (ValueError, TypeError):
                        pass
                
                normalized_reviews.append(normalized)
            except ValueError as e:
                logger.warning(f"⚠️ Validation error at index {idx}: {e}")
                raise HTTPException(status_code=400, detail=f"Invalid review data at index {idx}: {str(e)}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to normalize review at index {idx}: {e}")
                raise HTTPException(status_code=400, detail=f"Invalid review data at index {idx}: {str(e)}")
        
        if batch_duplicates and not skip_duplicates:
            raise ValueError(f"Found {len(batch_duplicates)} duplicate reviewIds in batch")
        
        existing_count = 0
        if skip_duplicates or operation in ("append_dedup", "merge"):
            try:
                incoming_review_ids = [r.get("reviewId") for r in normalized_reviews]
                if incoming_review_ids:
                    placeholders = ",".join(["%s"] * len(incoming_review_ids))
                    existing_rows = db.execute_query(
                        f"SELECT reviewId FROM {TABLE_NAME} WHERE reviewId IN ({placeholders})",
                        tuple(incoming_review_ids)
                    )
                    existing_ids = {row["reviewId"] for row in existing_rows}
                    existing_count = len(existing_ids)
                    
                    if skip_duplicates and existing_ids:
                        logger.info(f"🔍 Found {existing_count} existing reviews in database, filtering...")
                        normalized_reviews = [r for r in normalized_reviews if r.get("reviewId") not in existing_ids]
            except Exception as e:
                logger.warning(f"⚠️ Database duplicate check failed: {e}")
        
        if not normalized_reviews:
            return {
                "status": "success",
                "message": "No new reviews to upload (all skipped due to duplicates)",
                "rows_processed": 0,
                "total_reviews_submitted": len(reviews),
                "batch_duplicates": len(batch_duplicates),
                "database_duplicates": existing_count,
                "operation": operation,
                "duration_seconds": round(time.time() - start_time, 2),
                "duplicate_details": batch_duplicates[:100] if batch_duplicates else []
            }
        
        result = optimized_batch_write_with_dedup(
            reviews=normalized_reviews,
            operation=operation if operation != "append_dedup" else "append",
            update_aggregates=True
        )
        
        result["duration_seconds"] = round(time.time() - start_time, 2)
        result["total_reviews_submitted"] = len(reviews)
        result["batch_duplicates"] = len(batch_duplicates)
        result["database_duplicates"] = existing_count
        result["rows_deduped"] = len(reviews) - len(normalized_reviews)
        
        cache_store.clear()
        cache_timestamps.clear()
        
        logger.info(f"✅ Bulk upload completed: {result}")
        return result
    
    except ValueError as exc:
        logger.warning(f"Validation error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Bulk upload failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.post("/validate-bulk-reviews")
async def validate_bulk_reviews(
    reviews: List[BulkReviewItem] = Body(..., description="List of reviews to validate")
):
    start_time = time.time()
    try:
        from app.connections import db
        
        if not reviews:
            raise ValueError("Reviews list cannot be empty")
        
        batch_duplicates = []
        review_ids_in_batch = {}
        valid_review_ids = []
        
        for idx, review in enumerate(reviews):
            review_id = review.reviewId
            
            if not review_id:
                raise ValueError(f"reviewId is required at index {idx}")
            
            if review_id in review_ids_in_batch:
                batch_duplicates.append({
                    "reviewId": review_id,
                    "index": idx,
                    "first_occurrence_index": review_ids_in_batch[review_id],
                    "reason": "Duplicate within submitted batch"
                })
            else:
                review_ids_in_batch[review_id] = idx
                valid_review_ids.append(review_id)
        
        existing_count = 0
        existing_duplicates = []
        
        if valid_review_ids:
            placeholders = ",".join(["%s"] * len(valid_review_ids))
            existing_rows = db.execute_query(
                f"SELECT reviewId, location_id, storeCode, createTime FROM {TABLE_NAME} WHERE reviewId IN ({placeholders})",
                tuple(valid_review_ids)
            )
            
            if existing_rows:
                existing_count = len(existing_rows)
                existing_duplicates = [
                    {
                        "reviewId": row["reviewId"],
                        "location_id": row["location_id"],
                        "storeCode": row["storeCode"],
                        "existing_since": row["createTime"].isoformat() if row["createTime"] else None,
                        "reason": "Already exists in database"
                    }
                    for row in existing_rows
                ]
        
        new_count = len(valid_review_ids) - existing_count
        
        return {
            "total_reviews_in_batch": len(reviews),
            "valid_reviews": len(valid_review_ids),
            "new_reviews": new_count,
            "batch_duplicates": len(batch_duplicates),
            "database_duplicates": existing_count,
            "total_duplicates": len(batch_duplicates) + existing_count,
            "validation_duration_seconds": round(time.time() - start_time, 3),
            "batch_duplicate_details": batch_duplicates[:50],
            "database_duplicate_details": existing_duplicates[:50],
            "ready_to_upload": new_count > 0,
            "upload_recommendation": {
                "operation": "append_dedup" if new_count > 0 else "skip",
                "reason": f"Will insert {new_count} new reviews" if new_count > 0 else "No new reviews found"
            }
        }
    
    except ValueError as exc:
        logger.warning(f"Validation error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Bulk validation failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/bulk-delete-locations")
async def bulk_delete_locations(
    file: UploadFile = File(..., description="CSV file with storeCode column to KEEP"),
    admin_key: str = Query(..., description="Admin authentication key"),
    dry_run: bool = Query(True, description="Preview deletion without executing"),
):
    start_time = time.time()
    try:
        from app.connections import db
        
        if not admin_key or admin_key != settings.ADMIN_KEY:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid admin key")
        
        if not file.filename.endswith('.csv'):
            raise ValueError("File must be in CSV format (.csv)")
        
        logger.info(f"📥 Bulk location deletion started (dry_run={dry_run})")
        
        contents = await file.read()
        content_str = contents.decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(content_str))
        
        keep_store_codes = set()
        for idx, row in enumerate(csv_reader):
            store_code = row.get('storeCode') or row.get('store_code') or row.get('StoreCode')
            if store_code:
                keep_store_codes.add(store_code.strip())
        
        if not keep_store_codes:
            raise ValueError("No valid storeCodes found in CSV file")
        
        logger.info(f"📋 CSV parsed successfully: {len(keep_store_codes)} store codes to KEEP")
        
        all_store_codes = db.execute_query("SELECT DISTINCT storeCode FROM locations WHERE storeCode IS NOT NULL AND storeCode != ''")
        all_codes_set = {row["storeCode"] for row in all_store_codes}
        
        delete_store_codes = all_codes_set - keep_store_codes
        
        logger.info(f"📊 Deletion analysis:")
        logger.info(f"   - Total store codes in database: {len(all_codes_set)}")
        logger.info(f"   - Store codes to KEEP (from CSV): {len(keep_store_codes)}")
        logger.info(f"   - Store codes to DELETE: {len(delete_store_codes)}")
        
        if not delete_store_codes:
            return {
                "status": "success",
                "message": "No store codes to delete",
                "total_store_codes": len(all_codes_set),
                "keep_store_codes": len(keep_store_codes),
                "delete_store_codes": 0,
                "dry_run": dry_run,
                "affected_locations": 0,
                "affected_reviews": 0,
                "affected_ratings": 0,
                "duration_seconds": round(time.time() - start_time, 2)
            }
        
        placeholders = ",".join(["%s"] * len(delete_store_codes))
        
        locations_to_delete = db.execute_query(
            f"SELECT name, storeCode FROM locations WHERE storeCode IN ({placeholders})",
            tuple(delete_store_codes)
        )
        location_names_to_delete = {row["name"] for row in locations_to_delete}
        
        logger.info(f"🗂️ Found {len(location_names_to_delete)} locations to delete")
        
        location_placeholders = ",".join(["%s"] * len(location_names_to_delete)) if location_names_to_delete else "()"
        
        reviews_count = 0
        ratings_count = 0
        locations_count = len(locations_to_delete)
        
        if location_names_to_delete:
            reviews_result = db.execute_query(
                f"SELECT COUNT(*) as cnt FROM location_reviews WHERE name IN ({location_placeholders})",
                tuple(location_names_to_delete) if location_placeholders != "()" else ()
            )
            reviews_count = reviews_result[0]["cnt"] if reviews_result else 0
            
            ratings_result = db.execute_query(
                f"SELECT COUNT(*) as cnt FROM location_ratings WHERE name IN ({location_placeholders})",
                tuple(location_names_to_delete) if location_placeholders != "()" else ()
            )
            ratings_count = ratings_result[0]["cnt"] if ratings_result else 0
        
        logger.info(f"📈 Estimated deletion impact:")
        logger.info(f"   - Locations to delete: {locations_count}")
        logger.info(f"   - Reviews to delete: {reviews_count}")
        logger.info(f"   - Ratings records to delete: {ratings_count}")
        
        if dry_run:
            logger.info(f"🔍 DRY RUN MODE - No data deleted")
            return {
                "status": "success",
                "message": "DRY RUN: Deletion would proceed (no actual deletion performed)",
                "total_store_codes": len(all_codes_set),
                "keep_store_codes": len(keep_store_codes),
                "delete_store_codes": len(delete_store_codes),
                "store_codes_to_delete": sorted(list(delete_store_codes))[:50],
                "dry_run": True,
                "affected_locations": locations_count,
                "affected_reviews": reviews_count,
                "affected_ratings": ratings_count,
                "location_details": [
                    {"name": loc["name"], "storeCode": loc["storeCode"]}
                    for loc in locations_to_delete[:50]
                ],
                "duration_seconds": round(time.time() - start_time, 2),
                "next_step": "Call again with dry_run=false to execute deletion"
            }
        
        deletion_counts = {"locations": 0, "reviews": 0, "ratings": 0, "region": 0, "errors": []}
        
        try:
            if location_names_to_delete:
                db.execute_update(
                    f"DELETE FROM location_reviews WHERE name IN ({location_placeholders})",
                    tuple(location_names_to_delete) if location_placeholders != "()" else ()
                )
                deletion_counts["reviews"] = reviews_count
                logger.info(f"✅ Deleted {reviews_count} reviews")
        except Exception as e:
            deletion_counts["errors"].append(f"Error deleting reviews: {str(e)}")
            logger.error(f"❌ Error deleting reviews: {e}")
        
        try:
            if location_names_to_delete:
                db.execute_update(
                    f"DELETE FROM location_ratings WHERE name IN ({location_placeholders})",
                    tuple(location_names_to_delete) if location_placeholders != "()" else ()
                )
                deletion_counts["ratings"] = ratings_count
                logger.info(f"✅ Deleted {ratings_count} rating records")
        except Exception as e:
            deletion_counts["errors"].append(f"Error deleting ratings: {str(e)}")
            logger.error(f"❌ Error deleting ratings: {e}")
        
        try:
            db.execute_update(
                f"DELETE FROM locations WHERE storeCode IN ({placeholders})",
                tuple(delete_store_codes)
            )
            deletion_counts["locations"] = locations_count
            logger.info(f"✅ Deleted {locations_count} locations")
        except Exception as e:
            deletion_counts["errors"].append(f"Error deleting locations: {str(e)}")
            logger.error(f"❌ Error deleting locations: {e}")
        
        try:
            db.execute_update(
                f"DELETE FROM region WHERE storeCode IN ({placeholders})",
                tuple(delete_store_codes)
            )
            region_result = db.execute_query(
                f"SELECT COUNT(*) as cnt FROM region WHERE storeCode IN ({placeholders})",
                tuple(delete_store_codes)
            )
            deletion_counts["region"] = region_result[0]["cnt"] if region_result else 0
        except Exception as e:
            logger.warning(f"⚠️ Could not delete from region table: {e}")
        
        cache_store.clear()
        cache_timestamps.clear()
        
        logger.info(f"✅ Bulk deletion completed: {deletion_counts}")
        
        return {
            "status": "success",
            "message": f"Successfully deleted data for {len(delete_store_codes)} store codes",
            "total_store_codes": len(all_codes_set),
            "keep_store_codes": len(keep_store_codes),
            "delete_store_codes": len(delete_store_codes),
            "store_codes_deleted": sorted(list(delete_store_codes)),
            "dry_run": False,
            "deletion_summary": {
                "locations_deleted": deletion_counts["locations"],
                "reviews_deleted": deletion_counts["reviews"],
                "ratings_deleted": deletion_counts["ratings"],
                "region_records_deleted": deletion_counts["region"]
            },
            "total_records_deleted": deletion_counts["locations"] + deletion_counts["reviews"] + deletion_counts["ratings"],
            "errors": deletion_counts["errors"] if deletion_counts["errors"] else None,
            "duration_seconds": round(time.time() - start_time, 2)
        }
    
    except ValueError as exc:
        logger.warning(f"Validation error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Bulk deletion failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/review-history/{review_id}")
async def get_review_history(
    review_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("created_at", pattern="^(created_at|action|modified_by)$"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$")
):
    try:
        from app.connections import db
        
        if not review_id or not review_id.strip():
            raise ValueError("review_id is required")
        
        query = f"""
            SELECT 
                id,
                review_id,
                location_id,
                action,
                old_reply,
                new_reply,
                old_sentiment,
                new_sentiment,
                old_emotion,
                new_emotion,
                old_attributes,
                new_attributes,
                old_quality_score,
                new_quality_score,
                gmb_posted,
                gmb_error,
                modified_by,
                created_at
            FROM review_history
            WHERE review_id = %s
            ORDER BY {sort_by} {sort_order}
            LIMIT %s OFFSET %s
        """
        
        history_records = db.execute_query(query, (review_id, limit, offset))
        
        total_query = "SELECT COUNT(*) as cnt FROM review_history WHERE review_id = %s"
        total_result = db.execute_query(total_query, (review_id,))
        total_count = total_result[0]["cnt"] if total_result else 0
        
        serialized_records = []
        for record in history_records:
            record_copy = dict(record)
            for key in record_copy:
                if isinstance(record_copy[key], datetime):
                    record_copy[key] = record_copy[key].isoformat()
            serialized_records.append(record_copy)
        
        return {
            "review_id": review_id,
            "total_records": total_count,
            "records_returned": len(serialized_records),
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "history": serialized_records,
            "pagination": {
                "total_pages": (total_count + limit - 1) // limit,
                "current_page": (offset // limit) + 1 if limit > 0 else 1
            }
        }
    
    except ValueError as exc:
        logger.warning(f"Validation error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Failed to fetch review history: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/review-history")
async def list_review_history(
    location_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    modified_by: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("created_at", pattern="^(created_at|review_id|location_id|action|modified_by)$"),
    sort_order: str = Query("DESC", pattern="^(ASC|DESC)$")
):
    try:
        from app.connections import db
        
        where_clauses = []
        params = []
        
        if location_id:
            where_clauses.append("location_id = %s")
            params.append(location_id)
        
        if action:
            where_clauses.append("action = %s")
            params.append(action)
        
        if modified_by:
            where_clauses.append("modified_by = %s")
            params.append(modified_by)
        
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date)
                where_clauses.append("created_at >= %s")
                params.append(start_dt)
            except ValueError:
                raise ValueError("Invalid start_date format (use ISO 8601)")
        
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date)
                where_clauses.append("created_at <= %s")
                params.append(end_dt)
            except ValueError:
                raise ValueError("Invalid end_date format (use ISO 8601)")
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        params.append(limit)
        params.append(offset)
        
        query = f"""
            SELECT 
                id,
                review_id,
                location_id,
                action,
                old_reply,
                new_reply,
                old_sentiment,
                new_sentiment,
                old_emotion,
                new_emotion,
                old_attributes,
                new_attributes,
                old_quality_score,
                new_quality_score,
                gmb_posted,
                gmb_error,
                modified_by,
                created_at
            FROM review_history
            WHERE {where_sql}
            ORDER BY {sort_by} {sort_order}
            LIMIT %s OFFSET %s
        """
        
        history_records = db.execute_query(query, tuple(params))
        
        count_params = params[:-2]
        count_query = f"SELECT COUNT(*) as cnt FROM review_history WHERE {where_sql}"
        total_result = db.execute_query(count_query, tuple(count_params) if count_params else None)
        total_count = total_result[0]["cnt"] if total_result else 0
        
        serialized_records = []
        for record in history_records:
            record_copy = dict(record)
            for key in record_copy:
                if isinstance(record_copy[key], datetime):
                    record_copy[key] = record_copy[key].isoformat()
            serialized_records.append(record_copy)
        
        return {
            "total_records": total_count,
            "records_returned": len(serialized_records),
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "filters_used": {
                "location_id": location_id,
                "action": action,
                "modified_by": modified_by,
                "start_date": start_date,
                "end_date": end_date
            },
            "history": serialized_records,
            "pagination": {
                "total_pages": (total_count + limit - 1) // limit,
                "current_page": (offset // limit) + 1 if limit > 0 else 1
            }
        }
    
    except ValueError as exc:
        logger.warning(f"Validation error: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Failed to fetch review history: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/fetch-reviews")
async def fetch_reviews(
    account_id: Optional[str] = Query(None, description="Google Account ID"),
    location_ids: Optional[List[str]] = Query(None, description="Specific locations to fetch"),
    concurrency: int = Query(4, ge=1, le=20, description="Number of concurrent requests"),
    max_reviews: int = Query(30, ge=1, le=200, description="Fetch last N reviews per location"),
    only_unreplied: bool = Query(True, description="Store only unreplied reviews"),
    include_inactive: bool = Query(False, description="Include inactive locations"),
    dedup_mode: str = Query("append_dedup", pattern="^(merge|append_dedup|append)$")
):
    start_time = time.time()
    try:
        result = await fetch_reviews_for_locations(
            account_id or settings.GOOGLE_ACCOUNT_ID,
            location_ids,
            concurrency,
            max_reviews,
            only_unreplied,
            include_inactive,
            dedup_mode
        )
        result["duration_seconds"] = time.time() - start_time
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Failed to fetch reviews: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/admin/check-duplicates")
async def admin_check_duplicates(limit: int = Query(100, ge=1, le=1000)):
    try:
        result = check_duplicates(limit)
        return JSONResponse(result)
    except Exception as exc:
        logger.exception(f"Duplicate check failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/list_reviews")
def list_reviews(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    location_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    min_stars: Optional[int] = Query(None, ge=1, le=5),
    max_stars: Optional[int] = Query(None, ge=1, le=5),
    sentiment: Optional[str] = Query(None),
    has_reply: Optional[bool] = Query(None),
    region: Optional[str] = Query(None),
    reviewer_displayName: Optional[str] = Query(None),
    include_summary: bool = Query(False, description="Include total, positive/negative/neutral counts - use /summary endpoint for faster results"),
    use_cache: bool = Query(True)
):
    try:
        start_time = time.time()

        if len(cache_store) > 100:
            expired = [k for k, v in cache_timestamps.items() if time.time() - v > 1200]
            for k in expired:
                cache_store.pop(k, None)
                cache_timestamps.pop(k, None)

        cache_key = f"{location_id}:{start_date}:{end_date}:{min_stars}:{max_stars}:{sentiment}:{has_reply}:{region}:{reviewer_displayName}:{limit}:{offset}"

        if use_cache and cache_key in cache_store:
            cached_result = cache_store[cache_key]
            cached_result["from_cache"] = True
            cached_result["cache_age_seconds"] = round(time.time() - cache_timestamps[cache_key], 2)
            return Response(
                content=json.dumps(cached_result, cls=DateTimeEncoder),
                status_code=200,
                media_type="application/json"
            )

        force_no_summary = False
        if include_summary and start_date and end_date:
            try:
                start_dt = datetime.fromisoformat(start_date).date()
                end_dt = datetime.fromisoformat(end_date).date()
                days_diff = (end_dt - start_dt).days
                if days_diff > 31:
                    logger.warning("⚠️ Large date range (%d days) with include_summary - forcing summary=False. Use /summary-stats instead.", days_diff)
                    force_no_summary = True
            except Exception:
                pass
        
        result = list_reviews_optimized(
            store_code=location_id,
            start_date=start_date,
            end_date=end_date,
            min_stars=min_stars,
            max_stars=max_stars,
            sentiment=sentiment,
            has_reply=has_reply,
            region=region,
            reviewer_displayName=reviewer_displayName,
            limit=limit,
            offset=offset,
            include_summary=include_summary and not force_no_summary
        )

        result["execution_time_seconds"] = round(time.time() - start_time, 2)
        
        if force_no_summary:
            result["note"] = "Summary disabled for large date ranges. Use GET /summary-stats endpoint for counts and aggregations."
        
        result["filters_used"] = {
            "location_id": location_id,
            "start_date": start_date,
            "end_date": end_date,
            "min_stars": min_stars,
            "max_stars": max_stars,
            "sentiment": sentiment,
            "has_reply": has_reply,
            "region": region,
            "reviewer_displayName": reviewer_displayName
        }

        cache_store[cache_key] = result
        cache_timestamps[cache_key] = time.time()

        return Response(
            content=json.dumps(result, cls=DateTimeEncoder),
            status_code=200,
            media_type="application/json"
        )

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"List reviews failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/health")
def health_check():
    try:
        health = get_table_health()
        return health
    except Exception as exc:
        logger.exception(f"Health check failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/dashboard")
def dashboard_summary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    location_ids: Optional[List[str]] = Query(None)
):
    try:
        start = datetime.fromisoformat(start_date).date() if start_date else None
        end = datetime.fromisoformat(end_date).date() if end_date else None

        summary = get_dashboard_summary(start, end, location_ids)
        return summary
    except Exception as exc:
        logger.exception(f"Dashboard summary failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/performance/{location_id}")
def location_performance(location_id: str):
    try:
        perf = get_location_performance(location_id)
        return perf
    except Exception as exc:
        logger.exception(f"Performance query failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/clear_cache")
def clear_all_cache(admin_key: str = Query(...)):
    if not admin_key or admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        cache_store.clear()
        cache_timestamps.clear()
        return {"message": "Cache cleared", "cleared": True}
    except Exception as exc:
        logger.exception(f"Cache clear failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.post("/admin/clear-table")
def clear_table(admin_key: str = Query(...)):
    if not admin_key or admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        result = clear_all_reviews()
        cache_store.clear()
        cache_timestamps.clear()
        return result
    except Exception as exc:
        logger.exception(f"Clear table failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/summary-stats")
def get_summary_stats(
    location_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    min_stars: Optional[int] = Query(None, ge=1, le=5),
    max_stars: Optional[int] = Query(None, ge=1, le=5),
    sentiment: Optional[str] = Query(None),
    has_reply: Optional[bool] = Query(None),
    region: Optional[str] = Query(None),
    use_cache: bool = Query(True)
):
    try:
        start_time = time.time()
        
        cache_key = f"summary:{location_id}:{start_date}:{end_date}:{min_stars}:{max_stars}:{sentiment}:{has_reply}:{region}"
        
        if use_cache and cache_key in cache_store:
            cached_result = cache_store[cache_key]
            cached_result["from_cache"] = True
            cached_result["cache_age_seconds"] = round(time.time() - cache_timestamps[cache_key], 2)
            return Response(
                content=json.dumps(cached_result, cls=DateTimeEncoder),
                status_code=200,
                media_type="application/json"
            )
        
        result = list_reviews_optimized(
            store_code=location_id,
            start_date=start_date,
            end_date=end_date,
            min_stars=min_stars,
            max_stars=max_stars,
            sentiment=sentiment,
            has_reply=has_reply,
            region=region,
            limit=1,
            offset=0,
            include_summary=True
        )
        
        summary_result = {
            "summary_counts": result.get("summary_counts", {}),
            "pagination": {
                "total_pages": (result["summary_counts"].get("total_reviews", 0) + 99) // 100
            },
            "filters_used": {
                "location_id": location_id,
                "start_date": start_date,
                "end_date": end_date,
                "min_stars": min_stars,
                "max_stars": max_stars,
                "sentiment": sentiment,
                "has_reply": has_reply,
                "region": region
            },
            "execution_time_seconds": round(time.time() - start_time, 2),
            "from_cache": False
        }
        
        cache_store[cache_key] = summary_result
        cache_timestamps[cache_key] = time.time()
        
        return Response(
            content=json.dumps(summary_result, cls=DateTimeEncoder),
            status_code=200,
            media_type="application/json"
        )
    
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.exception(f"Summary stats failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/diagnostics/table-stats")
def get_table_diagnostics():
    try:
        from app.connections import db
        
        total_rows = db.execute_query("SELECT COUNT(*) as cnt FROM location_reviews")[0]["cnt"]
        unique_dates = db.execute_query("SELECT COUNT(DISTINCT DATE(createTime)) as cnt FROM location_reviews")[0]["cnt"]
        unique_locations = db.execute_query("SELECT COUNT(DISTINCT name) as cnt FROM location_reviews")[0]["cnt"]
        
        date_range = db.execute_query(
            "SELECT MIN(DATE(createTime)) as min_date, MAX(DATE(createTime)) as max_date FROM location_reviews"
        )[0]
        
        reviews_per_date = total_rows // max(unique_dates, 1) if unique_dates > 0 else 0
        
        return {
            "table_name": TABLE_NAME,
            "total_rows": total_rows,
            "unique_dates": unique_dates,
            "unique_locations": unique_locations,
            "avg_reviews_per_date": reviews_per_date,
            "date_range": {
                "min": date_range["min_date"].isoformat() if date_range["min_date"] else None,
                "max": date_range["max_date"].isoformat() if date_range["max_date"] else None
            },
            "optimization_tip": "If avg_reviews_per_date > 100k, ensure table is indexed on createTime for better performance."
        }
    
    except Exception as exc:
        logger.exception(f"Table diagnostics failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/cache_stats")
def get_cache_stats():
    try:
        return {
            "review_cache_size": len(cache_store),
            "total_cached_items": len(cache_store)
        }
    except Exception as exc:
        logger.exception(f"Cache stats failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/export/reviews")
def export_reviews(
    format: str = Query("csv", pattern="^(csv|json)$"),
    location_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    min_stars: Optional[int] = Query(None, ge=1, le=5),
    max_stars: Optional[int] = Query(None, ge=1, le=5),
    sentiment: Optional[str] = Query(None),
    has_reply: Optional[bool] = Query(None),
    region: Optional[str] = Query(None)
):
    try:
        start_time = time.time()
        
        result = list_reviews_optimized(
            store_code=location_id,
            start_date=start_date,
            end_date=end_date,
            min_stars=min_stars,
            max_stars=max_stars,
            sentiment=sentiment,
            has_reply=has_reply,
            region=region,
            limit=100000,
            offset=0,
            include_summary=True
        )
        
        reviews = result.get("reviews", [])
        
        reviews_serialized = []
        for review in reviews:
            review_copy = dict(review)
            for key in review_copy:
                if isinstance(review_copy[key], date) and not isinstance(review_copy[key], datetime):
                    review_copy[key] = review_copy[key].isoformat()
                elif isinstance(review_copy[key], datetime):
                    review_copy[key] = review_copy[key].isoformat()
            reviews_serialized.append(review_copy)
        
        if format == "json":
            export_data = {
                "export_date": datetime.now(timezone.utc).isoformat(),
                "filters": {
                    "location_id": location_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "min_stars": min_stars,
                    "max_stars": max_stars,
                    "sentiment": sentiment,
                    "has_reply": has_reply,
                    "region": region
                },
                "summary": result.get("summary_counts", {}),
                "reviews": reviews_serialized,
                "export_duration_seconds": round(time.time() - start_time, 2)
            }
            
            return Response(
                content=json.dumps(export_data, cls=DateTimeEncoder),
                status_code=200,
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename=reviews_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"}
            )
        
        else:
            output = io.StringIO()
            if reviews_serialized:
                fieldnames = list(reviews_serialized[0].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(reviews_serialized)
            
            csv_content = output.getvalue()
            output.close()
            
            return StreamingResponse(
                iter([csv_content]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=reviews_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"}
            )
    
    except Exception as exc:
        logger.exception("Export reviews failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to export reviews")


@router.get("/export/location-summary")
def export_location_summary(
    format: str = Query("csv", pattern="^(csv|json)$"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    location_ids: Optional[List[str]] = Query(None)
):
    try:
        from app.connections import db
        
        start_time = time.time()
        
        where_clauses = []
        params = []
        
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
        
        if location_ids:
            placeholders = ",".join(["%s"] * len(location_ids))
            where_clauses.append(f"l.storeCode IN ({placeholders})")
            params.extend(location_ids)
        
        location_where = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        date_conditions = []
        if start_date or end_date:
            conditions = []
            if start_date:
                conditions.append("DATE(lr.createTime) >= %s")
            if end_date:
                conditions.append("DATE(lr.createTime) <= %s")
            date_conditions = " AND ".join(conditions)
            review_join = f"LEFT JOIN location_reviews lr ON l.name = lr.name AND ({date_conditions})"
        else:
            review_join = "LEFT JOIN location_reviews lr ON l.name = lr.name"
        
        sql = f"""
        SELECT 
            l.name as location_id,
            l.storeCode,
            l.title,
            COUNT(lr.reviewId) as total_reviews,
            AVG(CAST(lr.rating as DECIMAL(3,2))) as average_rating,
            SUM(CASE WHEN lr.rating > 3 THEN 1 ELSE 0 END) as positive_reviews,
            SUM(CASE WHEN lr.rating < 3 THEN 1 ELSE 0 END) as negative_reviews,
            SUM(CASE WHEN lr.rating = 3 THEN 1 ELSE 0 END) as neutral_reviews,
            SUM(CASE WHEN (lr.comment IS NULL OR lr.comment = '') THEN 1 ELSE 0 END) as unreplied_count,
            MAX(lr.fetchedAt) as last_fetched
        FROM locations l
        {review_join}
        WHERE {location_where}
        GROUP BY l.name, l.storeCode, l.title
        """
        
        summary_data = db.execute_query(sql, tuple(params))
        
        summaries = []
        for row in summary_data:
            summaries.append({
                "location_id": row["location_id"],
                "store_code": row["storeCode"] or "",
                "title": row["title"] or "",
                "total_reviews": row["total_reviews"],
                "average_rating": round(float(row["average_rating"] or 0), 2),
                "positive_reviews": row["positive_reviews"],
                "negative_reviews": row["negative_reviews"],
                "neutral_reviews": row["neutral_reviews"],
                "unreplied_count": row["unreplied_count"],
                "last_fetched": row["last_fetched"].isoformat() if row["last_fetched"] else None
            })
        
        if format == "json":
            export_data = {
                "export_date": datetime.now(timezone.utc).isoformat(),
                "filters": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "location_ids": location_ids
                },
                "location_summaries": summaries,
                "total_locations": len(summaries),
                "export_duration_seconds": round(time.time() - start_time, 2)
            }
            
            return Response(
                content=json.dumps(export_data, cls=DateTimeEncoder),
                status_code=200,
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename=location_summary_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"}
            )
        
        else:
            output = io.StringIO()
            if summaries:
                fieldnames = list(summaries[0].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(summaries)
            
            csv_content = output.getvalue()
            output.close()
            
            return StreamingResponse(
                iter([csv_content]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=location_summary_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"}
            )
    
    except ValueError as ve:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as exc:
        logger.exception("Export location summary failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to export location summary")
