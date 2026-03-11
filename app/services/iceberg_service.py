"""Service for PlanetScale database persistence operations."""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.connections import db

logger = logging.getLogger(__name__)


class PlanetScaleService:
    """Service for persisting data to PlanetScale database."""

    BATCH_TABLE = "event_posts_batches"
    HISTORY_TABLE = "post_history"

    @staticmethod
    async def save_event_post_batch(
        batch_id: str,
        account_id: str,
        title_for_file: str,
        topic_type: str,
        location_count: int,
        locations: List[str],
        results: Dict[str, Any],
        created_by: str = None,
        modified_by: str = None,
    ) -> bool:
        """Save event post batch to database."""
        try:
            sql = """
            INSERT INTO event_posts_batches 
            (batch_id, createdAt, account_id, title_for_file, topic_type, 
             location_count, locations, results, created_by, modified_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            db.execute_update(
                sql,
                (
                    batch_id,
                    datetime.now(timezone.utc),
                    account_id,
                    title_for_file,
                    topic_type,
                    location_count,
                    json.dumps(locations),
                    json.dumps(results),
                    created_by,
                    modified_by,
                ),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save event post batch: {e}")
            raise

    @staticmethod
    async def get_event_post_batches(
        account_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get event post batches for an account."""
        try:
            sql = """
            SELECT * FROM event_posts_batches 
            WHERE account_id = %s 
            ORDER BY createdAt DESC 
            LIMIT %s
            """
            results = db.execute_query(sql, (account_id, limit))
            for result in results:
                if result.get("locations"):
                    result["locations"] = json.loads(result["locations"])
                if result.get("results"):
                    result["results"] = json.loads(result["results"])
            return results
        except Exception as e:
            logger.error(f"Failed to get event post batches: {e}")
            return []

    @staticmethod
    async def get_event_post_batch(batch_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific event post batch."""
        try:
            sql = "SELECT * FROM event_posts_batches WHERE batch_id = %s"
            results = db.execute_query(sql, (batch_id,))
            if results:
                result = results[0]
                if result.get("locations"):
                    result["locations"] = json.loads(result["locations"])
                if result.get("results"):
                    result["results"] = json.loads(result["results"])
                return result
            return None
        except Exception as e:
            logger.error(f"Failed to get event post batch: {e}")
            return None

    @staticmethod
    async def get_batch(batch_id: str) -> Optional[Dict[str, Any]]:
        """Alias for get_event_post_batch."""
        return await PlanetScaleService.get_event_post_batch(batch_id)

    @staticmethod
    async def delete_batch(batch_id: str) -> Dict[str, Any]:
        """Delete a batch record from the database."""
        try:
            sql = "DELETE FROM event_posts_batches WHERE batch_id = %s"
            db.execute_update(sql, (batch_id,))
            return {
                "status": "success",
                "message": f"Batch {batch_id} deleted",
                "batch_id": batch_id,
            }
        except Exception as e:
            logger.error(f"Failed to delete batch: {e}")
            return {
                "status": "error",
                "message": str(e),
                "batch_id": batch_id,
            }

    @staticmethod
    async def log_post_history(
        batch_id: str,
        post_id: str,
        location_id: str,
        action: str,
        status: str,
        user: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Log post history for audit trail."""
        try:
            sql = """
            INSERT INTO post_history 
            (batch_id, post_id, location_id, action, status, modified_by, details, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            db.execute_update(
                sql,
                (
                    batch_id,
                    post_id,
                    location_id,
                    action,
                    status,
                    user,
                    json.dumps(details) if details else None,
                    datetime.now(timezone.utc),
                ),
            )
            return True
        except Exception as e:
            logger.error(f"Failed to log post history: {e}")
            return False

    @staticmethod
    async def get_post_history(
        post_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        location_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get post history with optional filters."""
        try:
            sql = "SELECT * FROM post_history WHERE 1=1"
            params: List[Any] = []
            
            if batch_id:
                sql += " AND batch_id = %s"
                params.append(batch_id)
            if post_id:
                sql += " AND post_id = %s"
                params.append(post_id)
            if location_id:
                sql += " AND location_id = %s"
                params.append(location_id)
            
            sql += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            
            results = db.execute_query(sql, tuple(params))
            for result in results:
                if result.get("details"):
                    result["details"] = json.loads(result["details"])
            return results
        except Exception as e:
            logger.error(f"Failed to get post history: {e}")
            return []


IcebergService = PlanetScaleService
