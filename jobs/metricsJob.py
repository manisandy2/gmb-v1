import asyncio
import logging
from app.scheduler import fetch_daily_metrics_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("🚀 Cloud Run Job: Fetch Daily Metrics")
    try:
        await fetch_daily_metrics_jobs()
        logger.info("✅ Fetch metrics job completed successfully")
    except Exception:
        logger.exception("❌ Fetch metrics job failed")
        raise

if __name__ == "__main__":
    asyncio.run(main())
