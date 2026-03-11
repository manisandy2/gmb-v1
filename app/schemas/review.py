from datetime import datetime, date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ReviewRaw(BaseModel):
    reviewId: str
    reviewer: str = ""
    comment: str = ""
    starRating: Optional[int] = None
    createTime: datetime
    reviewReply: Optional[str] = None


class ReviewNormalized(BaseModel):
    location_id: str
    review_date: date
    reviewId: str
    storeCode: str = ""
    title: str = ""
    reviewer: str = ""
    comment: str = ""
    starRating: Optional[int] = None
    createTime: datetime
    reviewReply: str = ""
    fetchedAt: datetime
    context_sentiment: Optional[str] = None
    context_confidence: Optional[float] = None
    final_sentiment: Optional[str] = None
    attributes: Optional[str] = None
    emotion: Optional[str] = None
    quality_score: Optional[int] = None
    post_error: Optional[str] = None

    class Config:
        from_attributes = True


class LocationSummaryItem(BaseModel):
    location_id: str
    storeCode: str = ""
    title: str = ""
    total_fetched: int = 0
    unreplied_found: int = 0
    stored: int = 0
    pages: int = 0
    status: str
    error: Optional[str] = None


class FetchReviewsRequest(BaseModel):
    locations: Optional[List[str]] = Field(None, description="Specific location IDs or 'all'")
    only_unreplied: bool = Field(False, description="Only unreplied reviews")
    max_reviews_per_location: int = Field(200, ge=1, le=500)
    concurrency: int = Field(30, ge=1, le=100)
    dedup_mode: str = Field("merge", description="upsert, merge, or insert")


class FetchReviewsResponse(BaseModel):
    message: str
    total_fetched: int
    total_stored: int
    total_unreplied: int
    per_location: List[LocationSummaryItem] = Field(default_factory=list)
    duration_seconds: float


class DuplicateCheckRequest(BaseModel):
    limit: int = Field(10000, ge=1, le=100000)


class DuplicateCheckResponse(BaseModel):
    duplicates_found: int
    affected_locations: int
    sample_duplicates: List[Dict[str, Any]] = Field(default_factory=list)


class ListReviewsRequest(BaseModel):
    location_id: Optional[str] = None
    reviewer: Optional[str] = None
    limit: int = Field(1000, ge=1, le=10000)


class ListReviewsResponse(BaseModel):
    reviews: List[ReviewNormalized] = Field(default_factory=list)
    count: int
    location_id: Optional[str] = None


class CacheClearRequest(BaseModel):
    cache_type: str = Field("reviews", description="reviews, metadata, or all")


class CacheClearResponse(BaseModel):
    message: str
    cleared: bool


class CacheStatsResponse(BaseModel):
    review_cache_size: int
    metadata_cache_size: int
    total_cached_items: int


class GetReviewsRequest(BaseModel):
    location_id: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = Field(500, ge=1, le=5000)


class GetReviewsResponse(BaseModel):
    reviews: List[ReviewNormalized] = Field(default_factory=list)
    location_id: str
    count: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class LocationRatingSummary(BaseModel):
    location_id: str
    location_name: str
    store_code: str = ""
    title: str = ""
    total_reviews: int
    average_rating: float
    unreplied_count: int
    replied_count: int
    one_star: int = 0
    two_star: int = 0
    three_star: int = 0
    four_star: int = 0
    five_star: int = 0
    fetchedAt: str


class LocationTableSummaryResponse(BaseModel):
    locations: List[LocationRatingSummary] = Field(default_factory=list)
    count: int
    duration_seconds: float


class LocationTableExportRequest(BaseModel):
    format: str = Field("csv", description="csv or json")
    include_sentiment: bool = False


class DailySummary(BaseModel):
    date: str
    total_reviews: int
    average_rating: float
    positive_reviews: int
    neutral_reviews: int
    negative_reviews: int
    unreplied_count: int


class DailySummaryResponse(BaseModel):
    summary: List[DailySummary] = Field(default_factory=list)
    start_date: str
    end_date: str
    total_reviews: int
    average_rating: float


class ExportReviewsRequest(BaseModel):
    format: str = Field("csv", description="csv or json")
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class DeleteReviewsRequest(BaseModel):
    review_ids: List[str] = Field(..., description="Review IDs to delete")


class DeleteReviewsResponse(BaseModel):
    message: str
    deleted_count: int
    failed_count: int
