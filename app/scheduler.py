import asyncio
import json
import logging
import re
from typing import Optional, Any, Dict
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.connections import save_google_token_blocking, get_google_credentials
from app.routers import sync_locations, review_summary
from app.reviews import fetch_reviews
from app.utils_review_gem import process_all_reviews
from app.metrics_router import fetch_metrics
from app.schemas.location import ReviewSummaryRequest, SyncLocationsRequest
from app.timezone_utils import today_ist
import gc


logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

_TOKEN_JOB_ID = "token_refresh"
_FETCH_JOB_ID = "review_fetch"
_REPLY_JOB_ID = "review_reply"
_REVIEW_SUMMARY_JOB_ID = "review_summary"
_METRICS_JOB_ID = "daily_metrics"
_SYNC_LOCATIONS_JOB_ID = "sync_locations"


def sanitize_view_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _schedule_task_wrapper(job_coro) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(job_coro())
    except RuntimeError:
        asyncio.run(job_coro())


# ============================================================================
# JOB 1: TOKEN REFRESH
# ============================================================================

async def _run_refresh_logic(creds: Any) -> None:
    """Execute the token refresh and save logic."""
    if getattr(creds, "refresh_token", None) is None:
        logger.warning("⚠️ No refresh_token found; skipping refresh")
        return

    loop = asyncio.get_running_loop()

    def do_refresh_and_save():
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            creds.refresh(GoogleRequest())
            save_google_token_blocking(creds.token, creds.refresh_token, creds.expiry)
            logger.info("✅ Token refreshed and saved successfully")
        except Exception as exc:
            logger.exception(f"❌ Token refresh/save failed: {exc}")

    await loop.run_in_executor(None, do_refresh_and_save)


async def refresh_token_job() -> None:
    """
    Scheduled job to refresh Google OAuth token.
    Runs every hour by default.
    """
    try:
        logger.info("🔄 Starting token refresh job...")
        
        creds: Optional[Any] = await get_google_credentials()
        if not creds:
            logger.warning("⚠️ get_google_credentials returned None; skipping refresh")
            return
        
        await _run_refresh_logic(creds)
        logger.info("✅ Token refresh job completed successfully")
        
    except Exception as exc:
        logger.exception(f"❌ Token refresh job failed: {exc}")


async def fetch_reviews_job() -> None:
    """
    Scheduled job to fetch reviews from Google My Business.
    
    Configuration (from settings):
    - Runs every 30 minutes (default)
    - Fetches last 30 reviews per location (SCHED_MAX_REVIEWS)
    - Stores only unreplied reviews (SCHED_ONLY_UNREPLIED)
    - Processes locations concurrently (SCHED_FETCH_CONCURRENCY)
    
    Process:
    1. Fetches newest reviews from all locations
    2. Filters for unreplied reviews only
    3. Stores in PlanetScale database table with proper partitioning
    4. Logs detailed summary of operation
    """
    try:
        logger.info("⏳ Starting scheduled review fetch job...")

        # Get configuration with fallback defaults
        max_reviews = getattr(settings, "SCHED_MAX_REVIEWS", 10)
        only_unreplied = getattr(settings, "SCHED_ONLY_UNREPLIED", True)
        concurrency = getattr(settings, "SCHED_FETCH_CONCURRENCY", 20)

        logger.info(
            f"📋 Configuration: max_reviews={max_reviews}, "
            f"only_unreplied={only_unreplied}, "
            f"concurrency={concurrency}"
        )

        # Call the fetch_reviews function
        resp = await fetch_reviews(
            account_id=None,  # Uses settings.GOOGLE_ACCOUNT_ID
            location_ids=None,  # Fetches all locations from PlanetScale database
            concurrency=concurrency,
            max_reviews=max_reviews,
            only_unreplied=only_unreplied,
            dedup_mode="append_dedup"  # Explicitly pass the dedup_mode as string
        )

        # Parse and log response
        if hasattr(resp, "body"):
            try:
                data = json.loads(resp.body.decode())
                status = data.get("status", "unknown")
                locations = data.get("locations_requested", 0)
                fetched = data.get("total_fetched", 0)
                unreplied = data.get("total_unreplied_found", 0)
                written = data.get("total_written", 0)

                logger.info(
                    f"✅ Fetch job completed successfully\n"
                    f"   Status: {status}\n"
                    f"   Locations: {locations}\n"
                    f"   Total Fetched: {fetched}\n"
                    f"   Unreplied Found: {unreplied}\n"
                    f"   Stored in DB: {written}"
                )
            except json.JSONDecodeError:
                logger.info("✅ Fetch job completed (response not JSON)")
        else:
            logger.info("✅ Fetch job completed")

    except Exception as exc:
        logger.exception(f"❌ Scheduled review fetch job failed: {exc}")
