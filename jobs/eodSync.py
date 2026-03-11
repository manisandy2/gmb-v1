import asyncio
import logging
from app.scheduler import (
    fetch_daily_metrics_jobs,
    sync_locations_job
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("🚀 Cloud Run Job: End-of-Day Sync (Metrics + Locations)")
    try:
        # await sync_locations_job()
        await fetch_daily_metrics_jobs()
        logger.info("✅ End-of-day sync jobs completed successfully")
    except Exception:
        logger.exception("❌ End-of-day sync job failed")
        raise

if __name__ == "__main__":
    asyncio.run(main())