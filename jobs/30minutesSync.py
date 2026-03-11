"""
30-Minute Sync Job
This Cloud Run job runs every 30 minutes to:
1. Refresh the access token for the Google Play Developer API.
2. Reply to new reviews (currently commented out for testing).  
"""

import asyncio
import logging
from app.scheduler import refresh_token_job, reply_reviews_jobs, review_summary_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """
    Main function to run the 30-minute sync jobs:
    """
    logger.info("🚀 Cloud Run Job: 30-Minute Sync (Token + Reply + Summary)")
    try:
        await refresh_token_job()
        # await reply_reviews_jobs()
        await review_summary_job()
        logger.info("✅ 30-minute sync jobs completed successfully")
    except Exception:
        logger.exception("❌ 30-minute sync job failed")
        raise


if __name__ == "__main__":
    asyncio.run(main())
