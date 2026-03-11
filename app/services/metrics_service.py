from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import BackgroundTasks
from pydantic import BaseModel

from app.config import settings
from app.services.auth_service import get_google_credentials
from app.timezone_utils import now_utc

logger = logging.getLogger(__name__)

_METRIC_EXPANSIONS = {
    "BUSINESS_IMPRESSIONS": [
        "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
        "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
        "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
        "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    ],
    "CALL_CLICKS": ["CALL_CLICKS"],
    "WEBSITE_CLICKS": ["WEBSITE_CLICKS"],
    "BUSINESS_BOOKINGS": ["BUSINESS_BOOKINGS"],
    "BUSINESS_DIRECTION_REQUESTS": ["BUSINESS_DIRECTION_REQUESTS"],
    "BUSINESS_FOOD_ORDERS": ["BUSINESS_FOOD_ORDERS"],
    "BUSINESS_CONVERSATIONS": ["BUSINESS_CONVERSATIONS"],
}

_DEFAULT_FRIENDLY = list(_METRIC_EXPANSIONS.keys())


class MetricValue(BaseModel):
    date: str
    location_id: str
    metric: str
    value: int


class GBPMetricsClient:
    def __init__(self, access_token: str, rate_limiter: AdaptiveRateLimiter):
        self.access_token = access_token
        self.rate_limiter = rate_limiter
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    async def fetch_location_metrics(
        self,
        location_id: str,
        start_date: datetime,
        end_date: datetime,
        metrics: List[str]
    ) -> Dict[str, Any]:
        await self.rate_limiter.wait()

        norm_id = _normalize_location_id(location_id)
        url = f"https://businessprofileperformance.googleapis.com/v1/locations/{norm_id}:fetchMultiDailyMetricsTimeSeries"

        params = []
        for m in metrics:
            params.append(("dailyMetrics", m))

        params.extend([
            ("dailyRange.start_date.year", str(start_date.year)),
            ("dailyRange.start_date.month", str(start_date.month)),
            ("dailyRange.start_date.day", str(start_date.day)),
            ("dailyRange.end_date.year", str(end_date.year)),
            ("dailyRange.end_date.month", str(end_date.month)),
            ("dailyRange.end_date.day", str(end_date.day)),
        ])

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json"
        }

        try:
            resp = await self.client.get(url, headers=headers, params=params)

            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}


