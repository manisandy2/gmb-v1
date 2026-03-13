import logging
import time
import csv
import io
import json
from typing import Any, Dict, List, Optional
from datetime import datetime as _dt, date
from decimal import Decimal

import httpx
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse

# from app.connections import (
#     write_locations_to_db,
#     read_location_from_db,
#     delete_location_from_db,
#     db,
# )
from app.db.locations_repo import write_locations_to_db, read_location_from_db, delete_location_from_db

from app.config import settings
from app.schemas.location import (
    FetchLocationsResponse,
    FetchLocationsRequest,
    LocationWithRatings,
    LocationNormalized,
    ReviewSummaryRequest,
    ReviewSummaryResponse,
    SyncLocationsRequest,
    SyncLocationsResponse,
    VerifyAccountResponse,
    AccountInfo,
    
)
from app.services.auth_service import get_google_credentials
from app.services.location_service import (
    fetch_review_summary,
    sync_locations_from_google,
    normalize_location,
)
from app.timezone_utils import now_utc

logger = logging.getLogger(__name__)

router = APIRouter()


def normalize_location_name(location_input: str) -> str:
    """
    Normalize location input to format: locations/{location_id}
    
    Accepts formats:
    - Full: locations/456 (returned as-is)
    - ID only: 456 (prepends locations/)
    """
    if not location_input:
        raise ValueError("Location input cannot be empty")
    
    if location_input.startswith("locations/"):
        return location_input
    
    return f"locations/{location_input}"


FIELD_MAP = {
    "name": "Location ID",
    "storeCode": "Store Code",
    "title": "Title",
    "locality": "Locality",
    "administrativeArea": "Administrative Area",
    "regionCode": "Region Code",
    "postalCode": "Postal Code",
    "primaryPhone": "Primary Phone",
    "placeId": "Place ID",
    "status": "Status",
    "labels": "Labels",
    "review_count": "Review Count",
    "rating_average": "Average Rating",
    "regionName": "Region",
}


def _safe_iso_date(date_str: Optional[str]) -> Optional[date]:
    """Safely parse ISO date string (YYYY-MM-DD)."""
    if not date_str:
        return None
    try:
        return _dt.fromisoformat(date_str).date()
    except (ValueError, TypeError):
        return None


def _stream_csv_from_rows(rows: List[Dict[str, Any]], fieldnames: List[str]) -> Any:
    """Generator for streaming CSV rows."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    yield output.getvalue()
    output.truncate(0)
    output.seek(0)
    
    for row in rows:
        writer.writerow(row)
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize database row for JSON (convert Decimal to float, datetime to ISO)."""
    result = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            result[k] = float(v)
        elif isinstance(v, _dt):
            result[k] = v.isoformat()
        elif isinstance(v, date):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result


@router.post("/sync-locations", response_model=SyncLocationsResponse)
async def sync_locations(request: SyncLocationsRequest = SyncLocationsRequest()) -> SyncLocationsResponse:
    try:
        return await sync_locations_from_google(
            batch_size=request.batch_size,
            account_id=request.account_id,
            stop_on_error=request.stop_on_error,
        )
    except Exception as e:
        logger.exception("Sync locations failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync/review_summary", response_model=ReviewSummaryResponse)
async def review_summary(request: ReviewSummaryRequest = ReviewSummaryRequest()) -> ReviewSummaryResponse:
    try:
        return await fetch_review_summary(
            account_id=request.account_id,
            page_size=request.page_size,
            concurrency=request.concurrency,
        )
    except Exception as e:
        logger.exception("Review summary fetch failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch review summary")


