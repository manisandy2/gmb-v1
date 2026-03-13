import asyncio
import logging
from typing import List, Dict, Any

from app.db.connection import db

logger = logging.getLogger(__name__)


async def append_reviews_to_db(
    review_rows: List[Dict[str, Any]],
) -> int:
    """
    Insert reviews into location_reviews table.
    Skips duplicates using reviewId UNIQUE constraint.
    """

    if not review_rows:
        return 0

    loop = asyncio.get_running_loop()

    def _insert():

        sql = """
        INSERT INTO location_reviews
        (
            name,
            reviewId,
            reviewer_displayName,
            reviewer_isAnonymous,
            reviewer_profilePhotoUrl,
            starRating,
            rating,
            comment,
            createTime,
            updateTime,
            fetchedAt,
            reviewReply,
            title,
            sentiment,
            emotion,
            attributes,
            context_sentiment,
            context_confidence,
            final_sentiment,
            quality_score,
            post_error
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """

        inserted = 0

        for row in review_rows:

            try:

                db.execute_update(
                    sql,
                    (
                        row.get("name"),
                        row.get("reviewId"),
                        row.get("reviewer_displayName"),
                        row.get("reviewer_isAnonymous"),
                        row.get("reviewer_profilePhotoUrl"),
                        row.get("starRating"),
                        row.get("rating"),
                        row.get("comment"),
                        row.get("createTime"),
                        row.get("updateTime"),
                        row.get("fetchedAt"),
                        row.get("reviewReply"),
                        row.get("title"),
                        row.get("sentiment"),
                        row.get("emotion"),
                        row.get("attributes"),
                        row.get("context_sentiment"),
                        row.get("context_confidence"),
                        row.get("final_sentiment"),
                        row.get("quality_score"),
                        row.get("post_error"),
                    ),
                )

                inserted += 1

            except Exception as e:

                error_msg = str(e)

                if "Duplicate entry" in error_msg:
                    continue

                logger.error(f"Failed inserting review {row.get('reviewId')}: {e}")

        return inserted

    return await loop.run_in_executor(None, _insert)


async def append_lifetime_reviews(
    review_rows: List[Dict[str, Any]],
) -> int:
    """
    Alias function used for lifetime review ingestion.
    """

    return await append_reviews_to_db(review_rows)


async def batch_upsert_reviews(
    review_rows: List[Dict[str, Any]],
) -> int:
    """
    Faster batch upsert using ON DUPLICATE KEY UPDATE.
    This is recommended for large review imports.
    """

    if not review_rows:
        return 0

    loop = asyncio.get_running_loop()

    def _batch():

        columns = [
            "name",
            "reviewId",
            "reviewer_displayName",
            "reviewer_isAnonymous",
            "reviewer_profilePhotoUrl",
            "starRating",
            "rating",
            "comment",
            "createTime",
            "updateTime",
            "fetchedAt",
            "reviewReply",
            "title",
            "sentiment",
            "emotion",
            "attributes",
            "context_sentiment",
            "context_confidence",
            "final_sentiment",
            "quality_score",
            "post_error",
        ]

        rows = []

        for r in review_rows:

            rows.append(
                (
                    r.get("name"),
                    r.get("reviewId"),
                    r.get("reviewer_displayName"),
                    r.get("reviewer_isAnonymous"),
                    r.get("reviewer_profilePhotoUrl"),
                    r.get("starRating"),
                    r.get("rating"),
                    r.get("comment"),
                    r.get("createTime"),
                    r.get("updateTime"),
                    r.get("fetchedAt"),
                    r.get("reviewReply"),
                    r.get("title"),
                    r.get("sentiment"),
                    r.get("emotion"),
                    r.get("attributes"),
                    r.get("context_sentiment"),
                    r.get("context_confidence"),
                    r.get("final_sentiment"),
                    r.get("quality_score"),
                    r.get("post_error"),
                )
            )

        return db.execute_batch_upsert(
            table="location_reviews",
            columns=columns,
            rows=rows,
            unique_key="reviewId",
        )

    return await loop.run_in_executor(None, _batch)