# ============================================================================
# JOB 3: REPLY TO REVIEWS
# ============================================================================

async def reply_reviews_jobs() -> None:
    """
    Scheduled job to process and reply to reviews using AI.

    This version calls the partition-aware process_all_reviews endpoint and
    forces date_from/date_to to today's date (Asia/Kolkata).
    
    Configuration (from settings):
    - REPLY_REVIEWS_ENABLED: Enable/disable this job
    - REPLY_REVIEWS_DRY_RUN: Preview mode without posting
    - REPLY_REVIEWS_CONCURRENCY: Parallel processing limit
    - REPLY_REVIEWS_BATCH_SIZE: Reviews per batch
    - enable_quality_retry: Retry on low-quality replies
    - rate_limit_delay: Delay between API calls
    
    Process:
    1. Fetches unreplied reviews from database for today's date
    2. Generates AI replies using configured model
    3. Posts replies to Google My Business (if not dry_run)
    4. Updates database with reply status
    """
    try:
        from zoneinfo import ZoneInfo
        
        # Respect feature flag
        if not getattr(settings, "REPLY_REVIEWS_ENABLED", True):
            logger.info("ℹ️ Reply reviews job disabled in settings; skipping")
            return

        dry_run = getattr(settings, "REPLY_REVIEWS_DRY_RUN", False)
        concurrency = getattr(settings, "REPLY_REVIEWS_CONCURRENCY", 5)
        batch_size = getattr(settings, "REPLY_REVIEWS_BATCH_SIZE", 50)
        enable_quality_retry = getattr(settings, "enable_quality_retry", True)
        rate_limit_delay = getattr(settings, "rate_limit_delay", 0.5)

        logger.info(
            f"⏳ Starting scheduled reply job...\n"
            f"   Dry Run: {dry_run}\n"
            f"   Concurrency: {concurrency}\n"
            f"   Batch Size: {batch_size}\n"
            f"   Quality Retry: {enable_quality_retry}\n"
            f"   Rate Limit Delay: {rate_limit_delay}s"
        )

        # Build payload (use defaults / empty => endpoint defaults apply)
        payload: dict = {}

        # Force start/end to today in Asia/Kolkata
        tz = ZoneInfo("Asia/Kolkata")
        today = datetime.now(tz).date().isoformat()
        date_from = today
        date_to = today

        # Additional endpoint params (tune as needed)
        max_reviews = getattr(settings, "REPLY_REVIEWS_MAX_REVIEWS", 500)
        process_delay = getattr(settings, "REPLY_REVIEWS_PROCESS_DELAY", 0.3)
        skip_gemini = getattr(settings, "REPLY_REVIEWS_SKIP_GEMINI", False)
        skip_gmb_post = getattr(settings, "REPLY_REVIEWS_SKIP_GMB_POST", False)

        logger.info(f"🔍 Scheduling processing for date: {today}")

        # Call the process_all_reviews endpoint function directly
        resp = await process_all_reviews(
            payload=payload,
            dry_run=dry_run,
            location_id=None,
            date_from=date_from,
            date_to=date_to,
            max_reviews=max_reviews,
            batch_size=batch_size,
            process_delay=process_delay,
            skip_gemini=skip_gemini,
            skip_gmb_post=skip_gmb_post,
        )

        # process_all_reviews returns a dict (or raises HTTPException)
        if isinstance(resp, dict):
            msg = resp.get("message", "")
            processed = resp.get("processed") or resp.get("processed_count", 0)
            errors = resp.get("errors") or (len(resp.get("error_log", [])) if resp.get("error_log") else 0)
            error_log = resp.get("error_log") or []

            logger.info(
                f"✅ Reply job completed\n"
                f"   Message: {msg}\n"
                f"   Processed: {processed}\n"
                f"   Errors: {errors}"
            )

            if error_log:
                logger.warning(f"⚠️ {len(error_log)} errors occurred during reply processing")
                for e in (error_log[:5] if isinstance(error_log, list) else [error_log]):
                    logger.error(f"   Error: {e}")
        else:
            # Fallback: log raw response
            logger.info(f"✅ Reply job completed (non-dict response): {resp}")

    except Exception as exc:
        logger.exception(f"❌ Scheduled reply reviews job failed: {exc}")
    finally:
        # Best-effort cleanup
        try:
            gc.collect()
        except Exception as cleanup_exc:
            logger.debug(f"GC cleanup warning: {cleanup_exc}")


