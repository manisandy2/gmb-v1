from fastapi import APIRouter
from app.services.review_service import process_review
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(prefix="/Gemini", tags=["Gemini"])

@router.post("/process")
async def process_review_api(payload: dict):
    return await process_review(payload)

class ProcessReviewRequest(BaseModel):
    review_text: str = Field(..., description="The textual content of the review")
    star_rating: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    customer_name: Optional[str] = None
    store_location: Optional[str] = None
# 3. Defined an expected response schema
class ProcessReviewResponse(BaseModel):
    sentiment: Optional[str] = None
    emotion: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    reply: str
@router.post("/process", response_model=ProcessReviewResponse)
async def process_review_api(payload: ProcessReviewRequest):
    """
    Process a review to extract sentiment, emotion, attributes, and generate an AI reply.
    """
    # We use model_dump() (or dict() in Pydantic v1) because process_review currently expects a dictionary. 
    return await process_review(payload.model_dump())