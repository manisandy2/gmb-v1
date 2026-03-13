from fastapi import APIRouter
from app.services.review_batch_service import process_reviews

router = APIRouter(prefix="/reviews")

@router.post("/process_all")
async def process_all_reviews():

    return await process_reviews()

from fastapi import APIRouter
from models.review_models import ReviewRequest
from app.services.gemini_service import generate_review_reply

@router.post("/generate_reply")
async def generate_reply(request: ReviewRequest):

    result = generate_review_reply(
        request.review_text,
        request.star_rating,
        request.customer_name,
        request.store_location
    )

    return {
        "status": "success",
        "analysis": result
    }