async def fetch_daily_metrics_jobs() -> None:
    """
    Scheduled job to fetch Google My Business metrics.
    
    Configuration (from settings):
    - Runs daily at configured interval
    - GET_DATA_CONCURRENCY: Parallel location processing
    - GET_DATA_RATE_LIMIT_PER_MINUTE: API rate limiting
    
    Process:
    1. Calculates date range (yesterday to today)
    2. Fetches metrics for all locations
    3. Persists to PlanetScale database table
    4. Deduplicates records
    """
    try:
        # Calculate date range: yesterday to today
        today = today_ist().date()
        yesterday = today - timedelta(days=1)
        start_date = yesterday.isoformat()
        end_date = today.isoformat()

        logger.info(f"⏳ Starting daily metrics job for {start_date} → {end_date}")

        # Get configuration
        concurrency = getattr(settings, "GET_DATA_CONCURRENCY", 20)
        rate_limit = getattr(settings, "GET_DATA_RATE_LIMIT_PER_MINUTE", 300)

        logger.info(
            f"📋 Configuration: concurrency={concurrency}, "
            f"rate_limit={rate_limit}/min"
        )

        # Call fetch_metrics function with correct parameters
        result = await fetch_metrics(
            start_date=start_date,
            end_date=end_date,
            metrics=None,  # Fetch all available metrics
            location_ids=None,  # Fetch all locations
            concurrency=concurrency,
            rate_limit_per_minute=rate_limit,
            auto_save=True,
            test_mode=False,
            deduplicate_after=True
        )

        # Log summary
        if isinstance(result, dict):
            requested = result.get("locations_requested", 0)
            fetched = result.get("locations_fetched", 0)
            failed = result.get("locations_failed", 0)
            rows_saved = result.get("rows_saved", 0)

            logger.info(
                f"✅ Daily metrics job completed\n"
                f"   Locations Requested: {requested}\n"
                f"   Locations Fetched: {fetched}\n"
                f"   Locations Failed: {failed}\n"
                f"   Rows Saved: {rows_saved}"
            )
        else:
            logger.info(f"✅ Daily metrics job completed: {str(result)[:500]}")

    except Exception as exc:
        logger.exception(f"❌ Scheduled daily metrics job failed: {exc}")


# ============================================================================
# JOB 5: SYNC LOCATIONS
# ============================================================================

