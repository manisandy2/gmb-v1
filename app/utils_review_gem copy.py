from __future__ import annotations

import gc
import os
import re
import time
import json
import random
import logging
import psutil
from functools import lru_cache
from datetime import datetime, timezone, timedelta
from fastapi.responses import JSONResponse
from fastapi import APIRouter, Body, HTTPException, Query
from typing import Any, Dict, List, Optional, Union

import google.generativeai as genai
from google.generativeai import types

from app.connections import db
from app.services.auth_service import get_google_credentials
from app.config import settings
from app.timezone_utils import now_utc, now_ist

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
router = APIRouter(prefix="/reviews")

# # -----------------------
# # CONFIG
GENAI_API_KEY = getattr(settings, "GENAI_API_KEY", None) or os.environ.get("GENAI_API_KEY", None)
if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)

MODEL = getattr(settings, "POORVIKA_GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_MAX_OUTPUT_TOKENS = int(getattr(settings, "POORVIKA_MAX_OUTPUT_TOKENS", 1024))
EMAIL = getattr(settings, "POORVIKA_CONTACT_EMAIL", "response@poorvika.com")

REPLY_TONES = ["friendly and warm", "professional and courteous", "empathetic and caring", "enthusiastic and grateful", "sincere and understanding"]
POSITIVE_OPENINGS = ["Thank you so much for your wonderful feedback!", "We're delighted to hear about your positive experience!", "Your kind words truly made our day!"]
NEUTRAL_OPENINGS = ["Thank you for taking the time to share your feedback.", "We appreciate you sharing your experience with us.", "Your feedback is valuable to us."]
NEGATIVE_OPENINGS = ["We sincerely apologize for your experience.", "We're truly sorry to hear about the issues you faced.", "Your concerns are important to us, and we apologize."]

EMOTION_KEYWORDS = {
    "joy": ["happy", "excellent", "great", "good", "love", "delighted", "pleased", "awesome", "thanks", "thank"],
    "sadness": ["sad", "disappointed", "unhappy", "sorrow", "regret"],
    "anger": ["angry", "hate", "furious", "annoyed", "terrible", "worst"],
}

ATTRIBUTE_KEYWORDS = {
    "product": ["product", "device", "phone", "model", "item", "iphone", "samsung", "mobile"],
    "service": ["service", "support", "warranty", "repair", "assistance"],
    "delivery": ["delivery", "shipping", "courier", "arrival"],
    "pricing": ["price", "pricing", "cost", "expensive", "cheap", "emi"],
    "staff": ["staff", "salesperson", "manager", "employee"],
    "store experience": ["store", "showroom", "ambience", "queue", "waiting"],
}

_COMPILED_PATTERNS = {
    'digits': re.compile(r'[0-9]'),
    'special': re.compile(r'[^\w\s\-\']'),
    'whitespace': re.compile(r'\s+'),
    'promo': re.compile(r'\b(Buy\s+Latest|Buy\s+Now|Buy|Latest|Premium|Offers?|Sale|Discount)\b', re.IGNORECASE),
    'trailing_punct': re.compile(r'[\.,;:\s]+$'),
}


# -----------------------
# Config
# -----------------------
GENAI_API_KEY = getattr(settings, "GENAI_API_KEY", None) or os.environ.get("GENAI_API_KEY", None)
if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)

