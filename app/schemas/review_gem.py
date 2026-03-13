from typing import Optional, List, Union
from pydantic import BaseModel, Field

class ProcessAllReviewsRequest(BaseModel):
    location_id: Optional[str] = Field(None, description="Location ID to filter reviews")

class ManualReplyRequest(BaseModel):
    review_id: str = Field(..., description="Review ID")
    location_id: str = Field(..., description="Location ID")
    reply: str = Field(..., description="Reply text")
    sentiment: Optional[str] = Field(None, description="Sentiment override")
    emotion: Optional[str] = Field(None, description="Emotion override")
    attributes: Optional[Union[List[str], str]] = Field(None, description="Attributes override")
    quality_score: Optional[int] = Field(None, description="Quality score override")
    context_confidence: Optional[float] = Field(None, description="Context confidence override")
    update_reply: bool = Field(True, description="Whether to update the reply in the DB")
    modified_by: Optional[str] = Field(None, description="User who modified the reply")

class ProcessAllReviewsV2Request(BaseModel):
    location_id: Optional[str] = Field(None, description="Location ID to filter reviews")