class AdaptiveRateLimiter:
    def __init__(self, rate_limit_per_minute: int = 600, burst_size: int = 10):
        self.rate_limit_per_minute = rate_limit_per_minute
        self.burst_size = burst_size
        self.requests = []
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            one_minute_ago = now - 60.0

            self.requests = [req_time for req_time in self.requests if req_time > one_minute_ago]

            if len(self.requests) >= self.rate_limit_per_minute:
                wait_time = 60.0 - (now - self.requests[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            self.requests.append(asyncio.get_event_loop().time())


def _to_int_safe(v: Any) -> int:
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def _expand_friendly_metrics(
    friendly_list: List[str],
) -> Tuple[List[str], Dict[str, List[str]]]:
    concrete_metrics = []
    metric_mapping = {}

    for friendly in friendly_list:
        expanded = _METRIC_EXPANSIONS.get(friendly, [friendly])
        concrete_metrics.extend(expanded)
        for concrete in expanded:
            if concrete not in metric_mapping:
                metric_mapping[concrete] = []
            metric_mapping[concrete].append(friendly)

    return concrete_metrics, metric_mapping


def _normalize_location_id(location_id: str) -> str:
    if not location_id:
        return ""
    location_id = str(location_id).strip()
    return location_id.rsplit("/", 1)[-1] if "/" in location_id else location_id


def parse_gbp_response(
    response_data: Dict[str, Any],
    location_id: str,
    metric_mapping: Dict[str, List[str]],
) -> List[MetricValue]:
    metrics_list = []

    time_series_data = (
        response_data.get("multiDailyMetricTimeSeries")
        or response_data.get("timeSeries")
        or []
    )

    if isinstance(time_series_data, dict):
        time_series_data = [time_series_data]

    for series_group in time_series_data:
        daily_series = series_group.get("dailyMetricTimeSeries") or []

        for metric_series in daily_series:
            metric_name = metric_series.get("dailyMetric")
            if not metric_name:
                continue

            time_series = metric_series.get("timeSeries") or {}
            dated_values = time_series.get("datedValues") or []

            for dated_value in dated_values:
                date_dict = dated_value.get("date")
                if not date_dict:
                    continue

                try:
                    date_str = f"{date_dict['year']:04d}-{date_dict['month']:02d}-{date_dict['day']:02d}"
                except (KeyError, TypeError):
                    continue

                value = _to_int_safe(dated_value.get("value", 0))

                metrics_list.append(
                    MetricValue(
                        date=date_str,
                        location_id=location_id,
                        metric=metric_name,
                        value=value,
                    )
                )

    return metrics_list


def aggregate_metrics_by_friendly(
    metrics: List[MetricValue],
    metric_mapping: Dict[str, List[str]],
    friendly_metrics: List[str],
) -> Dict[str, Any]:
    by_date: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals: Dict[str, int] = defaultdict(int)

    for m in metrics:
        friendly_names = metric_mapping.get(m.metric, [])
        for fname in friendly_names:
            by_date[m.date][fname] += m.value
            totals[fname] += m.value

    dates_sorted = sorted(by_date.keys())
    time_series = {"dates": dates_sorted}

    for fname in friendly_metrics:
        time_series[fname] = [by_date[d].get(fname, 0) for d in dates_sorted]

    return {"totals": dict(totals), "time_series": time_series}


async def fetch_metrics_for_locations(
    start_date: str,
    end_date: str,
    metrics: Optional[List[str]],
    location_ids: Optional[List[str]],
    concurrency: int,
    rate_limit_per_minute: int,
    auto_save: bool,
    test_mode: bool,
    deduplicate_after: bool,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Dict[str, Any]:
    logger.info(f"📊 Starting metrics fetch: {start_date} to {end_date}, account_id={settings.GOOGLE_ACCOUNT_ID}")
    
    try:
        sdt = datetime.strptime(start_date, "%Y-%m-%d")
        edt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"Invalid date format: {e}")

    if edt < sdt:
        raise ValueError("end_date must be >= start_date")

    try:
        from app.connections import db
        
        results = db.execute_query("SELECT name FROM locations")
        all_location_ids = []
        for row in results:
            name = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
            if name:
                norm_id = _normalize_location_id(name)
                if norm_id:
                    all_location_ids.append(norm_id)
                    logger.debug(f"📍 Normalized location: {name} -> {norm_id}")

        logger.info(f"📍 Loaded {len(all_location_ids)} locations")

    except Exception as e:
        logger.exception(f"Failed to load locations: {e}")
        raise

    if not all_location_ids:
        return {"message": "No locations found", "count": 0, "results": []}

    if location_ids:
        filter_normalized = {_normalize_location_id(lid) for lid in location_ids}
        all_location_ids = [lid for lid in all_location_ids if lid in filter_normalized]

    if test_mode:
        all_location_ids = all_location_ids[:1]

    try:
        creds = await get_google_credentials()
        token = getattr(creds, "token", None)
        if not token:
            raise ValueError("Missing access token")
    except Exception as e:
        logger.exception(f"Failed to get credentials: {e}")
        raise

    metrics_list = metrics or _DEFAULT_FRIENDLY
    concrete_metrics, metric_mapping = _expand_friendly_metrics(metrics_list)

    rate_limiter = AdaptiveRateLimiter(rate_limit_per_minute)
    semaphore = asyncio.Semaphore(concurrency)

    all_results = []
    all_metrics_values = []

    async def fetch_one(loc_id: str) -> Dict[str, Any]:
        async with semaphore:
            try:
                async with GBPMetricsClient(token, rate_limiter) as client:
                    result = await client.fetch_location_metrics(
                        loc_id, sdt, edt, concrete_metrics
                    )

                    if result["success"]:
                        metrics_parsed = parse_gbp_response(
                            result["data"], loc_id, metric_mapping
                        )
                        all_metrics_values.extend(metrics_parsed)

                        aggregated = aggregate_metrics_by_friendly(
                            metrics_parsed, metric_mapping, metrics_list
                        )

                        logger.debug(f"✅ Fetched metrics for location {loc_id}: {len(metrics_parsed)} values")

                        return {
                            "location_id": loc_id,
                            "success": True,
                            "date_range": {"start": start_date, "end": end_date},
                            "metrics": aggregated,
                        }
                    else:
                        error_msg = result.get("error", "Unknown error")
                        logger.warning(f"❌ Failed to fetch metrics for {loc_id}: {error_msg}")
                        return {
                            "location_id": loc_id,
                            "success": False,
                            "error": error_msg,
                        }
            except Exception as e:
                logger.exception(f"❌ Exception fetching metrics for {loc_id}: {e}")
                return {
                    "location_id": loc_id,
                    "success": False,
                    "error": str(e),
                }

    tasks = [fetch_one(lid) for lid in all_location_ids]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = [r for r in all_results if isinstance(r, dict) and r.get("success")]
    failures = [
        {"location_id": r.get("location_id"), "error": r.get("error")}
        for r in all_results
        if isinstance(r, dict) and not r.get("success")
    ]

    logger.info(f"📊 Metrics fetch summary: {len(successes)} success, {len(failures)} failed")

    rows_written = 0
    if auto_save and all_metrics_values:
        try:
            from app.connections import db
            import json
            
            batch_id = f"metrics_{start_date}_{end_date}_{int(datetime.now().timestamp())}"
            
            flattened_results = []
            for m in all_metrics_values:
                flattened_results.append({
                    "date": m.date,
                    "location_id": m.location_id,
                    "metric": m.metric,
                    "value": m.value
                })
            
            results_json = json.dumps(flattened_results)
            errors_json = json.dumps(failures) if failures else None
            
            insert_sql = """
            INSERT INTO locations_metrics_batches 
            (batch_id, createdAt, account_id, start_date, end_date, location_count_requested, location_count_returned, 
             errors_count, metrics_requested, results)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            db.execute_update(insert_sql, (
                batch_id,
                datetime.now(),
                settings.GOOGLE_ACCOUNT_ID,
                start_date,
                end_date,
                len(all_location_ids),
                len(successes),
                len(failures),
                json.dumps(metrics_list),
                results_json
            ))
            
            rows_written = len(all_metrics_values)
            logger.info(f"✅ Saved {rows_written} individual metric rows to locations_metrics_batches")

        except Exception as e:
            logger.exception(f"Failed to save metrics: {e}")

    return {
        "timestamp": now_utc().isoformat() + "Z",
        "date_range": {"start": start_date, "end": end_date},
        "metrics_requested": metrics_list,
        "locations_requested": len(all_location_ids),
        "locations_fetched": len(successes),
        "locations_failed": len(failures),
        "rows_saved": rows_written,
        "results": successes,
        "errors": failures if failures else [],
    }
