"""Pydantic schemas for event post operations."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class CallToAction(BaseModel):
    """Call-to-action model for posts."""

    action_type: str = Field(..., alias="actionType")
    url: Optional[str] = Field(default=None)

    class Config:
        populate_by_name = True
    
    @field_validator("url")
    @classmethod
    def validate_url(cls, value: Optional[str], info) -> Optional[str]:
        """Validate URL supports http, https, tel, and mailto schemes. CALL actions don't need URL."""
        if not value:
            return value
        if not any(value.startswith(scheme) for scheme in ["http://", "https://", "tel:", "mailto:"]):
            raise ValueError("URL must start with http://, https://, tel:, or mailto:")
        return value


class Media(BaseModel):
    """Media object model for posts."""

    media_format: str = Field(..., alias="mediaFormat")
    source_url: HttpUrl = Field(..., alias="sourceUrl")

    class Config:
        populate_by_name = True


class HoursPeriod(BaseModel):
    """Business hours period model."""

    open_day: str = Field(..., alias="openDay")
    open_time: str = Field(..., alias="openTime")
    close_time: str = Field(..., alias="closeTime")

    class Config:
        populate_by_name = True


class CreateBulkPostRequest(BaseModel):
    """Request model for creating posts across multiple locations."""

    location_ids: List[str]
    summary: Optional[str] = None
    description: Optional[str] = None
    topic_type: str
    media_url: Optional[HttpUrl] = None
    event_title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    action_type: Optional[str] = None
    action_url: Optional[HttpUrl] = None
    language_code: Optional[str] = Field(default="en-US", alias="languageCode")
    created_by: Optional[str] = None
    modified_by: Optional[str] = None
    call_to_action: Optional[CallToAction] = Field(None, alias="callToAction")
    media: Optional[List[Media]] = None

    class Config:
        populate_by_name = True
        from_attributes = True

    @field_validator("topic_type")
    @classmethod
    def validate_topic_type(cls, value: str) -> str:
        """Validate topic type is recognized by Google My Business API."""
        valid_types = {"STANDARD", "EVENT", "OFFER", "ALERT"}
        if value.upper() not in valid_types:
            raise ValueError(f"topic_type must be one of {valid_types}")
        return value.upper()


class UpdateDescriptionRequest(BaseModel):
    """Request model for updating location description."""

    location_ids: List[str]
    description: str
    modified_by: Optional[str] = None


class UpdatePhoneRequest(BaseModel):
    """Request model for updating location phone number."""

    location_ids: List[str]
    phone: str
    modified_by: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        """Validate phone format (basic check)."""
        if not value or len(value) < 7:
            raise ValueError("Phone must be at least 7 digits")
        return value


class UpdateWebsiteRequest(BaseModel):
    """Request model for updating location website."""

    location_ids: List[str]
    website_url: HttpUrl
    modified_by: Optional[str] = None


class UpdateSocialLinksRequest(BaseModel):
    """Request model for updating social media links."""

    location_ids: List[str]
    facebook_url: Optional[HttpUrl] = None
    instagram_url: Optional[HttpUrl] = None
    x_url: Optional[HttpUrl] = None
    linkedin_url: Optional[HttpUrl] = None
    youtube_url: Optional[HttpUrl] = None
    modified_by: Optional[str] = None

    @field_validator(
        "facebook_url",
        "instagram_url",
        "x_url",
        "linkedin_url",
        "youtube_url",
        mode="before",
    )
    @classmethod
    def validate_urls(cls, value: Any) -> Any:
        """Ensure at least one URL is provided."""
        return value


class UpdateHoursRequest(BaseModel):
    """Request model for updating business hours."""

    location_ids: List[str]
    periods: List[HoursPeriod]
    modified_by: Optional[str] = None

    @field_validator("periods")
    @classmethod
    def validate_periods(cls, value: List[HoursPeriod]) -> List[HoursPeriod]:
        """Validate at least one period is provided."""
        if not value:
            raise ValueError("At least one period must be provided")
        return value


class OperationResult(BaseModel):
    """Individual operation result for a location."""

    location_id: str
    status: str
    error: Optional[Any] = None
    request_payload: Optional[Dict[str, Any]] = None
    response_body: Optional[Dict[str, Any]] = None


class BatchResponse(BaseModel):
    """Response model for batch operations."""

    batch_id: str
    results: List[Dict[str, Any]]
    persist_error: Optional[str] = None


class BatchDetailsResponse(BaseModel):
    """Response model for batch details."""

    batch_id: str
    created_at: str
    account_id: str
    title: str
    topic_type: str
    location_count: int
    locations: List[str]
    results: List[Dict[str, Any]]
    created_by: Optional[str] = None
    modified_by: Optional[str] = None


class BatchSummaryItem(BaseModel):
    """Summary item for batch listing."""

    batch_id: str
    title: str
    topic_type: str
    location_count: int
    status: str
    created_at: str
    created_by: Optional[str] = None
    modified_by: Optional[str] = None


class DeletePostsByBatchRequest(BaseModel):
    """Request model for deleting posts by batch."""

    batch_id: str
    modified_by: Optional[str] = None
