from fastapi import APIRouter, Body, HTTPException, Query
from typing import Any, Dict
from .config import GENAI_API_KEY
from .gemini_client import generate_response

router = APIRouter(prefix="/Gemini", tags=["Gemini"])

# @router.post("/negative_replay",status_code=200, summary="Moderate a generated reply for safety and compliance")
# def negative(review_text: str, star_rating: int, customer_name: str, store_location: str) -> Dict:
    
#     customer_name_norm = customer_name
#     store_canonical = store_location
#     star_rating_int = star_rating
#     review_text = review_text

#     if not GENAI_API_KEY:
#         heuristics = detect_attributes_and_emotion(review_text)
#         return {
#             "parsed": {
#                 "sentiment": sentiment,
#                 "emotion": heuristics["emotion"],
#                 "attributes": heuristics["attributes"],
#                 "star_rating": star_rating_int,
#                 "reply": build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
#             },
#             "quality_score": 50
#         }
    
    
#     return {
#         "customer_name": customer_name_norm,
#         "store_location": store_canonical,
#         "star_rating": star_rating_int,

#     }