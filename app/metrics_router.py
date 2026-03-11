import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

from app.auth import logger
from app.services.metrics_service import (
    fetch_metrics_for_locations,
    _normalize_location_id,
    _to_int_safe,
    _expand_friendly_metrics,
    _DEFAULT_FRIENDLY,
)

router = APIRouter(prefix="/dashboard/metrics", tags=["metrics"])


@router.post("/fetch")
async def fetch_metrics(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    metrics: Optional[List[str]] = Query(None, description="Friendly metrics (default: all)"),
    location_ids: Optional[List[str]] = Query(None, description="Specific location IDs to fetch"),
    concurrency: int = Query(50, ge=1, le=200, description="Concurrent requests"),
    rate_limit_per_minute: int = Query(600, ge=60, le=6000, description="API rate limit"),
    auto_save: bool = Query(True, description="Automatically save to PlanetScale"),
    test_mode: bool = Query(False, description="Process only first location"),
    deduplicate_after: bool = Query(True, description="Deduplicate after all writes"),
    background_tasks: BackgroundTasks = None
):
    try:
        result = await fetch_metrics_for_locations(
            start_date,
            end_date,
            metrics,
            location_ids,
            concurrency,
            rate_limit_per_minute,
            auto_save,
            test_mode,
            deduplicate_after,
            background_tasks
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid parameter value")
    except Exception as exc:
        logger.exception("Failed to fetch metrics: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch metrics")


@router.get("/query")
async def query_metrics(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    location_ids: Optional[List[str]] = Query(None),
    store_codes: Optional[List[str]] = Query(None),
    store_names: Optional[List[str]] = Query(None),
    metrics: Optional[List[str]] = Query(None),
    aggregate_by: str = Query("location", description="location, date, region, or store"),
    include_time_series: bool = Query(True),
):
    try:
        df_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        df_to = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")

    if df_to < df_from:
        raise HTTPException(status_code=400, detail="date_to must be >= date_from")

    from app.connections import db

    loc_metadata = {}
    region_data = {}
    filtered_location_ids = None

    try:
        query = "SELECT name, storeCode FROM locations"
        results = db.execute_query(query)
        
        for row in results:
            loc_name = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
            store_code = row.get("storeCode") if isinstance(row, dict) else getattr(row, "storeCode", None)
            
            if loc_name:
                norm_id = _normalize_location_id(loc_name)
                metadata = {
                    "store_code": str(store_code).strip() if store_code else None,
                    "store_name": loc_name,
                }
                loc_metadata[norm_id] = metadata

        if store_codes or store_names:
            filtered_location_ids = list(loc_metadata.keys())
            if not filtered_location_ids:
                return {
                    "count": 0,
                    "results": [],
                    "summary": {},
                    "summary_by_date": {}
                }

    except Exception as e:
        logger.exception(f"Failed to load location metadata: {e}")
        loc_metadata = {}

    try:
        region_query = "SELECT storeCode, title, name, region FROM region"
        region_results = db.execute_query(region_query)
        
        for row in region_results:
            store_code = row.get("storeCode") if isinstance(row, dict) else getattr(row, "storeCode", None)
            if store_code:
                region_data[store_code] = {
                    "title": row.get("title") if isinstance(row, dict) else getattr(row, "title", None),
                    "region_name": row.get("name") if isinstance(row, dict) else getattr(row, "name", None),
                    "region": row.get("region") if isinstance(row, dict) else getattr(row, "region", None),
                }
    except Exception as e:
        logger.exception(f"Failed to load region data: {e}")
        region_data = {}

    try:
        metrics_list = metrics or _DEFAULT_FRIENDLY
        
        query = "SELECT batch_id, results FROM locations_metrics_batches WHERE start_date <= %s AND end_date >= %s ORDER BY createdAt DESC"
        params = [df_to.isoformat(), df_from.isoformat()]
        
        batch_results = db.execute_query(query, tuple(params))
        
        rows = []
        for batch_row in batch_results:
            if isinstance(batch_row, dict):
                results_json = batch_row.get("results")
            else:
                results_json = batch_row[1] if len(batch_row) > 1 else None
            
            if results_json:
                try:
                    results_data = json.loads(results_json) if isinstance(results_json, str) else results_json
                    if isinstance(results_data, list):
                        rows.extend(results_data)
                except (json.JSONDecodeError, TypeError):
                    pass

    except Exception as e:
        logger.exception(f"Failed to query metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")

    concrete_metrics, metric_mapping = _expand_friendly_metrics(metrics_list)
    friendly_order = metrics_list

    location_data: Dict[str, Dict[str, Any]] = {}
    store_data: Dict[str, Dict[str, int]] = {}

    global_summary = {fk: 0 for fk in friendly_order}

    for row in rows:
        if not isinstance(row, dict):
            continue
        
        loc_id = row.get("location_id")
        metric_name = row.get("metric")
        metric_value = _to_int_safe(row.get("value", 0))
        date_str = row.get("date")
        
        if not loc_id or not metric_name:
            continue
        
        meta = loc_metadata.get(loc_id, {})
        store_code = meta.get("store_code")
        store_name = meta.get("store_name")
        
        if aggregate_by == "location":
            if loc_id not in location_data:
                region_info = region_data.get(store_code, {}) if store_code else {}
                store_title = region_info.get("title")
                if store_title and "." in store_title:
                    store_title = store_title.split(".", 1)[0]
                location_data[loc_id] = {
                    "location_id": loc_id,
                    "store_code": store_code,
                    "store_name": store_name,
                    "store_title": store_title,
                    "region_name": region_info.get("region_name"),
                    "region": region_info.get("region"),
                    "metrics": {fk: 0 for fk in friendly_order},
                }
            
            if metric_name in location_data[loc_id]["metrics"]:
                location_data[loc_id]["metrics"][metric_name] += metric_value
                global_summary[metric_name] = global_summary.get(metric_name, 0) + metric_value
        
        elif aggregate_by == "store":
            if not store_code:
                continue
            if store_code not in store_data:
                region_info = region_data.get(store_code, {})
                store_data[store_code] = {
                    "name": store_name,
                    "store_title": region_info.get("title"),
                    "region_name": region_info.get("region_name"),
                    "region": region_info.get("region"),
                    "metrics": {fk: 0 for fk in friendly_order}
                }
            
            if metric_name in store_data[store_code]["metrics"]:
                store_data[store_code]["metrics"][metric_name] += metric_value
                global_summary[metric_name] = global_summary.get(metric_name, 0) + metric_value

    if aggregate_by == "location":
        results = list(location_data.values())
    elif aggregate_by == "date":
        results = []
    elif aggregate_by == "store":
        results = []
        for s in sorted(store_data.keys()):
            store_title = store_data[s].get("store_title")
            if store_title and "." in store_title:
                store_title = store_title.split(".", 1)[0]
            results.append({
                "store_code": s,
                "store_name": store_data[s]["name"],
                "store_title": store_title,
                "region_name": store_data[s].get("region_name"),
                "region": store_data[s].get("region"),
                "metrics": store_data[s]["metrics"]
            })
    else:
        results = []

    summary_by_date = {}

    return {
        "metrics": metrics_list,
        "count": len(results),
        "results": results,
        "summary": global_summary,
        "summary_by_date": summary_by_date,
    }


# @router.post("/optimize")
async def optimize_table(background_tasks: BackgroundTasks):
    def run_optimization():
        try:
            from app.connections import db
            db.execute_update("OPTIMIZE TABLE review_analytics")
            db.execute_update("OPTIMIZE TABLE location_reviews")
            logger.info("✅ Table optimization completed")
        except Exception as e:
            logger.exception(f"Optimization failed: {e}")

    background_tasks.add_task(run_optimization)

    return {
        "message": "Table optimization started in background",
        "tables": ["review_analytics", "location_reviews"]
    }


# @router.post("/deduplicate")
async def deduplicate_table(background_tasks: BackgroundTasks):
    def run_deduplication():
        try:
            from app.connections import db
            sql = """
            DELETE from location_reviews 
            WHERE id NOT IN (
                SELECT MAX(id) FROM location_reviews 
                GROUP BY reviewId
            )
            """
            db.execute_update(sql)
            logger.info("✅ Deduplication completed")
        except Exception as e:
            logger.exception(f"Deduplication failed: {e}")

    background_tasks.add_task(run_deduplication)

    return {
        "message": "Deduplication started in background",
        "table": "location_reviews"
    }


# @router.get("/stats")
async def get_table_stats():
    try:
        from app.connections import db
        
        row_count_result = db.execute_query("SELECT COUNT(*) FROM review_analytics")
        row_count = row_count_result[0][0] if row_count_result else 0

        location_count_result = db.execute_query("SELECT COUNT(DISTINCT location_name) FROM review_analytics")
        location_count = location_count_result[0][0] if location_count_result else 0

        date_range_result = db.execute_query("SELECT MIN(date) as min_date, MAX(date) as max_date FROM review_analytics")
        min_date = None
        max_date = None
        if date_range_result:
            min_date = date_range_result[0][0]
            max_date = date_range_result[0][1]

        review_count_result = db.execute_query("SELECT COUNT(*) FROM location_reviews")
        review_count = review_count_result[0][0] if review_count_result else 0

        return {
            "table": "review_analytics",
            "row_count": row_count,
            "unique_locations": location_count,
            "min_date": str(min_date) if min_date else None,
            "max_date": str(max_date) if max_date else None,
            "total_reviews": review_count,
            "status": "healthy" if row_count > 0 else "empty"
        }

    except Exception as exc:
        logger.exception("Failed to get table stats: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to get table stats")


# @router.get("/diagnostics")
async def metrics_diagnostics():
    """Diagnose metrics fetching issues"""
    try:
        from app.connections import db
        from app.config import settings
        from app.services.auth_service import get_google_credentials
        
        diagnostics = {
            "config": {
                "account_id": settings.GOOGLE_ACCOUNT_ID,
                "account_id_configured": bool(settings.GOOGLE_ACCOUNT_ID),
            },
            "database": {},
            "google_auth": {},
            "recent_batches": []
        }
        
        db_locations = db.execute_query("SELECT COUNT(*) as cnt FROM locations")
        diagnostics["database"]["total_locations"] = db_locations[0]["cnt"] if db_locations else 0
        
        db_reviews = db.execute_query("SELECT COUNT(*) as cnt FROM location_reviews")
        diagnostics["database"]["total_reviews"] = db_reviews[0]["cnt"] if db_reviews else 0
        
        db_batches = db.execute_query("SELECT COUNT(*) as cnt FROM locations_metrics_batches")
        diagnostics["database"]["total_metric_batches"] = db_batches[0]["cnt"] if db_batches else 0
        
        try:
            creds = await get_google_credentials()
            diagnostics["google_auth"]["authenticated"] = True
            diagnostics["google_auth"]["has_token"] = bool(getattr(creds, "token", None))
        except Exception as e:
            diagnostics["google_auth"]["authenticated"] = False
            diagnostics["google_auth"]["error"] = str(e)
        
        recent_batches_result = db.execute_query(
            "SELECT batch_id, createdAt, location_count_requested, location_count_returned, errors_count FROM locations_metrics_batches ORDER BY createdAt DESC LIMIT 5"
        )
        if recent_batches_result:
            diagnostics["recent_batches"] = [
                {
                    "batch_id": r["batch_id"],
                    "created_at": str(r["createdAt"]),
                    "locations_requested": r["location_count_requested"],
                    "locations_fetched": r["location_count_returned"],
                    "errors": r["errors_count"]
                }
                for r in recent_batches_result
            ]
        
        return diagnostics
    
    except Exception as exc:
        logger.exception("Diagnostics failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
