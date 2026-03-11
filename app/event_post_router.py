"""Router for event post and location update operations."""
import json
import logging
from datetime import timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app.config import settings
from app.schemas.event_post import (
    CreateBulkPostRequest,
    UpdateDescriptionRequest,
    UpdateHoursRequest,
    UpdatePhoneRequest,
    UpdateSocialLinksRequest,
    UpdateWebsiteRequest,
)
from app.services.event_post_service import EventPostService
from app.services.google_api_service import GoogleAPIService
from app.services.iceberg_service import PlanetScaleService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/gmb/create-post")
async def create_bulk_post(data: CreateBulkPostRequest = Body(...)) -> Dict[str, Any]:
    """
    Create posts across multiple locations.

    Validates event-specific fields and creates posts via Google My Business API.
    """
    if data.topic_type.upper() == "EVENT":
        required_fields = [
            data.event_title,
            data.start_date,
            data.end_date,
            data.start_time,
            data.end_time,
        ]
        if not all(required_fields):
            raise HTTPException(
                status_code=400,
                detail="Missing event fields (event_title, start_date, end_date, start_time, end_time)",
            )

    return await EventPostService.create_posts(data)


@router.patch("/gmb/update-description")
async def update_description(req: UpdateDescriptionRequest) -> Dict[str, Any]:
    """Update location descriptions."""
    return await EventPostService.update_description(
        req.location_ids, req.description, req.modified_by
    )


@router.patch("/gmb/update-phone")
async def update_phone(req: UpdatePhoneRequest) -> Dict[str, Any]:
    """Update location phone numbers."""
    return await EventPostService.update_phone(
        req.location_ids, req.phone, req.modified_by
    )


@router.patch("/gmb/update-website")
async def update_website(req: UpdateWebsiteRequest) -> Dict[str, Any]:
    """Update location websites."""
    return await EventPostService.update_website(
        req.location_ids, str(req.website_url), req.modified_by
    )


@router.patch("/gmb/update-social-links")
async def update_social_links(req: UpdateSocialLinksRequest) -> Dict[str, Any]:
    """Update social media links."""
    links = [
        url
        for url_list in [
            (req.facebook_url, "Facebook"),
            (req.instagram_url, "Instagram"),
            (req.x_url, "X"),
            (req.linkedin_url, "LinkedIn"),
            (req.youtube_url, "YouTube"),
        ]
        if url_list[0]
        for url in [f"{url_list[1]}: {url_list[0]}"]
    ]

    if not links:
        raise HTTPException(status_code=400, detail="No social links provided")

    return await EventPostService.update_social_links(req)


@router.patch("/gmb/update-hours")
async def update_hours(req: UpdateHoursRequest) -> Dict[str, Any]:
    """Update business hours."""
    periods = [
        {
            "openDay": p.open_day,
            "openTime": p.open_time,
            "closeTime": p.close_time,
        }
        for p in req.periods
    ]

    return await EventPostService.update_hours(
        req.location_ids, periods, req.modified_by
    )


@router.get("/gmb/post_summary")
async def list_event_post_batches_summary(
    limit: int = Query(50, ge=1, le=500)
) -> Dict[str, Any]:
    """
    Get summary of recent event post batches.

    Returns batch metadata with aggregated status.
    """
    try:
        from app.connections import db
        
        sql = """
        SELECT 
            batch_id,
            createdAt,
            title_for_file,
            topic_type,
            location_count,
            created_by,
            modified_by,
            results
        FROM event_posts_batches
        ORDER BY createdAt DESC
        LIMIT %s
        """
        
        records = db.execute_query(sql, (limit,))
        
        def compute_status(topic_type: str, results_json: str) -> str:
            acceptable = {
                "deletion": {"deleted", "not_found", "success"},
                "event": {"success"},
                "default": {"success"},
            }
            acceptable_set = acceptable.get(
                (topic_type or "").lower(), acceptable["default"]
            )
            try:
                results_list = (
                    json.loads(results_json) if results_json else []
                )
            except Exception:
                results_list = []

            if not results_list:
                return "error"

            return (
                "success"
                if all(
                    r.get("status", "").strip().lower() in acceptable_set
                    for r in results_list
                )
                else "error"
            )
        
        items = []
        for record in records:
            items.append({
                "batch_id": record["batch_id"],
                "title": record.get("title_for_file"),
                "topic_type": record["topic_type"],
                "location_count": record["location_count"],
                "status": compute_status(record["topic_type"], record.get("results")),
                "created_at": record["createdAt"].strftime("%Y-%m-%d %H:%M:%S") if record["createdAt"] else None,
                "created_by": record.get("created_by"),
                "modified_by": record.get("modified_by"),
            })

        return {"items": items, "count": len(items)}

    except Exception as exc:
        logger.exception("Failed to summarize batches: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to list batch summaries: {str(exc)}"
        )


@router.get("/gmb/post/batches/{batch_id}")
async def get_batch_details(batch_id: str) -> Dict[str, Any]:
    """Get detailed information for a specific batch."""
    batch = await PlanetScaleService.get_batch(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    created_val = batch.get("createdAt")
    created_at_str = ""

    if created_val:
        try:
            created_at_str = created_val.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except Exception:
            created_at_str = str(created_val)

    results_json = batch.get("results") or "[]"

    try:
        results_list = (
            json.loads(results_json)
            if isinstance(results_json, str)
            else results_json
        )
    except Exception as exc:
        logger.exception("Failed to parse results JSON for batch %s", batch_id)
        results_list = []

    return {
        "batch_id": batch.get("batch_id"),
        "created_at": created_at_str,
        "account_id": batch.get("account_id"),
        "title": batch.get("title_for_file"),
        "topic_type": batch.get("topic_type"),
        "location_count": batch.get("location_count"),
        "locations": batch.get("locations") or [],
        "results": results_list,
        "created_by": batch.get("created_by"),
        "modified_by": batch.get("modified_by"),
    }


@router.delete("/gmb/delete-post/{batch_id}")
async def delete_posts_by_batch(
    batch_id: str, modified_by: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """
    Delete all posts associated with a batch.

    Fetches the batch, extracts successful posts, and deletes them.
    """
    return await EventPostService.delete_posts_by_batch(batch_id, modified_by)


# @router.delete("/gmb/batch/{batch_id}")
async def delete_batch_from_table(batch_id: str) -> Dict[str, Any]:
    """
    Delete a batch record completely from the database table.

    This removes the batch metadata and history entirely.
    """
    return await PlanetScaleService.delete_batch(batch_id)


@router.get("/gmb/post-history")
async def get_post_history(
    batch_id: Optional[str] = Query(None),
    post_id: Optional[str] = Query(None),
    location_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> Dict[str, Any]:
    """
    Get post modification history with optional filters.

    Query parameters:
    - batch_id: Filter by batch ID
    - post_id: Filter by specific post ID
    - location_id: Filter by location ID
    - limit: Max results (1-1000)
    """
    try:
        history = await PlanetScaleService.get_post_history(
            post_id=post_id,
            batch_id=batch_id,
            location_id=location_id,
            limit=limit,
        )
        return {
            "count": len(history),
            "items": history,
        }
    except Exception as exc:
        logger.exception("Failed to get post history: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to get post history: {str(exc)}"
        )