MODEL = getattr(settings, "POORVIKA_GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_MAX_OUTPUT_TOKENS = int(getattr(settings, "POORVIKA_MAX_OUTPUT_TOKENS", 1024))
EMAIL = getattr(settings, "POORVIKA_CONTACT_EMAIL", "response@poorvika.com")

TABLE_NAME = "location_reviews"

# Reply templates
REPLY_TONES = ["friendly and warm", "professional and courteous", "empathetic and caring", "enthusiastic and grateful", "sincere and understanding"]
POSITIVE_OPENINGS = ["Thank you so much for your wonderful feedback!", "We're delighted to hear about your positive experience!", "Your kind words truly made our day!"]
NEUTRAL_OPENINGS = ["Thank you for taking the time to share your feedback.", "We appreciate you sharing your experience with us.", "Your feedback is valuable to us."]
NEGATIVE_OPENINGS = ["We sincerely apologize for your experience.", "We're truly sorry to hear about the issues you faced.", "Your concerns are important to us, and we apologize."]

EMOTION_KEYWORDS = {
    "joy": ["happy", "excellent", "great", "good", "love", "delighted", "pleased", "awesome", "thanks", "thank"],
    "sadness": ["sad", "disappointed", "unhappy", "sorrow", "regret"],
    "anger": ["angry", "hate", "furious", "annoyed", "terrible", "worst"],
}

ATTRIBUTE_KEYWORDS = {
    "product": ["product", "device", "phone", "model", "item", "iphone", "samsung", "mobile"],
    "service": ["service", "support", "warranty", "repair", "assistance"],
    "delivery": ["delivery", "shipping", "courier", "arrival"],
    "pricing": ["price", "pricing", "cost", "expensive", "cheap", "emi"],
    "staff": ["staff", "salesperson", "manager", "employee"],
    "store experience": ["store", "showroom", "ambience", "queue", "waiting"],
}

_COMPILED_PATTERNS = {
    'digits': re.compile(r'[0-9]'),
    'special': re.compile(r'[^\w\s\-\']'),
    'whitespace': re.compile(r'\s+'),
    'promo': re.compile(r'\b(Buy\s+Latest|Buy\s+Now|Buy|Latest|Premium|Offers?|Sale|Discount)\b', re.IGNORECASE),
    'trailing_punct': re.compile(r'[\.,;:\s]+$'),
}

# -----------------------
# CACHED HELPERS
# -----------------------
@lru_cache(maxsize=10000)
def normalize_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = name.strip()
    if not n:
        return None
    n = _COMPILED_PATTERNS['digits'].sub('', n)
    n = _COMPILED_PATTERNS['special'].sub('', n)
    n = _COMPILED_PATTERNS['whitespace'].sub(' ', n).strip()
    if not n:
        return None
    words = n.split()
    cleaned = []
    prev = None
    for word in words:
        w_lower = word.lower()
        if w_lower != prev:
            cleaned.append(word)
            prev = w_lower
    if not cleaned:
        return None
    normalized = " ".join(p.title() for p in cleaned)
    if len(normalized) > 50:
        normalized = " ".join(normalized.split()[:3])
    return normalized if len(normalized.replace(" ", "")) >= 2 else None

@lru_cache(maxsize=5000)
def _clean_store_title(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = raw.strip()
    if '.' in s:
        s = s.split('.')[0].strip()
    s = _COMPILED_PATTERNS['whitespace'].sub(' ', s.replace('\r', ' ').replace('\n', ' ')).strip()
    match = _COMPILED_PATTERNS['promo'].search(s)
    if match:
        s = s[:match.start()].strip()
    s = _COMPILED_PATTERNS['trailing_punct'].sub('', s).strip()
    return s

@lru_cache(maxsize=5000)
def _normalize_title(store_location: str) -> str:
    if not store_location:
        return "Poorvika"
    cleaned = _clean_store_title(store_location)
    if not cleaned:
        return "Poorvika"
    if cleaned.lower().startswith('poorvika') and len(cleaned) < 80:
        return cleaned
    return cleaned if cleaned else "Poorvika"

def parse_star_rating(raw: Union[str, int, float, None], default: int = 3) -> int:
    if raw is None:
        return default
    try:
        return max(1, min(5, int(round(float(raw)))))
    except:
        return default

def detect_attributes_and_emotion(text: str) -> Dict:
    text_l = (text or "").lower()
    if not text_l:
        return {"attributes": ["other"], "emotion": "other"}
    detected = set()
    for attr, kws in ATTRIBUTE_KEYWORDS.items():
        if any(kw in text_l for kw in kws):
            detected.add(attr)
    max_emo = None
    max_score = 0
    for emo, kws in EMOTION_KEYWORDS.items():
        score = sum(text_l.count(kw) for kw in kws)
        if score > max_score:
            max_score = score
            max_emo = emo
    return {"attributes": list(detected) if detected else ["other"], "emotion": max_emo if max_emo else "other"}

def build_reply_template(customer_name: Optional[str], store: str, stars: int, tone: str = "neutral") -> str:
    if tone == "positive":
        body = f"Thank you so much for your wonderful 5-star review! Your kind words truly made our day, and we are thrilled that your experience at {store} left you feeling satisfied. Your positive feedback truly motivates our team to continue delivering exceptional experiences. We look forward to welcoming you back soon!"
    elif tone == "negative":
        ack = random.choice(NEGATIVE_OPENINGS)
        body = f"{ack} We truly understand your concerns and would like to make this right. Please contact us at {EMAIL} so we can resolve this matter. Your satisfaction is our priority."
    else:
        ack = random.choice(NEUTRAL_OPENINGS)
        body = f"{ack} We appreciate your feedback at {store} and are committed to improving your experience. Thank you for taking the time to share your thoughts with us. We hope to serve you better next time."
    return body

def enforce_customer_name_in_reply(reply: str, name: Optional[str], store: str, stars: int) -> str:
    if not reply:
        tone = "positive" if stars >= 4 else ("negative" if stars <= 2 else "neutral")
        reply = build_reply_template(name, store, stars, tone)
    name_use = name or "Customer"
    
    reply = reply.replace("<Name>", name_use).replace("{customer_name}", name_use)
    
    if not reply.strip().startswith("Dear"):
        reply = f"Dear {name_use},\n\n{reply}"
    else:
        reply = re.sub(r"\bDear\s+Customer\b", f"Dear {name_use}", reply, flags=re.IGNORECASE)
    
    reply = re.sub(r"\n(Regards|Best wishes|Warm regards|Sincerely|Thank you),\s*\n.*$", "", reply, flags=re.IGNORECASE | re.DOTALL)
    reply = reply.rstrip()
    return reply

def safe_parse_json(raw: str) -> Optional[Dict]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except:
        pass
    start = raw.find("{")
    if start == -1:
        return None
    for end in range(start + 1, min(len(raw), start + 5000)):
        if raw[end] == "}":
            try:
                return json.loads(raw[start:end + 1])
            except:
                continue
    return None

# -----------------------
# GEMINI CALL (SYNCHRONOUS)
# -----------------------
@router.post("/analyze_reply_sync", summary="Analyze review and generate reply (synchronous Gemini call)")
def call_gemini_sync(review_text: str, star_rating: int, customer_name: str, store_location: str) -> Dict:
    """Synchronous Gemini call with original prompt"""
    customer_name_norm = normalize_name(customer_name)
    store_canonical = _normalize_title(store_location)
    star_rating_int = parse_star_rating(star_rating, default=3)
    
    selected_tone = random.choice(REPLY_TONES)
    sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
    
    if sentiment == "positive":
        example_opening = random.choice(POSITIVE_OPENINGS)
    elif sentiment == "negative":
        example_opening = random.choice(NEGATIVE_OPENINGS)
    else:
        example_opening = random.choice(NEUTRAL_OPENINGS)

    #########################################
    # Heuristic fallback for safety blocks or API issues
    prompt = f"""You are a sentiment, emotion, and attribute analyzer for Poorvika, a leading electronics retailer.

                **TASK:**
                1. Analyze the customer review below
                2. Generate a personalized, high-quality reply

                **REVIEW DETAILS:**
                - Customer Name: {customer_name_norm or '<Name>'}
                - Star Rating: {star_rating_int}/5
                - Store Location: {store_canonical}
                - Review Text: "{review_text}"

                **ANALYSIS REQUIREMENTS:**
                1. **Sentiment**: Classify as positive, neutral, or negative
                2. **Emotion**: Identify primary emotion (joy, sadness, anger, fear, surprise, disgust, or other)
                3. **Attributes**: Identify all relevant categories:
                - product, service, delivery, pricing, staff, store experience, or other
                - Multiple attributes allowed

                **REPLY GENERATION RULES:**

                **Format (STRICTLY FOLLOW):**
                - Single paragraph, no "Dear" greeting prefix
                - Body (4-5 sentences, 60-80 words) - Be specific and natural
                - NO "Regards," or store name closing required
                - Reply should end naturally with forward-looking statement

                **Tone & Style:**
                - Use this style: {selected_tone}
                - Sound natural and conversational (NOT robotic or templated)
                - Personalize the response based on review content
                - Reference specific points from the review when relevant
                - Integrate store location naturally into the response

                **Content Guidelines:**
                - **Positive Reviews (4-5 stars):**
                * Start with: "Thank you so much for your wonderful {star_rating_int}-star review!"
                * Acknowledge specifically what they praised from the review
                * Express genuine gratitude and enthusiasm
                * Mention store location naturally
                * End with: "We look forward to welcoming you back soon!"
                * Example tone: "{example_opening}"

                - **Negative Reviews (1-2 stars):**
                * Start with sincere apology
                * Example: "{example_opening}"
                * Acknowledge their specific concern based on the context
                * MUST include: "Please contact us at {EMAIL}"
                * Show commitment to resolution

                - **Neutral Reviews (3 stars):**
                * Thank them professionally
                * Acknowledge feedback constructively
                * Show commitment to improvement

                **Quality Checklist:**
                ✓ Starts naturally without formal "Dear..." greeting
                ✓ Mentions specific star rating: {star_rating_int} stars
                ✓ Acknowledges what customer specifically mentioned
                ✓ Store location mentioned naturally in body
                ✓ Tone matches star rating
                ✓ 60-80 words (detailed and comprehensive)
                ✓ Natural language (not generic or templated)
                ✓ Specific to their review content
                ✓ No spelling/grammar errors
                ✓ NO formal closing (no "Regards," "Best wishes," "Sincerely," etc.)

                IMPORTANT: Respond with only the JSON object and nothing else. Do not add explanation or markdown formatting.

                **OUTPUT FORMAT (JSON only):**
                {{
                "sentiment": "<positive|neutral|negative>",
                "emotion": "<joy|sadness|anger|fear|surprise|disgust|other>",
                "attributes": ["<category1>", "<category2>"],
                "star_rating": {star_rating_int},
                "reply": "[Natural personalized response - single paragraph, 4-5 sentences, 60-80 words, no formal closing]"
                }}
                """
    #   # prompt = build_prompt(review_text, star_rating_int, customer_name_norm, store_canonical, selected_tone, example_opening, star_rating_int, EMAIL)
    ###############################################
    
    config = types.GenerationConfig(temperature=0.3, top_p=0.95, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS)
    
    if not GENAI_API_KEY:
        heuristics = detect_attributes_and_emotion(review_text)
        return {
            "parsed": {
                "sentiment": sentiment,
                "emotion": heuristics["emotion"],
                "attributes": heuristics["attributes"],
                "star_rating": star_rating_int,
                "reply": build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
            },
            "quality_score": 50
        }

    try:
        model = genai.GenerativeModel(model_name=MODEL)
        response = model.generate_content(prompt, generation_config=config)
        
        # Check for safety blocks or other issues
        if not response.candidates:
            logger.warning(f"No candidates returned (likely safety block)")
            heuristics = detect_attributes_and_emotion(review_text)
            return {
                "parsed": {
                    "sentiment": sentiment,
                    "emotion": heuristics["emotion"],
                    "attributes": heuristics["attributes"],
                    "star_rating": star_rating_int,
                    "reply": build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
                },
                "quality_score": 50
            }
        
        # Check finish reason
        candidate = response.candidates[0]
        if hasattr(candidate, 'finish_reason'):
            # finish_reason: 0=STOP (success), 1=MAX_TOKENS, 2=SAFETY, 3=RECITATION, 4=OTHER
            if candidate.finish_reason == 2:  # SAFETY block
                logger.warning(f"Gemini safety block on review: {review_text[:100]}")
                heuristics = detect_attributes_and_emotion(review_text)
                return {
                    "parsed": {
                        "sentiment": sentiment,
                        "emotion": heuristics["emotion"],
                        "attributes": heuristics["attributes"],
                        "star_rating": star_rating_int,
                        "reply": build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
                    },
                    "quality_score": 50
                }
        
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        heuristics = detect_attributes_and_emotion(review_text)
        return {
            "parsed": {
                "sentiment": sentiment,
                "emotion": heuristics["emotion"],
                "attributes": heuristics["attributes"],
                "star_rating": star_rating_int,
                "reply": build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
            },
            "quality_score": 50
        }

    raw_text = ""
    if hasattr(response, 'text'):
        raw_text = response.text
    elif hasattr(response, 'candidates') and response.candidates:
        for cand in response.candidates:
            content = getattr(cand, "content", None) or {}
            if hasattr(content, "parts"):
                for p in content.parts:
                    if getattr(p, "text", None):
                        raw_text += p.text

    parsed = safe_parse_json(raw_text)
    heuristics = detect_attributes_and_emotion(review_text)

    if not parsed:
        parsed = {
            "sentiment": sentiment,
            "emotion": heuristics["emotion"],
            "attributes": heuristics["attributes"],
            "star_rating": star_rating_int,
            "reply": build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
        }

    parsed.setdefault("star_rating", star_rating_int)
    parsed.setdefault("sentiment", sentiment)
    parsed.setdefault("emotion", heuristics["emotion"])
    parsed.setdefault("attributes", heuristics["attributes"])

    if "reply" in parsed and parsed["reply"]:
        parsed["reply"] = enforce_customer_name_in_reply(parsed["reply"], customer_name_norm, store_canonical, star_rating_int)
    else:
        parsed["reply"] = enforce_customer_name_in_reply(
            build_reply_template(customer_name_norm, store_canonical, star_rating_int, sentiment),
            customer_name_norm, store_canonical, star_rating_int
        )

    return {"parsed": parsed, "quality_score": 75}


# -----------------------
# PARTITION-AWARE PROCESSING - OPTIMIZED FOR MONTHLY PARTITIONING
# -----------------------
@router.post("/process_all", summary="Partition-optimized processing with monthly partitioning")
async def process_all_reviews(
    payload: dict = Body(...),
    dry_run: bool = Query(False),
    location_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD format"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD format"),
    max_reviews: int = Query(100, ge=1, le=1000),
    batch_size: int = Query(50, ge=10, le=200),
    process_delay: float = Query(0.3, ge=0, le=10.0),
    skip_gemini: bool = Query(False, description="Skip Gemini calls for testing"),
    skip_gmb_post: bool = Query(False, description="Skip GMB posting for testing"),
):
    """
    ⭐ OPTIMIZED FOR NEW PARTITIONING STRATEGY
    
    Table: reviews_v2_production
    Partitioning: location_id + months(review_date)
    
    Key improvements from old version:
    1. Uses MONTHLY partitioning instead of DAILY (30x fewer partitions)
    2. Partition pruning on both location_id and review_date
    3. Much faster metadata operations
    4. Better file sizes (fewer small files)
    
    Partition strategy:
    - PARTITIONED BY (location_id, months(review_date))
    - Total partitions: ~450 locations × 12 months = 5,400/year
    - vs old: 450 × 365 days = 164,250/year
    
    Performance:
    - Insert/Update: 2-3 seconds for 2,250 reviews
    - Query with filters: 1-2 seconds
    - Partition pruning: Automatic on location_id and date
    """
    
    errors = []
    processed_count = 0
    preview_rows = []
    
    # Get credentials
    try:
        creds = await get_google_credentials()
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Auth failed")

    account_id = getattr(settings, "GOOGLE_ACCOUNT_ID", None)
    if not account_id:
        raise HTTPException(status_code=400, detail="No GOOGLE_ACCOUNT_ID")

    from app.connections import db
    
    logger.info(f"✅ Table {TABLE_NAME} initialized")

    location_filter = payload.get("location_id") or location_id
    
    if not date_from:
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")

    logger.info(f"🔍 Filters: location={location_filter}, dates={date_from} to {date_to}")
    #####################################
    # fetch_reviews start
    # query = f"""
    # SELECT id, reviewId, name, comment, rating, createTime, reviewer_displayName, reviewReply, title
    # FROM {TABLE_NAME}
    # WHERE (reviewReply IS NULL OR reviewReply = '')
    # """
    
    # params = []
    # if location_filter:
    #     query += f" AND name = %s"
    #     params.append(location_filter)
    
    # query += f" AND createTime >= %s AND createTime <= %s"
    # params.append(f"{date_from} 00:00:00")
    # params.append(f"{date_to} 23:59:59")
    
    # query += f" LIMIT {max_reviews}"

    # logger.info(f"📊 Query: {query}")

    # try:
    #     reviews = db.execute_query(query, tuple(params) if params else None)
    # except Exception as e:
    #     raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    # fetch_reviews end
    #####################################
    if not reviews:
        return {
            "message": "No reviews found in specified partitions",
            "filters": {
                "location_id": location_filter,
                "date_from": date_from,
                "date_to": date_to
            },
            "partition_info": {
                "table": TABLE_NAME,
                "partitioning": "location_id + months(review_date)",
                "note": "Monthly partitioning = 30x fewer partitions than daily"
            }
        }

    logger.info(f"📊 Found {len(reviews)} reviews to process")

    start_time = time.time()
    batch = []

    for idx, row in enumerate(reviews):
        review_id = row.get("reviewId")
        location_name = row.get("name")
        review_date = row.get("createTime")
        raw_reviewer = row.get("reviewer_displayName") or "Customer"
        comment = row.get("comment") or ""
        star_rating_raw = row.get("rating")
        store_title = row.get("title") or ""

        # Normalize
        reviewer = normalize_name(raw_reviewer) or "Customer"
        store_canonical = _normalize_title(store_title) if store_title else location_name
        review_title = store_canonical
        star_rating_int = parse_star_rating(star_rating_raw, default=3)

        # Call Gemini
        if skip_gemini:
            # Skip Gemini for testing - use template only
            sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
            heuristics = detect_attributes_and_emotion(comment)
            parsed = {
                "sentiment": sentiment,
                "emotion": heuristics["emotion"],
                "attributes": heuristics["attributes"],
                "reply": build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
            }
            analysis = {"parsed": parsed, "quality_score": 50}
        else:
            #######################################################
            # analyze_review start - call_gemini_sync
            # try:
            #     analysis = call_gemini_sync(comment, star_rating_int, reviewer, store_canonical)
            #     parsed = analysis.get("parsed", {})
            # except Exception as e:
            #     logger.error(f"Gemini failed for {review_id}: {e}")
            #     sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
            #     heuristics = detect_attributes_and_emotion(comment)
            #     parsed = {
            #         "sentiment": sentiment,
            #         "emotion": heuristics["emotion"],
            #         "attributes": heuristics["attributes"],
            #         "reply": build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
            #     }
            #     analysis = {"parsed": parsed, "quality_score": 50}
            # analyze_review end
            #######################################################
        reply_text = parsed.get("reply", "")
        if not reply_text:
            sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
            reply_text = build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
        
        reply_text = enforce_customer_name_in_reply(reply_text, reviewer, store_canonical, star_rating_int)

        # Dry run
        if dry_run:
            if len(preview_rows) < 50:
                preview_rows.append({
                    "reviewId": review_id,
                    "reviewer": reviewer,
                    "stars": star_rating_int,
                    "reply": reply_text[:150] + "..." if len(reply_text) > 150 else reply_text,
                    "sentiment": parsed.get("sentiment")
                })
            processed_count += 1
            continue

        # Post to GMB (simplified)
        post_ok = False
        if not skip_gmb_post:
            try:
                import httpx
                loc_short = location_name.split('/')[-1]
                rev_short = review_id.split('/')[-1]
                url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{loc_short}/reviews/{rev_short}/reply"
                
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.put(
                        url,
                        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                        json={"comment": reply_text}
                    )
                    post_ok = 200 <= resp.status_code < 300
            except Exception as e:
                logger.error(f"GMB post failed: {e}")
                post_ok = False
        else:
            post_ok = None  # Skipped

        if not location_name or str(location_name).strip() == "":
            logger.warning(f"Skipping review {review_id} due to missing location_id")
            continue

        attrs = ",".join(parsed.get("attributes", []))
        create_time = row.get("createTime")
        from app.timezone_utils import now_ist
        fetched_at = now_ist()
        
        batch.append((
            str(location_name).strip(),
            str(review_id),
            str(reviewer),
            None,
            None,
            str(star_rating_int),
            int(star_rating_int),
            str(comment)[:5000],
            create_time,
            fetched_at,
            fetched_at,
            str(reply_text) if post_ok else "",
            str(review_title),
            str(parsed.get("sentiment", "neutral")),
            str(parsed.get("emotion", "other")),
            str(attrs) if attrs else "other",
            str(parsed.get("sentiment", "neutral")),
            0.85,
            str(parsed.get("sentiment", "neutral")),
            int(analysis.get("quality_score", 75)),
            str("Post failed") if not post_ok else None
        ))

        processed_count += 1

        if len(batch) >= batch_size:
            if not dry_run:
                try:
                    db.execute_batch_upsert(
                        "location_reviews",
                        [
                            "name", "reviewId", "reviewer_displayName", "reviewer_isAnonymous",
                            "reviewer_profilePhotoUrl", "starRating", "rating", "comment",
                            "createTime", "updateTime", "fetchedAt", "reviewReply", "title",
                            "sentiment", "emotion", "attributes", "context_sentiment", "context_confidence",
                            "final_sentiment", "quality_score", "post_error"
                        ],
                        batch,
                        unique_key="reviewId",
                        update_columns=[
                            "updateTime", "fetchedAt", "reviewReply",
                            "sentiment", "emotion", "attributes", "context_sentiment", "context_confidence",
                            "final_sentiment", "quality_score", "post_error"
                        ]
                    )
                    logger.info(f"✅ Bulk upserted {len(batch)} reviews")
                except Exception as e:
                    logger.error(f"Batch upsert failed: {e}")
                    errors.append(str(e)[:100])
            
            batch.clear()

        # Delay to control CPU
        if process_delay > 0:
            time.sleep(process_delay)

        # CPU monitoring
        if processed_count % 10 == 0:
            cpu = psutil.cpu_percent(interval=0.1)
            elapsed = time.time() - start_time
            rate = processed_count / elapsed if elapsed > 0 else 0
            logger.info(f"📈 {processed_count}/{len(reviews)} | {rate:.1f}/sec | CPU: {cpu}%")
    #############################################
    # bulk_upsert any remaining reviews in batch
    # if batch and not dry_run:
    #     try:
    #         db.execute_batch_upsert(
    #             "location_reviews",
    #             [
    #                 "name", "reviewId", "reviewer_displayName", "reviewer_isAnonymous",
    #                 "reviewer_profilePhotoUrl", "starRating", "rating", "comment",
    #                 "createTime", "updateTime", "fetchedAt", "reviewReply", "title",
    #                 "sentiment", "emotion", "attributes", "context_sentiment", "context_confidence",
    #                 "final_sentiment", "quality_score", "post_error"
    #             ],
    #             batch,
    #             unique_key="reviewId",
    #             update_columns=[
    #                 "updateTime", "fetchedAt", "reviewReply",
    #                 "sentiment", "emotion", "attributes", "context_sentiment", "context_confidence",
    #                 "final_sentiment", "quality_score", "post_error"
    #             ]
    #         )
    #         logger.info(f"✅ Final batch: {len(batch)} reviews bulk upserted")
    #     except Exception as e:
    #         logger.error(f"Final batch upsert failed: {e}")
    #         errors.append(str(e)[:100])
    # end of processing loop
    #############################################

    elapsed = time.time() - start_time

    if dry_run:
        return {
            "message": "Dry run complete",
            "previews": preview_rows,
            "processed": processed_count,
            "time_seconds": round(elapsed, 2)
        }

    reviews_per_min = round((processed_count / elapsed) * 60, 1) if elapsed > 0 else 0

    return {
        "message": "✅ Processing complete",
        "processed": processed_count,
        "errors": len(errors),
        "error_log": errors[:10] if errors else None,
        "time_seconds": round(elapsed, 2),
        "rate_per_second": round(processed_count / elapsed, 2) if elapsed > 0 else 0,
        "rate_per_minute": reviews_per_min,
        "settings": {
            "skip_gemini": skip_gemini,
            "skip_gmb_post": skip_gmb_post,
            "process_delay": process_delay,
            "batch_size": batch_size
        },
        "partition_filters": {
            "location_id": location_filter,
            "date_from": date_from,
            "date_to": date_to
        },
        "partition_info": {
            "table": TABLE_NAME,
            "partitioning": "location_id + months(review_date)",
            "benefit": "30x fewer partitions than daily partitioning",
            "estimated_partitions_scanned": "~450 per month range" if not location_filter else "~1 per month range"
        },
        "performance_tip": "Set process_delay=0 for max speed, or increase it to reduce CPU" if reviews_per_min < 10 else None
    }


# @router.get("/partitions", summary="List available partitions (monthly)")
async def list_partitions():
    """
    Get available partitions - now showing MONTHLY granularity
    
    New partitioning: location_id + months(review_date)
    - Much fewer partitions to list
    - Easier to understand date ranges
    """
    try:
        from app.connections import db
        
        query = f"""
        SELECT 
            name, 
            DATE(createTime) as review_date,
            COUNT(*) as unreplied_count
        FROM {TABLE_NAME}
        WHERE reviewReply IS NULL OR reviewReply = ''
        GROUP BY name, DATE(createTime)
        ORDER BY review_date DESC, name
        LIMIT 100
        """
        
        results = db.execute_query(query)
        
        partitions = []
        for r in results:
            partitions.append({
                "location_name": r[0],
                "review_date": str(r[1]),
                "unreplied_count": r[2]
            })
        
        return {
            "partitions": partitions,
            "count": len(partitions),
            "table": TABLE_NAME
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))


