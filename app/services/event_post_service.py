"""Service for event post and location update operations."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.config import settings
from app.schemas.event_post import UpdateSocialLinksRequest
from app.services.google_api_service import GoogleAPIService
from app.services.iceberg_service import IcebergService


logger = logging.getLogger(__name__)


def to_date(d: str) -> Dict[str, int]:
    """Convert YYYY-MM-DD to Google API date format."""
    dt = datetime.strptime(d, "%Y-%m-%d")
    return {"year": dt.year, "month": dt.month, "day": dt.day}


def to_time(t: str) -> Dict[str, int]:
    """Convert HH:MM or HH:MM:SS to Google API time format."""
    try:
        tm = datetime.strptime(t, "%H:%M:%S")
    except ValueError:
        tm = datetime.strptime(t, "%H:%M")
    return {"hours": tm.hour, "minutes": tm.minute, "seconds": 0, "nanos": 0}


class EventPostService:
    """Service for creating and managing event posts and location updates."""

    @staticmethod
    async def create_posts(data: Any) -> Dict[str, Any]:
        """
        Create posts across multiple locations.

        Args:
            data: CreateBulkPostRequest object with post details

        Returns:
            Dictionary with batch results and status
        """
        try:
            batch_id = str(uuid.uuid4())
            location_ids = data.location_ids or []
            
            if not location_ids:
                return {
                    "status": "error",
                    "message": "No location IDs provided",
                    "batch_id": batch_id,
                }

            def build_payload(location_id: str) -> Dict[str, Any]:
                """Build post payload for a location matching Google My Business API format."""
                post: Dict[str, Any] = {
                    "topicType": data.topic_type.upper(),
                    "languageCode": data.language_code,
                }

                if data.topic_type.upper() == "EVENT":
                    post["summary"] = data.summary or data.event_title or ""
                    post["event"] = {
                        "title": data.event_title,
                        "schedule": {
                            "startDate": to_date(data.start_date),
                            "endDate": to_date(data.end_date),
                            "startTime": to_time(data.start_time),
                            "endTime": to_time(data.end_time),
                        },
                    }
                else:
                    post["summary"] = data.summary or ""

                post["media"] = []
                if data.media:
                    post["media"] = [
                        {
                            "mediaFormat": m.media_format,
                            "sourceUrl": str(m.source_url),
                        }
                        for m in data.media
                    ]
                elif data.media_url:
                    post["media"] = [
                        {
                            "mediaFormat": "PHOTO",
                            "sourceUrl": str(data.media_url),
                        }
                    ]

                if data.call_to_action:
                    post["callToAction"] = {
                        "actionType": data.call_to_action.action_type,
                    }
                    if data.call_to_action.action_type.upper() != "CALL":
                        post["callToAction"]["url"] = str(data.call_to_action.url)

                return post

            results = await GoogleAPIService.create_posts(
                location_ids, build_payload
            )

            batch_doc = {
                "batch_id": batch_id,
                "account_id": settings.GOOGLE_ACCOUNT_ID,
                "title_for_file": data.event_title or data.summary or f"Post Batch {batch_id[:8]}",
                "topic_type": data.topic_type.upper(),
                "location_count": len(location_ids),
                "locations": location_ids,
                "results": results,
                "created_by": data.created_by,
            }

            persistence_error = None
            try:
                await IcebergService.save_event_post_batch(**batch_doc)
                
                for result in results:
                    post_id = result.get("post_id")
                    location_id = result.get("location_id")
                    status = result.get("status", "unknown")
                    if post_id and location_id:
                        await IcebergService.log_post_history(
                            batch_id=batch_id,
                            post_id=post_id,
                            location_id=location_id,
                            action="CREATE",
                            status=status,
                            user=data.created_by,
                            details={
                                "topic_type": data.topic_type.upper(),
                                "event_title": data.event_title,
                                "summary": data.summary,
                            },
                        )
            except Exception as e:
                persistence_error = str(e)
                logger.error(f"Failed to persist batch: {e}")
            
            success_count = sum(
                1 for r in results if r.get("status") == "success"
            )
            error_count = len(results) - success_count

            return {
                "status": "success" if error_count == 0 else "partial",
                "batch_id": batch_id,
                "title": batch_doc.get("title_for_file"),
                "locations_processed": len(results),
                "locations_succeeded": success_count,
                "locations_failed": error_count,
                "results": results,
                "persistence_error": persistence_error,
            }

        except Exception as exc:
            logger.exception("Failed to create posts: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "batch_id": batch_id if 'batch_id' in locals() else None,
            }

    @staticmethod
    async def update_description(
        location_ids: List[str], description: str, modified_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update description for multiple locations."""
        try:
            def make_body_and_mask(location_id: str) -> tuple:
                return (
                    {"description": description},
                    ["description"],
                )

            results = await GoogleAPIService.bulk_update(
                "update_description", location_ids, make_body_and_mask
            )

            success_count = sum(
                1 for r in results if r.get("status") == "success"
            )

            return {
                "status": "success" if success_count == len(results) else "partial",
                "operation": "update_description",
                "locations_updated": success_count,
                "locations_failed": len(results) - success_count,
                "results": results,
                "modified_by": modified_by,
            }

        except Exception as exc:
            logger.exception("Failed to update descriptions: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "operation": "update_description",
            }

    @staticmethod
    async def update_phone(
        location_ids: List[str], phone: str, modified_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update phone number for multiple locations."""
        try:
            def make_body_and_mask(location_id: str) -> tuple:
                return (
                    {
                        "phoneNumbers": {
                            "primaryPhone": phone,
                        }
                    },
                    ["phoneNumbers.primaryPhone"],
                )

            results = await GoogleAPIService.bulk_update(
                "update_phone", location_ids, make_body_and_mask
            )

            success_count = sum(
                1 for r in results if r.get("status") == "success"
            )

            return {
                "status": "success" if success_count == len(results) else "partial",
                "operation": "update_phone",
                "locations_updated": success_count,
                "locations_failed": len(results) - success_count,
                "results": results,
                "modified_by": modified_by,
            }

        except Exception as exc:
            logger.exception("Failed to update phone numbers: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "operation": "update_phone",
            }

    @staticmethod
    async def update_website(
        location_ids: List[str], website_url: str, modified_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update website URL for multiple locations."""
        try:
            def make_body_and_mask(location_id: str) -> tuple:
                return (
                    {
                        "websiteUrl": website_url,
                    },
                    ["websiteUrl"],
                )

            results = await GoogleAPIService.bulk_update(
                "update_website", location_ids, make_body_and_mask
            )

            success_count = sum(
                1 for r in results if r.get("status") == "success"
            )

            return {
                "status": "success" if success_count == len(results) else "partial",
                "operation": "update_website",
                "locations_updated": success_count,
                "locations_failed": len(results) - success_count,
                "results": results,
                "modified_by": modified_by,
            }

        except Exception as exc:
            logger.exception("Failed to update websites: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "operation": "update_website",
            }

    @staticmethod
    async def update_social_links(req: UpdateSocialLinksRequest) -> Dict[str, Any]:
        """Update social media links for multiple locations."""
        try:
            social_links = []
            
            links_map = [
                (req.facebook_url, "Facebook"),
                (req.instagram_url, "Instagram"),
                (req.x_url, "X"),
                (req.linkedin_url, "LinkedIn"),
                (req.youtube_url, "YouTube"),
            ]
            
            for url, platform in links_map:
                if url:
                    social_links.append({
                        "uri": url,
                        "platform": platform,
                    })

            if not social_links:
                return {
                    "status": "error",
                    "message": "No social links provided",
                    "operation": "update_social_links",
                }

            def make_body_and_mask(location_id: str) -> tuple:
                return (
                    {
                        "serviceArea": {
                            "businessType": "CUSTOMER_VISIT",
                            "areas": [],
                        },
                        "profiles": social_links,
                    },
                    ["profiles"],
                )

            results = await GoogleAPIService.bulk_update(
                "update_social_links", req.location_ids, make_body_and_mask
            )

            success_count = sum(
                1 for r in results if r.get("status") == "success"
            )

            return {
                "status": "success" if success_count == len(results) else "partial",
                "operation": "update_social_links",
                "locations_updated": success_count,
                "locations_failed": len(results) - success_count,
                "results": results,
                "modified_by": req.modified_by,
            }

        except Exception as exc:
            logger.exception("Failed to update social links: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "operation": "update_social_links",
            }

    @staticmethod
    async def update_hours(
        location_ids: List[str],
        periods: List[Dict[str, str]],
        modified_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update business hours for multiple locations."""
        try:
            def make_body_and_mask(location_id: str) -> tuple:
                return (
                    {
                        "regularHours": {
                            "periods": periods,
                        }
                    },
                    ["regularHours.periods"],
                )

            results = await GoogleAPIService.bulk_update(
                "update_hours", location_ids, make_body_and_mask
            )

            success_count = sum(
                1 for r in results if r.get("status") == "success"
            )

            return {
                "status": "success" if success_count == len(results) else "partial",
                "operation": "update_hours",
                "locations_updated": success_count,
                "locations_failed": len(results) - success_count,
                "results": results,
                "modified_by": modified_by,
            }

        except Exception as exc:
            logger.exception("Failed to update hours: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "operation": "update_hours",
            }

    @staticmethod
    async def delete_posts_by_batch(
        batch_id: str, modified_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Delete all posts associated with a batch.

        Args:
            batch_id: The batch ID to delete posts from
            modified_by: User who initiated the deletion

        Returns:
            Dictionary with deletion results
        """
        try:
            batch = await IcebergService.get_event_post_batch(batch_id)

            if not batch:
                return {
                    "status": "error",
                    "message": f"Batch {batch_id} not found",
                }

            results_json = batch.get("results") or "[]"

            try:
                if isinstance(results_json, str):
                    results_list = json.loads(results_json)
                else:
                    results_list = results_json
            except Exception:
                results_list = []

            deletion_jobs = []
            for result in results_list:
                if result.get("status") == "success" and result.get("post_id"):
                    deletion_jobs.append({
                        "location_id": result.get("location_id"),
                        "post_id": result.get("post_id"),
                    })

            if not deletion_jobs:
                return {
                    "status": "success",
                    "message": "No posts to delete from batch",
                    "batch_id": batch_id,
                    "deleted_count": 0,
                }

            deletion_results = await GoogleAPIService.delete_posts(deletion_jobs)

            for result in deletion_results:
                post_id = result.get("post_id")
                location_id = result.get("location_id")
                status = result.get("status", "unknown")
                if post_id and location_id:
                    await IcebergService.log_post_history(
                        batch_id=batch_id,
                        post_id=post_id,
                        location_id=location_id,
                        action="DELETE",
                        status=status,
                        user=modified_by,
                        details={"original_batch": batch_id},
                    )

            deleted_count = sum(
                1 for r in deletion_results
                if r.get("status") in ("deleted", "not_found")
            )
            error_count = len(deletion_results) - deleted_count

            return {
                "status": "success" if error_count == 0 else "partial",
                "batch_id": batch_id,
                "posts_deleted": deleted_count,
                "posts_failed": error_count,
                "results": deletion_results,
                "modified_by": modified_by,
            }

        except Exception as exc:
            logger.exception("Failed to delete posts by batch: %s", exc)
            return {
                "status": "error",
                "message": str(exc),
                "batch_id": batch_id,
            }