async def sync_locations_job() -> None:
    """
    Scheduled job to sync locations from Google My Business.
    
    Configuration (from settings):
    - Runs daily
    - SYNC_LOCATIONS_BATCH_SIZE: Locations per batch
    - SYNC_LOCATIONS_ACCOUNT_ID: Google account ID
    - SYNC_LOCATIONS_STOP_ON_ERROR: Stop on first error
    
    Process:
    1. Fetches all locations from GMB API
    2. Updates PlanetScale database locations table
    3. Syncs metadata (name, address, phone, etc.)
    4. Handles pagination automatically
    """
    try:
        logger.info("⏳ Starting scheduled locations sync job...")

        # Get configuration
        batch_size = getattr(settings, "SYNC_LOCATIONS_BATCH_SIZE", 100)
        account_id = getattr(settings, "SYNC_LOCATIONS_ACCOUNT_ID", None)
        stop_on_error = getattr(settings, "SYNC_LOCATIONS_STOP_ON_ERROR", False)

        logger.info(
            f"📋 Configuration: batch_size={batch_size}, "
            f"account_id={account_id or 'default'}, "
            f"stop_on_error={stop_on_error}"
        )

        request = SyncLocationsRequest(
            batch_size=batch_size,
            account_id=account_id,
            stop_on_error=stop_on_error,
        )

        result = await sync_locations(request)

        logger.info(
            f"✅ Locations sync completed\n"
            f"   Message: {result.message}\n"
            f"   Locations Synced: {result.locations_synced}\n"
            f"   Pages Processed: {result.pages or 0}"
        )

    except Exception as exc:
        logger.exception(f"❌ Scheduled sync locations job failed: {exc}")


# ============================================================================
# JOB 6: REVIEW SUMMARY
# ============================================================================

async def review_summary_job() -> None:
    """
    Scheduled job to generate review summaries.
    
    Configuration (from settings):
    - Runs hourly
    - SUMMARY_REVIEWS_CONCURRENCY: Parallel processing limit
    
    Process:
    1. Aggregates review counts by location
    2. Calculates average ratings
    3. Computes sentiment distribution
    4. Updates summary table
    """
    try:
        logger.info("⏳ Starting scheduled review summary job...")

        # Get configuration
        concurrency = getattr(settings, "SUMMARY_REVIEWS_CONCURRENCY", 40)

        logger.info(f"📋 Configuration: concurrency={concurrency}")

        # Create request object
        request = ReviewSummaryRequest(
            account_id=None,
            page_size=1,
            concurrency=concurrency,
        )

        # Call review_summary function
        result = await review_summary(request)

        # Log summary
        logger.info(
            f"✅ Review summary completed\n"
            f"   Message: {result.message}\n"
            f"   Locations Processed: {result.locations_processed}\n"
            f"   Summaries Upserted: {result.rating_summaries_upserted}"
        )

    except Exception as exc:
        logger.exception(f"❌ Scheduled review summary job failed: {exc}")


# ============================================================================
# SCHEDULER MANAGEMENT
# ============================================================================

