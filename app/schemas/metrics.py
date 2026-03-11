from datetime import datetime, date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DateRange(BaseModel):
    start: str = Field(..., description="YYYY-MM-DD")
    end: str = Field(..., description="YYYY-MM-DD")


class FetchMetricsRequest(BaseModel):
    start_date: str = Field(..., description="YYYY-MM-DD")
    end_date: str = Field(..., description="YYYY-MM-DD")
    metrics: Optional[List[str]] = Field(None, description="Friendly metrics list")
    location_ids: Optional[List[str]] = None
    concurrency: int = Field(50, ge=1, le=200)
    rate_limit_per_minute: int = Field(600, ge=60, le=6000)
    auto_save: bool = True
    test_mode: bool = False
    deduplicate_after: bool = True


class LocationMetricsResult(BaseModel):
    location_id: str
    success: bool
    date_range: Optional[DateRange] = None
    metrics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class FetchMetricsResponse(BaseModel):
    timestamp: str
    date_range: DateRange
    metrics_requested: List[str]
    locations_requested: int
    locations_fetched: int
    locations_failed: int
    rows_saved: int
    results: List[LocationMetricsResult] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)


class QueryMetricsRequest(BaseModel):
    date_from: str = Field(..., description="YYYY-MM-DD")
    date_to: str = Field(..., description="YYYY-MM-DD")
    location_ids: Optional[List[str]] = None
    store_codes: Optional[List[str]] = None
    store_names: Optional[List[str]] = None
    metrics: Optional[List[str]] = None
    aggregate_by: str = Field("location", description="location, date, region, or store")
    include_time_series: bool = True


class TimeSeriesData(BaseModel):
    date: str
    value: float
    metric: str


class LocationMetricsData(BaseModel):
    location_id: str
    store_code: Optional[str] = None
    store_name: Optional[str] = None
    metrics: Dict[str, float] = Field(default_factory=dict)
    time_series: Optional[List[TimeSeriesData]] = None


class QueryMetricsResponse(BaseModel):
    date_range: DateRange
    aggregate_by: str
    data: List[LocationMetricsData] = Field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None


class OptimizeMetricsRequest(BaseModel):
    compact_files: bool = True
    reorder_data: bool = True


class OptimizeMetricsResponse(BaseModel):
    message: str
    files_compacted: int
    rows_reordered: int


class DeduplicateMetricsRequest(BaseModel):
    keep_latest: bool = True


class DeduplicateMetricsResponse(BaseModel):
    message: str
    duplicates_removed: int
    rows_affected: int


class MetricsStatsResponse(BaseModel):
    total_rows: int
    date_range: DateRange
    locations_covered: int
    metrics_available: List[str] = Field(default_factory=list)
    last_update: Optional[str] = None


class PurgeMetricsRequest(BaseModel):
    start_date: str = Field(..., description="YYYY-MM-DD")
    end_date: str = Field(..., description="YYYY-MM-DD")
    location_ids: Optional[List[str]] = None
    confirm: bool = Field(False, description="Must be true to confirm deletion")


class PurgeMetricsResponse(BaseModel):
    message: str
    rows_deleted: int
    date_range_deleted: DateRange
    locations_affected: int