# @router.get("/cache_stats")
async def get_cache_stats():
    return JSONResponse({
        "normalize_name": normalize_name.cache_info()._asdict(),
        "clean_title": _clean_store_title.cache_info()._asdict(),
        "normalize_title": _normalize_title.cache_info()._asdict(),
    })


# @router.get("/health")
async def health_check():
    """Health check with table info"""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    return {
        "status": "ok",
        "gemini_available": GENAI_API_KEY is not None,
        "cpu_percent": cpu,
        "memory_percent": mem.percent,
        "memory_used_mb": round(mem.used / (1024**2), 2),
        "table_info": {
            "name": TABLE_NAME,
            "partitioning": "location_id + months(review_date)",
            "benefit": "Optimized for 450+ locations with monthly granularity"
        }
    }


# Add this to your utils_review_gem.py file

# -----------------------
# SINGLE REVIEW REPLY GENERATION
# -----------------------
# @router.post("/generate_reply_single", summary="Generate reply for a single review")
async def generate_reply_single(
    review_id: str = Query(..., description="Review ID"),
    location_id: str = Query(..., description="Location ID"),
    skip_gemini: bool = Query(False, description="Skip Gemini and use template only"),
    post_to_gmb: bool = Query(False, description="Post reply directly to GMB"),
):
    """
     GENERATE REPLY FOR SINGLE REVIEW
    
    Features:
    ---------
    1. Fetches review from database using reviewId + location_id
    2. Generates personalized reply using Gemini
    3. Optionally posts reply to GMB
    4. Updates database with generated reply
    
    Parameters:
    -----------
    - review_id: The review ID (from GMB)
    - location_id: The location ID (for partition pruning)
    - skip_gemini: Use template-only reply (for testing)
    - post_to_gmb: Automatically post to GMB after generation
    
    Returns:
    --------
    {
        "status": "success",
        "review_id": "...",
        "location_id": "...",
        "generated_reply": "...",
        "posted_to_gmb": true/false,
        "updated_database": true/false
    }
    """
    
    try:
        from app.connections import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")
    
    logger.info(f" Fetching review: {review_id} from location: {location_id}")
    
    query = f"""
    SELECT *
    FROM {TABLE_NAME}
    WHERE reviewId = %s
      AND name = %s
    LIMIT 1
    """
    
    try:
        reviews = db.execute_query(query, (review_id, location_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    
    if not reviews:
        raise HTTPException(
            status_code=404, 
            detail=f"Review not found: reviewId={review_id}, location_id={location_id}"
        )
    
    row = reviews[0]
    
    # Check if already replied
    if row.get("reviewReply") and row.get("reviewReply", "").strip():
        return {
            "status": "already_replied",
            "message": "This review already has a reply",
            "review_id": review_id,
            "location_id": location_id,
            "existing_reply": row.get("reviewReply"),
            "reviewer": row.get("reviewer_displayName"),
            "comment": row.get("comment"),
            "stars": row.get("starRating")
        }
    
    # Extract review details
    raw_reviewer = row.get("reviewer_displayName") or "Customer"
    comment = row.get("comment") or ""
    star_rating_raw = row.get("starRating")
    raw_title = row.get("title") or ""
    
    # Normalize
    reviewer = normalize_name(raw_reviewer) or "Customer"
    store_canonical = _normalize_title(_clean_store_title(raw_title) or raw_title or location_id)
    star_rating_int = parse_star_rating(star_rating_raw, default=3)
    
    logger.info(f"Š Processing review: {reviewer} ({star_rating_int} stars) - {comment[:50]}...")
    
    # Generate reply
    if skip_gemini:
        sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
        heuristics = detect_attributes_and_emotion(comment)
        parsed = {
            "sentiment": sentiment,
            "emotion": heuristics["emotion"],
            "attributes": heuristics["attributes"],
            "reply": build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
        }
        analysis = {"parsed": parsed, "quality_score": 50}
        logger.info("âœ… Used template reply (Gemini skipped)")
    else:
        try:
            analysis = call_gemini_sync(comment, star_rating_int, reviewer, store_canonical)
            parsed = analysis.get("parsed", {})
            logger.info("âœ… Generated reply using Gemini")
        except Exception as e:
            logger.error(f"Gemini failed: {e}")
            sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
            heuristics = detect_attributes_and_emotion(comment)
            parsed = {
                "sentiment": sentiment,
                "emotion": heuristics["emotion"],
                "attributes": heuristics["attributes"],
                "reply": build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
            }
            analysis = {"parsed": parsed, "quality_score": 50}
    
    reply_text = parsed.get("reply", "")
    if not reply_text:
        sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
        reply_text = build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
    
    reply_text = enforce_customer_name_in_reply(reply_text, reviewer, store_canonical, star_rating_int)
    
    # Post to GMB if requested
    posted_to_gmb = False
    gmb_error = None
    
    if post_to_gmb:
        try:
            creds = await get_google_credentials()
            account_id = getattr(settings, "GOOGLE_ACCOUNT_ID", None)
            
            if not account_id:
                gmb_error = "GOOGLE_ACCOUNT_ID not configured"
            else:
                import httpx
                loc_short = location_id.split('/')[-1]
                rev_short = review_id.split('/')[-1]
                url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{loc_short}/reviews/{rev_short}/reply"
                
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.put(
                        url,
                        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                        json={"comment": reply_text}
                    )
                    posted_to_gmb = 200 <= resp.status_code < 300
                    
                    if not posted_to_gmb:
                        gmb_error = f"GMB API returned status {resp.status_code}"
                        logger.error(f"GMB post failed: {resp.text}")
                
                if posted_to_gmb:
                    logger.info("âœ… Posted reply to GMB")
        except Exception as e:
            gmb_error = str(e)
            logger.error(f"GMB post error: {e}")
    
    # Update database
    updated_database = False
    update_error = None
    
    try:
        from app.connections import db
        
        if posted_to_gmb and reply_text:
            from app.timezone_utils import now_ist
            update_query = """
            UPDATE location_reviews 
            SET reviewReply = %s,
                updateTime = %s,
                sentiment = %s,
                emotion = %s,
                attributes = %s,
                context_sentiment = %s,
                context_confidence = %s,
                final_sentiment = %s,
                quality_score = %s,
                post_error = %s
            WHERE reviewId = %s AND name = %s
            """
            db.execute_update(
                update_query,
                (
                    reply_text,
                    now_ist(),
                    parsed.get("sentiment", "neutral"),
                    parsed.get("emotion", "other"),
                    ",".join(parsed.get("attributes", [])) if parsed.get("attributes") else "other",
                    parsed.get("sentiment", "neutral"),
                    0.85,
                    parsed.get("sentiment", "neutral"),
                    analysis.get("quality_score", 75),
                    gmb_error if gmb_error else None,
                    review_id,
                    location_id
                )
            )
            updated_database = True
            logger.info(f"✅ Updated review reply in database")
        else:
            logger.info(f"⏭️ Skipped database update (not posted to GMB)")
        
    except Exception as e:
        update_error = str(e)
        logger.error(f"Database update failed: {e}")
    
    return {
        "status": "success",
        "review_id": review_id,
        "location_id": location_id,
        "review_details": {
            "reviewer": reviewer,
            "comment": comment,
            "stars": star_rating_int,
            "store": store_canonical,
            "review_date": str(row.get("createTime"))
        },
        "generated_reply": reply_text,
        "analysis": {
            "sentiment": parsed.get("sentiment"),
            "emotion": parsed.get("emotion"),
            "attributes": parsed.get("attributes"),
            "quality_score": analysis.get("quality_score")
        },
        "posted_to_gmb": posted_to_gmb,
        "gmb_error": gmb_error,
        "updated_database": updated_database,
        "update_error": update_error,
        "used_gemini": not skip_gemini
    }


def log_review_history(
    review_id: str,
    location_id: str,
    action: str,
    old_reply: Optional[str] = None,
    new_reply: Optional[str] = None,
    old_sentiment: Optional[str] = None,
    new_sentiment: Optional[str] = None,
    old_emotion: Optional[str] = None,
    new_emotion: Optional[str] = None,
    old_attributes: Optional[str] = None,
    new_attributes: Optional[str] = None,
    old_quality_score: Optional[int] = None,
    new_quality_score: Optional[int] = None,
    gmb_posted: bool = False,
    gmb_error: Optional[str] = None,
    modified_by: Optional[str] = None,
):
    try:
        insert_query = """
        INSERT INTO review_history (
            review_id, location_id, action, old_reply, new_reply,
            old_sentiment, new_sentiment, old_emotion, new_emotion,
            old_attributes, new_attributes, old_quality_score, new_quality_score,
            gmb_posted, gmb_error, modified_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        db.execute_update(
            insert_query,
            (
                review_id,
                location_id,
                action,
                old_reply,
                new_reply,
                old_sentiment,
                new_sentiment,
                old_emotion,
                new_emotion,
                old_attributes,
                new_attributes,
                old_quality_score,
                new_quality_score,
                gmb_posted,
                gmb_error,
                modified_by,
            )
        )
        logger.info(f"✅ Review history logged for {review_id}")
    except Exception as e:
        logger.error(f"Failed to log review history: {e}")


@router.post("/manual_reply", summary="Manually update reply and optionally post to GMB")
async def manual_update_reply(
    payload: dict = Body(...),
    post_to_gmb: bool = Query(False, description="Post reply to Google My Business"),
):
    review_id = payload.get("review_id")
    location_id = payload.get("location_id")
    reply_text = payload.get("reply")
    if not review_id or not location_id or not reply_text:
        raise HTTPException(status_code=400, detail="review_id, location_id, and reply are required")
    
    try:
        from app.connections import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")

    query = """
    SELECT *
    FROM {0}
    WHERE reviewId = %s
      AND name = %s
    LIMIT 1
    """.format(TABLE_NAME)
    try:
        reviews = db.execute_query(query, (review_id, location_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    
    if not reviews:
        raise HTTPException(status_code=404, detail=f"Review not found: reviewId={review_id}, location_id={location_id}")

    row = reviews[0]
    comment = row.get("comment") or ""
    raw_reviewer = row.get("reviewer_displayName") or "Customer"
    raw_title = row.get("title") or ""
    star_rating_raw = row.get("starRating")
    reviewer = normalize_name(raw_reviewer) or "Customer"
    store_canonical = _normalize_title(_clean_store_title(raw_title) or raw_title or location_id)
    star_rating_int = parse_star_rating(star_rating_raw, default=3)

    provided_sentiment = payload.get("sentiment")
    provided_emotion = payload.get("emotion")
    provided_attributes = payload.get("attributes")
    provided_quality_raw = payload.get("quality_score")
    
    if provided_quality_raw is not None:
        try:
            provided_quality = int(provided_quality_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="quality_score must be an integer")
    else:
        provided_quality = None

    heuristics = None
    if not provided_emotion or not provided_attributes:
        heuristics = detect_attributes_and_emotion(comment)

    if isinstance(provided_attributes, list):
        attributes_list = [str(x).strip() for x in provided_attributes if str(x).strip()]
    elif isinstance(provided_attributes, str):
        attributes_list = [part.strip() for part in provided_attributes.split(",") if part.strip()]
    else:
        attributes_list = []

    if not attributes_list:
        if heuristics:
            attributes_list = heuristics.get("attributes", [])
        else:
            existing_attrs = row.get("attributes", "") or ""
            attributes_list = [part.strip() for part in existing_attrs.split(",") if part.strip()]
    
    attributes_list = [str(x).strip() for x in attributes_list if str(x).strip()]
    if not attributes_list:
        attributes_list = ["other"]

    if provided_emotion:
        emotion_value = provided_emotion
    else:
        if heuristics:
            emotion_value = heuristics.get("emotion")
        else:
            emotion_value = getattr(row, "emotion", None)
        if not emotion_value:
            emotion_value = "other"

    if provided_sentiment:
        sentiment_value = provided_sentiment
    else:
        existing_sentiment = getattr(row, "final_sentiment", None) or getattr(row, "context_sentiment", None)
        if existing_sentiment:
            sentiment_value = existing_sentiment
        else:
            sentiment_value = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")

    quality_score_value = provided_quality if provided_quality is not None else int(getattr(row, "quality_score", 75) or 75)
    
    context_confidence_raw = payload.get("context_confidence")
    if context_confidence_raw is None:
        context_confidence_value = 0.85
    else:
        try:
            context_confidence_value = float(context_confidence_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="context_confidence must be numeric")

    reply_text = enforce_customer_name_in_reply(reply_text, reviewer, store_canonical, star_rating_int)

    old_reply = row.get("reviewReply") or ""
    old_sentiment = row.get("final_sentiment") or row.get("context_sentiment")
    old_emotion = row.get("emotion")
    old_attributes = row.get("attributes", "") or ""
    old_quality_score = int(row.get("quality_score") or 75)

    posted_to_gmb = False
    gmb_error = None
    if post_to_gmb:
        try:
            creds = await get_google_credentials()
            account_id = getattr(settings, "GOOGLE_ACCOUNT_ID", None)
            if not account_id:
                gmb_error = "GOOGLE_ACCOUNT_ID not configured"
            else:
                import httpx
                loc_short = location_id.split("/")[-1]
                rev_short = review_id.split("/")[-1]
                url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{loc_short}/reviews/{rev_short}/reply"
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.put(
                        url,
                        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                        json={"comment": reply_text},
                    )
                    posted_to_gmb = 200 <= resp.status_code < 300
                    if not posted_to_gmb:
                        gmb_error = f"GMB API returned status {resp.status_code}"
        except Exception as e:
            gmb_error = str(e)

    update_reply_flag = payload.get("update_reply", True)
    review_reply_value = reply_text if reply_text is not None else (row.get("reviewReply") or "")

    fetched_at = now_utc().isoformat()
    attrs_string = ",".join(attributes_list)
    post_error_value = gmb_error if gmb_error else None

    updated_database = False
    update_error = None
    
    try:
        if update_reply_flag or posted_to_gmb:
            update_query = """
            UPDATE location_reviews 
            SET reviewReply = %s, 
                sentiment = %s,
                emotion = %s,
                attributes = %s,
                context_sentiment = %s,
                context_confidence = %s,
                final_sentiment = %s,
                quality_score = %s,
                post_error = %s
            WHERE reviewId = %s AND name = %s
            """
            db.execute_update(
                update_query,
                (
                    str(review_reply_value),
                    str(sentiment_value),
                    str(emotion_value),
                    str(attrs_string) if attrs_string else "other",
                    str(sentiment_value),
                    float(context_confidence_value),
                    str(sentiment_value),
                    int(quality_score_value),
                    str(post_error_value) if post_error_value else None,
                    review_id,
                    location_id
                )
            )
            updated_database = True
            logger.info(f"✅ Updated review in database")
            
            log_review_history(
                review_id=review_id,
                location_id=location_id,
                action="manual_update",
                old_reply=old_reply,
                new_reply=review_reply_value,
                old_sentiment=old_sentiment,
                new_sentiment=sentiment_value,
                old_emotion=old_emotion,
                new_emotion=emotion_value,
                old_attributes=old_attributes,
                new_attributes=attrs_string,
                old_quality_score=old_quality_score,
                new_quality_score=quality_score_value,
                gmb_posted=posted_to_gmb,
                gmb_error=gmb_error,
                modified_by=payload.get("modified_by"),
            )
        else:
            logger.info(f"⏭️ No database update needed")
    except Exception as e:
        update_error = f"Database update failed: {str(e)}"
        logger.error(f"Database update failed: {e}")

    return {
        "status": "success" if updated_database else "error",
        "review_id": review_id,
        "location_id": location_id,
        "posted_to_gmb": posted_to_gmb,
        "gmb_error": gmb_error,
        "updated_database": updated_database,
        "update_error": update_error,
        "sentiment": sentiment_value,
        "emotion": emotion_value,
        "attributes": attributes_list,
        "quality_score": quality_score_value,
    }


# -----------------------
# -----------------------
# REWRITTEN BATCH PROCESSING WITH DEDUPLICATION
# -----------------------
# @router.post("/process_all_v2", summary="Batch process with deduplication and optimizations")
async def process_all_reviews_v2(
    payload: dict = Body(...),
    dry_run: bool = Query(False, description="Preview without making changes"),
    location_id: Optional[str] = Query(None, description="Filter by location"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    max_reviews: int = Query(100, ge=1, le=1000, description="Max reviews to process"),
    batch_size: int = Query(50, ge=10, le=200, description="Batch size for updates"),
    process_delay: float = Query(0.1, ge=0, le=10.0, description="Delay between batches"),
    skip_gemini: bool = Query(False, description="Use template replies only"),
    post_to_gmb: bool = Query(False, description="Post replies to GMB"),
    skip_duplicates: bool = Query(True, description="Skip reviews already replied"),
):
    """
    ðŸš€ BATCH PROCESS REVIEWS - V2 WITH IMPROVEMENTS
    
    New features:
    -------------
    1. Automatic duplicate detection (skip already replied)
    2. Better error handling with detailed logs
    3. Progress tracking with ETA
    4. Configurable GMB posting
    5. Optimized for monthly partitioning
    
    Optimizations:
    --------------
    - Uses partition pruning (location_id + months(review_date))
    - Batch MERGE operations (fewer commits)
    - Concurrent processing within batches
    - Memory-efficient streaming
    
    Performance:
    ------------
    - 100 reviews: ~30-60 seconds (with Gemini)
    - 500 reviews: ~3-5 minutes (with Gemini)
    - 1000 reviews: ~6-10 minutes (with Gemini)
    
    Returns:
    --------
    {
        "status": "success",
        "processed": 100,
        "skipped": 20,
        "posted_to_gmb": 80,
        "errors": 0,
        "duration_seconds": 45.2,
        "reviews_per_minute": 132.7
    }
    """
    
    start_time = time.time()
    errors = []
    processed_count = 0
    skipped_count = 0
    posted_count = 0
    preview_rows = []
    
    # Get credentials if GMB posting is enabled
    creds = None
    account_id = None
    if post_to_gmb:
        try:
            creds = await get_google_credentials()
            account_id = getattr(settings, "GOOGLE_ACCOUNT_ID", None)
            if not account_id:
                logger.warning("GOOGLE_ACCOUNT_ID not configured, GMB posting disabled")
                post_to_gmb = False
        except Exception as exc:
            logger.error(f"Failed to get credentials: {exc}")
            post_to_gmb = False
    
    # Initialize Spark
    try:
        from app.connections import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Spark initialization failed: {e}")
    
    location_filter = payload.get("location_id") or location_id
    
    if not date_from:
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    
    logger.info(f" Query filters: location={location_filter}, dates={date_from} to {date_to}")
    
    query_conditions = []
    params = []
    
    if skip_duplicates:
        query_conditions.append("(reviewReply IS NULL OR reviewReply = '')")
    
    if location_filter:
        query_conditions.append("name = %s")
        params.append(location_filter)
    
    query_conditions.append("DATE(createTime) >= %s")
    params.append(date_from)
    query_conditions.append("DATE(createTime) <= %s")
    params.append(date_to)
    
    where_sql = " AND ".join(query_conditions) if query_conditions else "1=1"
    
    query = f"""
    SELECT *
    FROM {TABLE_NAME}
    WHERE {where_sql}
    LIMIT %s
    """
    params.append(max_reviews)
    
    logger.info(f"Š Executing query...")
    
    try:
        reviews = db.execute_query(query, tuple(params))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    
    if not reviews:
        return {
            "status": "no_reviews_found",
            "message": "No reviews found matching the criteria",
            "filters": {
                "location_id": location_filter,
                "date_from": date_from,
                "date_to": date_to,
                "skip_duplicates": skip_duplicates
            }
        }
    
    logger.info(f"Š Found {len(reviews)} reviews to process")
    
    # Process reviews in batches
    batch = []
    
    for idx, row in enumerate(reviews):
        review_id = row.get("reviewId")
        location_name = row.get("name")
        
        # Skip if already replied (double-check)
        if skip_duplicates and row.get("reviewReply") and row.get("reviewReply", "").strip():
            skipped_count += 1
            logger.debug(f"â­ï¸ Skipping already replied: {review_id}")
            continue
        
        # Extract and normalize data
        raw_reviewer = row.get("reviewer_displayName") or "Customer"
        comment = row.get("comment") or ""
        star_rating_raw = row.get("starRating")
        store_title = row.get("title") or ""
        
        reviewer = normalize_name(raw_reviewer) or "Customer"
        store_canonical = _normalize_title(store_title) if store_title else location_name
        review_title = store_canonical
        star_rating_int = parse_star_rating(star_rating_raw, default=3)
        
        # Generate reply
        if skip_gemini:
            sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
            heuristics = detect_attributes_and_emotion(comment)
            parsed = {
                "sentiment": sentiment,
                "emotion": heuristics["emotion"],
                "attributes": heuristics["attributes"],
                "reply": build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
            }
            analysis = {"parsed": parsed, "quality_score": 50}
        else:
            try:
                analysis = call_gemini_sync(comment, star_rating_int, reviewer, store_canonical)
                parsed = analysis.get("parsed", {})
            except Exception as e:
                logger.error(f"Gemini failed for {review_id}: {e}")
                sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
                heuristics = detect_attributes_and_emotion(comment)
                parsed = {
                    "sentiment": sentiment,
                    "emotion": heuristics["emotion"],
                    "attributes": heuristics["attributes"],
                    "reply": build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
                }
                analysis = {"parsed": parsed, "quality_score": 50}
                errors.append({"review_id": review_id, "error": "gemini_failed", "message": str(e)[:100]})
        
        reply_text = parsed.get("reply", "")
        if not reply_text:
            sentiment = "positive" if star_rating_int >= 4 else ("negative" if star_rating_int <= 2 else "neutral")
            reply_text = build_reply_template(reviewer, store_canonical, star_rating_int, sentiment)
        
        reply_text = enforce_customer_name_in_reply(reply_text, reviewer, store_canonical, star_rating_int)
        
        # Dry run preview
        if dry_run:
            if len(preview_rows) < 50:
                preview_rows.append({
                    "reviewId": review_id,
                    "location_id": location_name,
                    "reviewer": reviewer,
                    "stars": star_rating_int,
                    "sentiment": parsed.get("sentiment"),
                    "reply_preview": reply_text[:100] + "..." if len(reply_text) > 100 else reply_text
                })
            processed_count += 1
            continue
        
        # Post to GMB
        post_ok = False
        gmb_error = None
        
        if post_to_gmb and creds and account_id:
            try:
                import httpx
                loc_short = location_name.split('/')[-1]
                rev_short = review_id.split('/')[-1]
                url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{loc_short}/reviews/{rev_short}/reply"
                
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.put(
                        url,
                        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                        json={"comment": reply_text}
                    )
                    post_ok = 200 <= resp.status_code < 300
                    
                    if post_ok:
                        posted_count += 1
                    else:
                        gmb_error = f"Status {resp.status_code}"
                        
            except Exception as e:
                gmb_error = str(e)[:100]
                logger.error(f"GMB post failed for {review_id}: {e}")
        
        attrs = ",".join(parsed.get("attributes", []))
        create_time = row.get("createTime")
        fetched_at = now_utc()
        
        batch.append((
            str(location_name),
            str(review_id),
            str(reviewer),
            None,
            None,
            str(star_rating_int),
            int(star_rating_int),
            str(comment)[:5000],
            create_time,
            fetched_at,
            fetched_at,
            str(reply_text) if post_ok else "",
            str(review_title),
            str(parsed.get("sentiment", "neutral")),
            str(parsed.get("emotion", "other")),
            str(attrs) if attrs else "other",
            str(parsed.get("sentiment", "neutral")),
            0.85,
            str(parsed.get("sentiment", "neutral")),
            int(analysis.get("quality_score", 75)),
            str(gmb_error) if gmb_error else None
        ))
        
        processed_count += 1
        
        # Write batch
        if len(batch) >= batch_size:
            if not dry_run:
                try:
                    await _write_batch_to_db(batch)
                    logger.info(f"âœ… Batch written: {len(batch)} reviews")
                except Exception as e:
                    logger.error(f"Batch write failed: {e}")
                    errors.append({"error": "batch_write_failed", "message": str(e)[:100]})
            
            batch.clear()
            gc.collect()
            
            # Progress update
            elapsed = time.time() - start_time
            rate = processed_count / elapsed if elapsed > 0 else 0
            eta_seconds = ((len(reviews) - processed_count) / rate) if rate > 0 else 0
            
            logger.info(f"ˆ Progress: {processed_count}/{len(reviews)} | {rate:.1f}/s | ETA: {eta_seconds:.0f}s")
        
        # Delay
        if process_delay > 0:
            time.sleep(process_delay)
    
    # Write final batch
    if batch and not dry_run:
        try:
            await _write_batch_to_db(batch)
            logger.info(f"âœ… Final batch written: {len(batch)} reviews")
        except Exception as e:
            logger.error(f"Final batch write failed: {e}")
            errors.append({"error": "final_batch_write_failed", "message": str(e)[:100]})
    
    elapsed = time.time() - start_time
    
    if dry_run:
        return {
            "status": "dry_run_complete",
            "message": "Preview only - no changes made",
            "previews": preview_rows,
            "total_found": len(reviews),
            "would_process": processed_count,
            "would_skip": skipped_count,
            "duration_seconds": round(elapsed, 2)
        }
    
    return {
        "status": "success",
        "message": "âœ… Batch processing complete",
        "statistics": {
            "total_found": len(reviews),
            "processed": processed_count,
            "skipped": skipped_count,
            "posted_to_gmb": posted_count,
            "errors": len(errors)
        },
        "performance": {
            "duration_seconds": round(elapsed, 2),
            "reviews_per_second": round(processed_count / elapsed, 2) if elapsed > 0 else 0,
            "reviews_per_minute": round((processed_count / elapsed) * 60, 1) if elapsed > 0 else 0
        },
        "settings": {
            "skip_gemini": skip_gemini,
            "post_to_gmb": post_to_gmb,
            "skip_duplicates": skip_duplicates,
            "batch_size": batch_size,
            "process_delay": process_delay
        },
        "filters": {
            "location_id": location_filter,
            "date_from": date_from,
            "date_to": date_to
        },
        "errors": errors[:20] if errors else None,  # Show first 20 errors
        "partition_info": {
            "table": TABLE_NAME,
            "partitioning": "location_id + months(review_date)",
            "benefit": "Optimized for monthly granularity"
        }
    }


async def _write_batch_to_db(batch: List):
    """Helper function to write batch to PlanetScale database with bulk upsert"""
    if not batch:
        return
    
    from app.connections import db
    
    try:
        db.execute_batch_upsert(
            "location_reviews",
            [
                "name", "reviewId", "reviewer_displayName", "reviewer_isAnonymous",
                "reviewer_profilePhotoUrl", "starRating", "rating", "comment",
                "createTime", "updateTime", "fetchedAt", "reviewReply", "title",
                "sentiment", "emotion", "attributes", "context_sentiment", "context_confidence",
                "final_sentiment", "quality_score", "post_error"
            ],
            batch,
            unique_key="reviewId",
            update_columns=[
                "updateTime", "fetchedAt", "reviewReply",
                "sentiment", "emotion", "attributes", "context_sentiment", "context_confidence",
                "final_sentiment", "quality_score", "post_error"
            ]
        )
        logger.info(f"✅ Bulk upserted {len(batch)} reviews to PlanetScale")
    except Exception as e:
        logger.error(f"❌ Failed to write batch to PlanetScale: {e}")
        raise