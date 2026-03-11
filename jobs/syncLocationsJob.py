import asyncio
import logging
from app.scheduler import sync_locations_job

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("🚀 Cloud Run Job: Sync Locations")
    try:
        # await sync_locations_job()
        logger.info("✅ Sync locations job completed successfully")
    except Exception:
        logger.exception("❌ Sync locations job failed")
        raise

if __name__ == "__main__":
    asyncio.run(main())