@router.get("/fetch-locations", response_model=List[Dict[str, Any]])
async def list_locations_with_reviews(
    name: Optional[str] = Query(None),
    placeId: Optional[str] = Query(None),
    storeCode: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    start_time = time.perf_counter()

    try:
        where_clauses = []
        params = []
        
        if name:
            where_clauses.append("l.name = %s")
            params.append(name)
        if placeId:
            where_clauses.append("l.placeId = %s")
            params.append(placeId)
        if storeCode:
            where_clauses.append("l.storeCode = %s")
            params.append(storeCode)
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        sql = f"""
        SELECT 
            l.name,
            l.storeCode,
            l.title,
            l.locality,
            l.administrativeArea,
            l.regionCode,
            l.postalCode,
            l.primaryPhone,
            l.placeId,
            l.status,
            l.labels,
            l.fetchedAt,
            lr.review_count,
            lr.rating_average,
            r.region as regionName
        FROM locations l
        LEFT JOIN location_ratings lr ON l.name = lr.name
        LEFT JOIN region r ON l.name = r.name
        WHERE {where_clause}
        LIMIT %s
        """
        
        params.append(limit)
        results = db.execute_query(sql, tuple(params))
        
        mapped = []
        for r in results:
            out: Dict[str, Any] = {}
            
            field_mapping = {
                "name": "Location ID",
                "storeCode": "Store Code",
                "title": "Title",
                "locality": "Locality",
                "administrativeArea": "Administrative Area",
                "regionCode": "Region Code",
                "postalCode": "Postal Code",
                "primaryPhone": "Primary Phone",
                "placeId": "Place ID",
                "status": "Status",
                "labels": "Labels",
                "review_count": "Review Count",
                "rating_average": "Average Rating",
                "regionName": "Region",
            }
            
            for src_field, friendly in field_mapping.items():
                val = r.get(src_field)
                
                if src_field == "labels":
                    if val is None:
                        val = ""
                    elif isinstance(val, str):
                        try:
                            import json
                            label_list = json.loads(val)
                            if isinstance(label_list, list):
                                val = "|".join(map(str, label_list))
                        except Exception:
                            pass
                elif src_field == "title":
                    # if val and isinstance(val, str) and "." in val:
                    #     val = val.split(".")[0] + "."
                    if isinstance(val, str):
                        idx = val.find(". Buy")
                        if idx != -1:
                            val = val[:idx + 1]
                if isinstance(val, _dt):
                    val = val.isoformat()
                try:
                    if pd.isna(val):
                        val = ""
                except Exception:
                    pass
                out[friendly] = val or ""
            
            fetched = r.get("fetchedAt", "")
            if isinstance(fetched, _dt):
                fetched = fetched.isoformat()
            out["Fetched At"] = fetched or ""
            
            mapped.append(out)
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info("Fetched %d records in %.2f ms", len(mapped), elapsed_ms)
        
        return mapped
    
    except Exception as exc:
        logger.exception("Failed to read locations: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to read locations table")


@router.get("/v1/verify-account")
async def verify_account_id() -> Dict[str, Any]:
    try:
        credentials = await get_google_credentials()
    except Exception as exc:
        logger.exception("Failed to load Google credentials: %s", exc)
        raise HTTPException(
            status_code=401, detail="Google credentials unavailable. Authenticate at /auth/google"
        )

    headers = {"Authorization": f"Bearer {credentials.token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://mybusinessbusinessinformation.googleapis.com/v1/accounts", headers=headers
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
    except httpx.HTTPStatusError as e:
        body = None
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text
        logger.error("Google API returned error fetching accounts: %s %s", e.response.status_code, body)
        raise HTTPException(status_code=e.response.status_code, detail={"error": body})
    except httpx.RequestError as e:
        logger.exception("Network error while contacting Google Business API: %s", e)
        raise HTTPException(status_code=503, detail="Network error contacting Google Business API")
    except Exception as e:
        logger.exception("Unexpected error while verifying account id: %s", e)
        raise HTTPException(status_code=500, detail="Unexpected server error")

    accounts_raw = data.get("accounts", []) or []
    accounts = []
    for acc in accounts_raw:
        try:
            accounts.append(
                AccountInfo(
                    account_id=str(acc.get("name", "")).split("/")[-1],
                    full_resource_name=acc.get("name", ""),
                    account_name=acc.get("accountName", "UNKNOWN"),
                )
            )
        except Exception:
            logger.debug("Skipping malformed account entry: %s", acc)
            continue

    if not accounts:
        raise HTTPException(status_code=404, detail="No Google Business accounts found.")

    return {"accounts": [acc.dict() for acc in accounts]}


class LocationCreateRequest(LocationNormalized):
    pass


class LocationUpdateRequest(LocationNormalized):
    pass


class LocationResponse(LocationNormalized):
    class Config:
        from_attributes = True


@router.post("/locations", response_model=Dict[str, Any], status_code=201)
async def create_location(request: LocationCreateRequest) -> Dict[str, Any]:
    """
    Create a new location.
    
    - **name**: Location identifier (required)
    - **title**: Location title
    - **storeCode**: Store code
    - Other location fields...
    """
    try:
        if not request.name or not request.name.strip():
            raise HTTPException(status_code=400, detail="Location name is required")
        
        location_dict = request.dict()
        location_dict["fetchedAt"] = now_utc() if not location_dict.get("fetchedAt") else location_dict["fetchedAt"]
        
        rows_written = await write_locations_to_db([location_dict])
        
        return {
            "message": "Location created successfully",
            "location": location_dict,
            "rows_written": rows_written,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create location: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create location: {str(e)}")


@router.get("/locations/{location_name}", response_model=Dict[str, Any])
async def get_location(location_name: str) -> Dict[str, Any]:
    """
    Retrieve a single location by name (location ID).
    
    Accepts multiple formats:
    - Full: `accounts/123/locations/456`
    - Short: `locations/456`
    - ID only: `456` (auto-prepends from config)
    """
    try:
        if not location_name or not location_name.strip():
            raise HTTPException(status_code=400, detail="Location name is required")
        
        full_location_name = normalize_location_name(location_name)
        location = await read_location_from_db(full_location_name)
        
        if location is None:
            raise HTTPException(status_code=404, detail=f"Location '{location_name}' not found")
        
        location_dict = dict(location) if not isinstance(location, dict) else location
        
        for key, value in location_dict.items():
            if isinstance(value, _dt):
                location_dict[key] = value.isoformat()
        
        return {
            "message": "Location retrieved successfully",
            "location": location_dict,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to retrieve location: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve location: {str(e)}")


@router.put("/locations/{location_name}", response_model=Dict[str, Any])
async def update_location(location_name: str, request: LocationUpdateRequest) -> Dict[str, Any]:
    """
    Update an existing location (full replace).
    
    Accepts multiple formats:
    - Full: `accounts/123/locations/456`
    - Short: `locations/456`
    - ID only: `456` (auto-prepends from config)
    """
    try:
        if not location_name or not location_name.strip():
            raise HTTPException(status_code=400, detail="Location name is required")
        
        full_location_name = normalize_location_name(location_name)
        existing = await read_location_from_db(full_location_name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Location '{location_name}' not found")
        
        location_dict = request.dict()
        location_dict["name"] = full_location_name
        location_dict["fetchedAt"] = now_utc()
        
        rows_written = await write_locations_to_db([location_dict])
        
        return {
            "message": "Location updated successfully",
            "location": location_dict,
            "rows_written": rows_written,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update location: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to update location: {str(e)}")


@router.patch("/locations/{location_name}", response_model=Dict[str, Any])
async def partial_update_location(location_name: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Partially update a location (merge fields).
    
    Accepts multiple formats:
    - Full: `accounts/123/locations/456`
    - Short: `locations/456`
    - ID only: `456` (auto-prepends from config)
    """
    try:
        if not location_name or not location_name.strip():
            raise HTTPException(status_code=400, detail="Location name is required")
        
        full_location_name = normalize_location_name(location_name)
        existing = await read_location_from_db(full_location_name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Location '{location_name}' not found")
        
        location_dict = dict(existing) if not isinstance(existing, dict) else existing
        location_dict.update(request)
        location_dict["name"] = full_location_name
        location_dict["fetchedAt"] = now_utc()
        
        rows_written = await write_locations_to_db([location_dict])
        
        return {
            "message": "Location partially updated successfully",
            "location": location_dict,
            "rows_written": rows_written,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to partially update location: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to partially update location: {str(e)}")


@router.delete("/locations/{location_name}", response_model=Dict[str, Any])
async def delete_location(location_name: str) -> Dict[str, Any]:
    """
    Delete a location by name (location ID).
    
    Accepts multiple formats:
    - Full: `accounts/123/locations/456`
    - Short: `locations/456`
    - ID only: `456` (auto-prepends from config)
    """
    try:
        if not location_name or not location_name.strip():
            raise HTTPException(status_code=400, detail="Location name is required")
        
        full_location_name = normalize_location_name(location_name)
        existing = await read_location_from_db(full_location_name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Location '{location_name}' not found")
        
        await delete_location_from_db(full_location_name)
        
        return {
            "message": "Location deleted successfully",
            "location_name": location_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete location: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to delete location: {str(e)}")


@router.post("/locations/batch/create", response_model=Dict[str, Any], status_code=201)
async def create_locations_batch(request: List[LocationCreateRequest]) -> Dict[str, Any]:
    """
    Create multiple locations in a batch.
    
    - **request**: List of location objects
    """
    try:
        if not request:
            raise HTTPException(status_code=400, detail="At least one location is required")
        
        locations = []
        for loc_req in request:
            loc_dict = loc_req.dict()
            loc_dict["fetchedAt"] = now_utc() if not loc_dict.get("fetchedAt") else loc_dict["fetchedAt"]
            locations.append(loc_dict)
        
        rows_written = await write_locations_to_db(locations)
        
        return {
            "message": "Locations batch created successfully",
            "count": len(locations),
            "rows_written": rows_written,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create locations batch: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create locations batch: {str(e)}")


# @router.get("/locations/debug/list", response_model=Dict[str, Any])
async def debug_list_all_locations(limit: int = Query(100, ge=1, le=10000)) -> Dict[str, Any]:
    """
    DEBUG: List all locations with their IDs to help diagnose issues.
    
    - **limit**: Maximum number of locations to return (default: 100)
    
    Returns all location names and basic info for debugging.
    """
    try:
        locations = db.execute_query(
            "SELECT name, title, storeCode, placeId FROM locations LIMIT %s",
            (limit,)
        )
        
        total_count = db.execute_query("SELECT COUNT(*) as cnt FROM locations")[0]["cnt"]
        
        return {
            "message": f"Found {len(locations)} locations",
            "total_count": total_count,
            "limit": limit,
            "locations": locations,
        }
    except Exception as e:
        logger.exception("Failed to list locations: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list locations: {str(e)}")


# @router.get("/locations/debug/search", response_model=Dict[str, Any])
async def debug_search_locations(query: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """
    DEBUG: Search locations by name, title, or store code.
    
    - **query**: Search term to find matching locations
    
    Returns matching locations for debugging.
    """
    try:
        search_term = f"%{query}%"
        locations = db.execute_query(
            """
            SELECT name, title, storeCode, placeId, fetchedAt
            FROM locations
            WHERE name LIKE %s OR title LIKE %s OR storeCode LIKE %s
            LIMIT 100
            """,
            (search_term, search_term, search_term)
        )
        
        return {
            "message": f"Found {len(locations)} matching locations",
            "query": query,
            "locations": locations,
        }
    except Exception as e:
        logger.exception("Failed to search locations: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to search locations: {str(e)}")


@router.get("/summary/location-table")
def summary_location_table(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    only_active_locations: bool = Query(False),
    limit: int = Query(10000, ge=1, le=500000),
    sort_by: str = Query("Location"),
    sort_order: str = Query("asc", regex="^(asc|desc)$")
):
    """
    Get location summary with metrics for a date range.
    
    Returns: LocationCode, Location, AverageRatingSelectedPeriod, AverageTotalRating,
             TotalReviews, NewlyAddedReviews, Positive, Negative, Neutral,
             Rating5, Rating4, Rating3, Rating2, Rating1
    """
    today = date.today()
    start_dt = _safe_iso_date(start_date) or date(today.year, 1, 1)
    end_dt = _safe_iso_date(end_date) or today
    
    try:
        sql = """
        SELECT 
            COALESCE(l.storeCode, l.name) as LocationCode,
            COALESCE(CASE 
                WHEN l.title LIKE '%%.%%' THEN CONCAT(SUBSTRING_INDEX(l.title, '.', 1), '.')
                ELSE l.title
            END, l.name) as Location,
            ROUND(COALESCE(lr.rating_average, 0), 2) as AverageTotalRating,
            COALESCE(lr.review_count, 0) as TotalReviews,
            COALESCE(pa.NewlyAddedReviews, 0) as NewlyAddedReviews,
            ROUND(COALESCE(pa.AvgRatingPeriod, 0), 2) as AverageRatingSelectedPeriod,
            COALESCE(pa.PositiveCount, 0) as Positive,
            COALESCE(pa.NegativeCount, 0) as Negative,
            COALESCE(pa.NeutralCount, 0) as Neutral,
            COALESCE(pa.Rating5, 0) as Rating5,
            COALESCE(pa.Rating4, 0) as Rating4,
            COALESCE(pa.Rating3, 0) as Rating3,
            COALESCE(pa.Rating2, 0) as Rating2,
            COALESCE(pa.Rating1, 0) as Rating1
        FROM locations l
        LEFT JOIN location_ratings lr ON l.name = lr.name
        LEFT JOIN (
            SELECT 
                name,
                COUNT(*) as NewlyAddedReviews,
                AVG(CAST(rating as DECIMAL(5,2))) as AvgRatingPeriod,
                SUM(CASE WHEN LOWER(final_sentiment) = 'positive' THEN 1 ELSE 0 END) as PositiveCount,
                SUM(CASE WHEN LOWER(final_sentiment) = 'negative' THEN 1 ELSE 0 END) as NegativeCount,
                SUM(CASE WHEN LOWER(final_sentiment) = 'neutral' THEN 1 ELSE 0 END) as NeutralCount,
                SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as Rating5,
                SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) as Rating4,
                SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) as Rating3,
                SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) as Rating2,
                SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as Rating1
            FROM location_reviews
            WHERE DATE(createTime) >= %s AND DATE(createTime) <= %s
            GROUP BY name
        ) pa ON l.name = pa.name
        WHERE 1=1
        """
        
        params = [start_dt, end_dt]
        
        if only_active_locations:
            sql += " AND (l.status IS NULL OR LOWER(l.status) = 'open')"
        
        sort_col = "Location" if sort_by not in [
            "LocationCode", "Location", "AverageTotalRating", "TotalReviews",
            "NewlyAddedReviews", "AverageRatingSelectedPeriod", "Positive",
            "Negative", "Neutral", "Rating5", "Rating4", "Rating3", "Rating2", "Rating1"
        ] else sort_by
        
        sql += f" ORDER BY {sort_col} {'ASC' if sort_order == 'asc' else 'DESC'}"
        sql += f" LIMIT {limit}"
        
        rows = db.execute_query(sql, tuple(params))
        serialized_rows = [_serialize_row(r) for r in rows]
        
        return JSONResponse({
            "start_date": start_dt.isoformat(),
            "end_date": end_dt.isoformat(),
            "count": len(serialized_rows),
            "rows": serialized_rows
        })
    
    except Exception as exc:
        logger.exception("Failed to get location summary: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to get location summary")


@router.get("/summary/location-table/export")
def export_location_table_csv(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    only_active_locations: bool = Query(False),
    limit: int = Query(100000, ge=1, le=1000000),
    sort_by: str = Query("Location"),
    sort_order: str = Query("asc", regex="^(asc|desc)$"),
    filename: str = Query("location-summary.csv")
):
    """
    Export location summary as CSV.
    
    Columns: LocationCode, Location, AverageTotalRating, TotalReviews,
             NewlyAddedReviews, AverageRatingSelectedPeriod, Positive, Negative, Neutral,
             Rating5, Rating4, Rating3, Rating2, Rating1
    """
    today = date.today()
    start_dt = _safe_iso_date(start_date) or date(today.year, 1, 1)
    end_dt = _safe_iso_date(end_date) or today
    
    try:
        sql = """
        SELECT 
            COALESCE(l.storeCode, l.name) as LocationCode,
            COALESCE(CASE 
                WHEN l.title LIKE '%%.%%' THEN CONCAT(SUBSTRING_INDEX(l.title, '.', 1), '.')
                ELSE l.title
            END, l.name) as Location,
            ROUND(COALESCE(lr.rating_average, 0), 2) as AverageTotalRating,
            COALESCE(lr.review_count, 0) as TotalReviews,
            COALESCE(pa.NewlyAddedReviews, 0) as NewlyAddedReviews,
            ROUND(COALESCE(pa.AvgRatingPeriod, 0), 2) as AverageRatingSelectedPeriod,
            COALESCE(pa.PositiveCount, 0) as Positive,
            COALESCE(pa.NegativeCount, 0) as Negative,
            COALESCE(pa.NeutralCount, 0) as Neutral,
            COALESCE(pa.Rating5, 0) as Rating5,
            COALESCE(pa.Rating4, 0) as Rating4,
            COALESCE(pa.Rating3, 0) as Rating3,
            COALESCE(pa.Rating2, 0) as Rating2,
            COALESCE(pa.Rating1, 0) as Rating1
        FROM locations l
        LEFT JOIN location_ratings lr ON l.name = lr.name
        LEFT JOIN (
            SELECT 
                name,
                COUNT(*) as NewlyAddedReviews,
                AVG(CAST(rating as DECIMAL(5,2))) as AvgRatingPeriod,
                SUM(CASE WHEN LOWER(final_sentiment) = 'positive' THEN 1 ELSE 0 END) as PositiveCount,
                SUM(CASE WHEN LOWER(final_sentiment) = 'negative' THEN 1 ELSE 0 END) as NegativeCount,
                SUM(CASE WHEN LOWER(final_sentiment) = 'neutral' THEN 1 ELSE 0 END) as NeutralCount,
                SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as Rating5,
                SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) as Rating4,
                SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) as Rating3,
                SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) as Rating2,
                SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as Rating1
            FROM location_reviews
            WHERE DATE(createTime) >= %s AND DATE(createTime) <= %s
            GROUP BY name
        ) pa ON l.name = pa.name
        WHERE 1=1
        """
        
        params = [start_dt, end_dt]
        
        if only_active_locations:
            sql += " AND (l.status IS NULL OR LOWER(l.status) = 'open')"
        
        sort_col = "Location" if sort_by not in [
            "LocationCode", "Location", "AverageTotalRating", "TotalReviews",
            "NewlyAddedReviews", "AverageRatingSelectedPeriod", "Positive",
            "Negative", "Neutral", "Rating5", "Rating4", "Rating3", "Rating2", "Rating1"
        ] else sort_by
        
        sql += f" ORDER BY {sort_col} {'ASC' if sort_order == 'asc' else 'DESC'}"
        sql += f" LIMIT {limit}"
        
        rows = db.execute_query(sql, tuple(params))
        
        if not rows:
            empty_csv = "LocationCode,Location,AverageTotalRating,TotalReviews,NewlyAddedReviews,AverageRatingSelectedPeriod,Positive,Negative,Neutral,Rating5,Rating4,Rating3,Rating2,Rating1\n"
            return StreamingResponse(
                iter([empty_csv]),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'}
            )
        
        fieldnames = [
            "LocationCode", "Location", "AverageTotalRating", "TotalReviews",
            "NewlyAddedReviews", "AverageRatingSelectedPeriod", "Positive", "Negative",
            "Neutral", "Rating5", "Rating4", "Rating3", "Rating2", "Rating1"
        ]
        
        serialized_rows = [_serialize_row(r) for r in rows]
        
        return StreamingResponse(
            _stream_csv_from_rows(serialized_rows, fieldnames),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    
    except Exception as exc:
        logger.exception("Failed to export location summary: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to export location summary")



