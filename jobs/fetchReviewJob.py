"""
Docstring for jobs.fetchReviewJob
"""

import asyncio
import logging
from app.scheduler import fetch_reviews_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """
    Main function to run the fetch reviews job:
    """
    logger.info("🚀 Cloud Run Job: Fetch Reviews")
    try:
        await fetch_reviews_job()
        logger.info("✅ Fetch reviews job completed successfully")
    except Exception:
        logger.exception("❌ Fetch reviews job failed")
        raise

if __name__ == "__main__":
    asyncio.run(main())