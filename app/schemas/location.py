from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class LocationNormalized(BaseModel):
    title: str = Field(..., max_length=1024)
    name: str
    storeCode: str = ""
    status: str = ""
    primaryPhone: str = ""
    regionCode: str = ""
    administrativeArea: str = ""
    locality: str = ""
    postalCode: str = ""
    placeId: str = ""
    labels: List[str] = Field(default_factory=list)
    fetchedAt: datetime

    class Config:
        from_attributes = True


class LocationWithRatings(BaseModel):
    name: str
    storeCode: str
    title: str
    locality: str = ""
    administrativeArea: str = ""
    regionCode: str = ""
    postalCode: str = ""
    primaryPhone: str = ""
    placeId: str = ""
    status: str = ""
    labels: str = ""
    review_count: Optional[int] = None
    rating_average: Optional[float] = None
    regionName: Optional[str] = None
    fetchedAt: str = ""

    class Config:
        from_attributes = True


class ReviewSummary(BaseModel):
    name: str
    review_count: int
    rating_average: Optional[float] = None
    fetchedAt: datetime


class LocationIdentifierRequest(BaseModel):
    identifier: str = Field(..., description="Location ID, store code, or place ID")


class SyncLocationsRequest(BaseModel):
    batch_size: int = Field(100, ge=1, le=1000)
    account_id: Optional[str] = None
    stop_on_error: bool = False


class SyncLocationsResponse(BaseModel):
    message: str
    locations_synced: int
    pages: Optional[int] = None
    error: Optional[Any] = None


class ReviewSummaryRequest(BaseModel):
    account_id: Optional[str] = None
    page_size: int = Field(1, ge=1, le=200)
    concurrency: int = Field(40, ge=1, le=200)


class ReviewSummaryResponse(BaseModel):
    message: str
    locations_processed: int
    rating_summaries_upserted: int
    sample: List[Dict[str, Any]] = Field(default_factory=list)
    duration_seconds: float


class FetchLocationsRequest(BaseModel):
    name: Optional[str] = None
    placeId: Optional[str] = None
    storeCode: Optional[str] = None
    limit: int = Field(500, ge=1, le=1000)


class FetchLocationsResponse(BaseModel):
    locations: List[LocationWithRatings]
    count: int
    duration_ms: float


class AccountInfo(BaseModel):
    account_id: str
    full_resource_name: str
    account_name: str


class VerifyAccountResponse(BaseModel):
    accounts: List[AccountInfo]


class AuthStatusResponse(BaseModel):
    authenticated: bool
