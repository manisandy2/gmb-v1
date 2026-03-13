import asyncio
import logging
from typing import List, Dict, Any, Tuple

from app.db.connection import db

logger = logging.getLogger(__name__)


async def bulk_upsert_review_analytics(
    analytics_rows: List[Dict[str, Any]],
) -> int:
    """
    Bulk upsert review analytics for locations.
    """

    if not analytics_rows:
        return 0

    loop = asyncio.get_running_loop()

    def _upsert():

        sql = """
        INSERT INTO review_analytics
        (
            location_name,
            date,
            total_reviews,
            positive_reviews,
            negative_reviews,
            neutral_reviews,
            average_rating,
            reply_rate
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            total_reviews = VALUES(total_reviews),
            positive_reviews = VALUES(positive_reviews),
            negative_reviews = VALUES(negative_reviews),
            neutral_reviews = VALUES(neutral_reviews),
            average_rating = VALUES(average_rating),
            reply_rate = VALUES(reply_rate),
            updated_at = CURRENT_TIMESTAMP
        """

        count = 0

        for row in analytics_rows:

            try:

                db.execute_update(
                    sql,
                    (
                        row.get("location_name"),
                        row.get("date"),
                        row.get("total_reviews", 0),
                        row.get("positive_reviews", 0),
                        row.get("negative_reviews", 0),
                        row.get("neutral_reviews", 0),
                        row.get("average_rating"),
                        row.get("reply_rate"),
                    ),
                )

                count += 1

            except Exception as e:
                logger.error(
                    f"Failed upserting analytics for {row.get('location_name')} "
                    f"{row.get('date')}: {e}"
                )

        return count

    return await loop.run_in_executor(None, _upsert)


async def get_location_analytics(
    location_name: str,
    start_date: str = None,
    end_date: str = None,
) -> List[Dict[str, Any]]:
    """
    Fetch analytics for a specific location.
    """

    loop = asyncio.get_running_loop()

    def _get():

        sql = """
        SELECT *
        FROM review_analytics
        WHERE location_name = %s
        """

        params = [location_name]

        if start_date:
            sql += " AND date >= %s"
            params.append(start_date)

        if end_date:
            sql += " AND date <= %s"
            params.append(end_date)

        sql += " ORDER BY date DESC"

        return db.execute_query(sql, tuple(params))

    return await loop.run_in_executor(None, _get)


async def get_batch_analytics(
    account_id: str,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetch batch analytics for event posts.
    """

    loop = asyncio.get_running_loop()

    def _get():

        count_sql = """
        SELECT COUNT(*) as cnt
        FROM event_posts_batches
        WHERE account_id = %s
        """

        count_result = db.execute_query(count_sql, (account_id,))
        total = count_result[0]["cnt"] if count_result else 0

        sql = """
        SELECT
            batch_id,
            createdAt,
            topic_type,
            location_count,
            created_by,
            modified_by
        FROM event_posts_batches
        WHERE account_id = %s
        ORDER BY createdAt DESC
        LIMIT %s OFFSET %s
        """

        results = db.execute_query(
            sql,
            (account_id, limit, offset),
        )

        return results, total

    return await loop.run_in_executor(None, _get)