def start_scheduler() -> None:
   
    # Get intervals from settings
    token_interval = int(getattr(settings, "TOKEN_REFRESH_INTERVAL_SECONDS", 3600))
    fetch_interval = int(getattr(settings, "FETCH_REVIEWS_INTERVAL_SECONDS", 1800))
    reply_interval = int(getattr(settings, "REPLY_REVIEWS_INTERVAL_SECONDS", 1800))
    summary_interval = int(getattr(settings, "SUMMARY_REVIEWS_INTERVAL_SECONDS", 3600))

   

    # Remove existing jobs if already scheduled (idempotent)
    for job_id in (_TOKEN_JOB_ID, _FETCH_JOB_ID, _REPLY_JOB_ID, 
                   _REVIEW_SUMMARY_JOB_ID, _METRICS_JOB_ID, _SYNC_LOCATIONS_JOB_ID):
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    # Schedule all jobs
    logger.info("📝 Adding jobs to scheduler...")

    # Job 1: Token Refresh
    scheduler.add_job(
        _schedule_task_wrapper,
        trigger=IntervalTrigger(seconds=token_interval),
        id=_TOKEN_JOB_ID,
        args=[refresh_token_job],
        replace_existing=True,
    )
    logger.info(f"   ✅ Added: Token Refresh (every {token_interval}s)")

    # Job 2: Review Fetch
    scheduler.add_job(
        _schedule_task_wrapper,
        trigger=IntervalTrigger(seconds=fetch_interval),
        id=_FETCH_JOB_ID,
        args=[fetch_reviews_job],
        replace_existing=True,
    )
    logger.info(f"   ✅ Added: Review Fetch (every {fetch_interval}s)")

    # Job 3: Review Reply (conditional)
    if getattr(settings, "REPLY_REVIEWS_ENABLED", True):
        scheduler.add_job(
            _schedule_task_wrapper,
            trigger=IntervalTrigger(seconds=reply_interval),
            id=_REPLY_JOB_ID,
            args=[reply_reviews_jobs],
            replace_existing=True,
        )
        logger.info(f"   ✅ Added: Review Reply (every {reply_interval}s)")
    else:
        logger.info(f"   ⏭️ Skipped: Review Reply (disabled in settings)")

    # Job 4: Review Summary
    scheduler.add_job(
        _schedule_task_wrapper,
        trigger=IntervalTrigger(seconds=summary_interval),
        id=_REVIEW_SUMMARY_JOB_ID,
        args=[review_summary_job],
        replace_existing=True,
    )
    logger.info(f"   ✅ Added: Review Summary (every {summary_interval}s)")

    # Job 5: Daily Metrics
    scheduler.add_job(
        _schedule_task_wrapper,
        trigger=IntervalTrigger(seconds=86400),
        id=_METRICS_JOB_ID,
        args=[fetch_daily_metrics_jobs],
        replace_existing=True,
    )
    logger.info(f"   ✅ Added: Daily Metrics (every 86400s)")

    # Job 6: Sync Locations
    scheduler.add_job(
        _schedule_task_wrapper,
        trigger=IntervalTrigger(seconds=86400),
        id=_SYNC_LOCATIONS_JOB_ID,
        args=[sync_locations_job],
        replace_existing=True,
    )
    logger.info(f"   ✅ Added: Sync Locations (every 86400s)")

    # Start the scheduler
    if not scheduler.running:
        scheduler.start()
        logger.info("✅ Scheduler started successfully - all jobs active")
    else:
        logger.info("✅ Scheduler already running - jobs rescheduled")


def stop_scheduler() -> None:
    """
    Gracefully stop the scheduler and all running jobs.
    Should be called during application shutdown.
    """
    try:
        if scheduler.running:
            logger.info("⏳ Stopping scheduler...")
            scheduler.shutdown(wait=False)
            logger.info("✅ Scheduler stopped successfully")
        else:
            logger.info("ℹ️ Scheduler was not running")
    except Exception as exc:
        logger.exception(f"❌ Error stopping scheduler: {exc}")


# ============================================================================
# MANUAL JOB TRIGGERS (for testing/debugging)
# ============================================================================

async def run_all_jobs_once() -> None:
    """
    Run all scheduled jobs once manually.
    Useful for testing or manual triggers.
    """
    logger.info("🔧 Running all jobs manually...")

    try:
        await refresh_token_job()
        await fetch_reviews_job()
        await reply_reviews_jobs()
        await review_summary_job()
        await fetch_daily_metrics_jobs()
        await sync_locations_job()
        
        logger.info("✅ All jobs completed successfully")
    except Exception as exc:
        logger.exception(f"❌ Manual job execution failed: {exc}")


def get_scheduler_status() -> Dict[str, Any]:
    """
    Get current scheduler status and job information.
    
    Returns:
        dict: Scheduler status including running jobs and next run times
    """
    if not scheduler.running:
        return {
            "running": False,
            "message": "Scheduler is not running"
        }

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })

    return {
        "running": True,
        "jobs": jobs,
        "total_jobs": len(jobs)
    }