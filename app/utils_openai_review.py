# from __future__ import annotations

# import json
# import logging
# import random
# import re
# import time
# from datetime import datetime
# from typing import Any, Dict, List, Optional, Tuple

# import httpx
# from fastapi import APIRouter, Body, HTTPException, Query
# from fastapi.responses import JSONResponse
# from pyspark.sql import Row
# from pyspark.sql.functions import col

# # project-specific imports; must exist in your project
# from app.connections import spark
# from app.auth import get_google_credentials
# from app.config import settings
# from app.timezone_utils import now_utc


# logger = logging.getLogger(__name__)
# router = APIRouter()
# OAUTHLIB_INSECURE_TRANSPORT=1
# # ----------------------------
# # Minimal OpenAI wrapper + heuristics (PROMPTS UNCHANGED)
# # ----------------------------
# try:
#     from openai import OpenAI, APIError, RateLimitError, APIConnectionError  # type: ignore
#     try:
#         from openai import APITimeoutError  # type: ignore
#     except Exception:
#         APITimeoutError = Exception  # type: ignore
#     OPENAI_AVAILABLE = True
# except Exception:
#     OPENAI_AVAILABLE = False

# MODEL = getattr(settings, "POORVIKA_MODEL", "gpt-4o-mini")
# TIMEOUT_SECS = float(getattr(settings, "POORVIKA_TIMEOUT", 60))
# MAX_RETRIES = int(getattr(settings, "POORVIKA_MAX_RETRIES", 8))
# OPENAI_API_KEY = getattr(settings, "OPENAI_API_KEY", "") or ""
# USE_OPENAI = bool(OPENAI_API_KEY and OPENAI_AVAILABLE)

# REPLY_HISTORY: List[str] = []
# MAX_HISTORY = int(getattr(settings, "POORVIKA_MAX_HISTORY", 50))
# FEEDBACK_LINK = getattr(settings, "POORVIKA_FEEDBACK_LINK", "poorvika.me/feedback")
# EMAIL = getattr(settings, "POORVIKA_CONTACT_EMAIL", "response@poorvika.com")

# _client: Optional["OpenAI"] = None
# if USE_OPENAI:
#     try:
#         _client = OpenAI(api_key=OPENAI_API_KEY)
#     except Exception:
#         _client = None
#         USE_OPENAI = False


# def _should_retry(exc: Exception) -> bool:
#     if not USE_OPENAI:
#         return False
#     return isinstance(exc, (APIConnectionError, RateLimitError, APITimeoutError, APIError))


# def _retry(call_fn):
#     last_err = None
#     for attempt in range(1, MAX_RETRIES + 1):
#         try:
#             return call_fn()
#         except Exception as e:
#             last_err = e
#             if not _should_retry(e) or attempt == MAX_RETRIES:
#                 break
#             time.sleep(random.uniform(0.0, min(5.0, 2 ** attempt)))
#     if last_err:
#         raise last_err
#     raise RuntimeError("Unknown error in _retry")


# def _chat_call(messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
#     if USE_OPENAI and _client is not None:
#         _model = model or MODEL

#         def _do():
#             return _client.chat.completions.create(
#                 model=_model,
#                 messages=messages,
#                 temperature=0,
#                 timeout=TIMEOUT_SECS,
#             )

#         resp = _retry(_do)
#         try:
#             return (resp.choices[0].message.content or "").strip()
#         except Exception:
#             return str(resp).strip()

#     # fallback heuristics (keeps app runnable without OpenAI key)
#     joined = " ".join(m.get("content", "") for m in messages[-2:])
#     lower = joined.lower()

#     if "classify this review as positive" in lower or "classify this review as positive, neutral, or negative" in lower:
#         return json.dumps(
#             {"label": "Negative" if any(w in lower for w in ["bad", "terrible", "not good", "worst", "hate", "disappointed"]) else "Positive", "confidence": 0.65}
#         )
#     if "classify the dominant emotion" in lower:
#         if any(w in lower for w in ["angry", "frustrat", "rude", "disappointed"]):
#             return json.dumps({"emotion": "Anger"})
#         if any(w in lower for w in ["happy", "excellent", "good", "great", "satisfied"]):
#             return json.dumps({"emotion": "Joy"})
#         return json.dumps({"emotion": "Neutral"})
#     if "classify this review into categories" in lower:
#         cats = []
#         if any(x in lower for x in ["charger", "mobile", "phone", "screen", "battery", "product"]):
#             cats.append("Product")
#         if any(x in lower for x in ["staff", "billing", "counter", "rude", "promoter", "helped"]):
#             cats.append("Staff / Promoter")
#         if any(x in lower for x in ["discount", "offer", "gift", "sale"]):
#             cats.append("Discount / Offer / Gift")
#         return json.dumps(cats)
#     return "Thanks for your review. We appreciate your feedback."


# # ----------------------------
# # small helpers and classifiers (KEEP PROMPTS/LOGIC)
# # ----------------------------
# def _normalize_title(title: str) -> str:
#     if not isinstance(title, str):
#         return "Poorvika"
#     head = title.split(".")[0].strip()
#     return head if head else title.strip()


# def _cap_90_words(text: str) -> str:
#     words = (text or "").split()
#     if len(words) <= 90:
#         return text.strip()
#     clipped = " ".join(words[:90]).strip()
#     last_dot = clipped.rfind(".")
#     if last_dot >= 60:
#         return clipped[: last_dot + 1].strip()
#     return clipped + "…"


# def classify_sentiment(text: str) -> Tuple[str, float]:
#     if not text or not text.strip():
#         return "Neutral", 0.5
#     raw = _chat_call([
#         {"role": "system", "content": "You are a precise sentiment classifier."},
#         {"role": "user", "content": (
#             "Classify this review as Positive, Neutral, or Negative. "
#             "Return JSON with keys: label, confidence.\n\n"
#             f"{text}"
#         )}
#     ]).strip()
#     try:
#         j = json.loads(raw)
#         l = str(j.get("label", "Neutral")).capitalize()
#         c = float(j.get("confidence", 0.5))
#         return (l if l in ("Positive", "Neutral", "Negative") else "Neutral", max(0.0, min(1.0, c)))
#     except Exception:
#         lowered = text.lower()
#         if any(w in lowered for w in ["not", "bad", "worst", "hate", "angry", "disappointed", "rude"]):
#             return "Negative", 0.6
#         if any(w in lowered for w in ["good", "great", "love", "excellent", "awesome", "satisfied", "happy"]):
#             return "Positive", 0.75
#         return "Neutral", 0.5


# def classify_emotion(text: str) -> str:
#     if not text or not text.strip():
#         return "Neutral"
#     raw = _chat_call([
#         {"role": "system", "content": "You are an emotion classifier."},
#         {"role": "user", "content": (
#             "Classify the dominant emotion in this review. "
#             "Choose one of: Joy, Anger, Sadness, Surprise, Fear, Disgust, Neutral. "
#             "Return ONLY a JSON object {\"emotion\": \"...\"}.\n\n"
#             f"Review: {text}"
#         )}
#     ]).strip()
#     try:
#         j = json.loads(raw)
#         emo = str(j.get("emotion", "Neutral")).capitalize()
#         return emo if emo in ("Joy", "Anger", "Sadness", "Surprise", "Fear", "Disgust", "Neutral") else "Neutral"
#     except Exception:
#         lowered = text.lower()
#         if any(w in lowered for w in ["angry", "rude", "frustrat", "disappointed"]):
#             return "Anger"
#         if any(w in lowered for w in ["happy", "great", "satisfied", "love", "excellent"]):
#             return "Joy"
#         return "Neutral"


# def extract_attributes(text: str, star_rating: Optional[str] = None) -> str:
#     if not text or not text.strip():
#         return ""
#     prompt = [
#         {"role": "system", "content": (
#             "You are an AI assistant for Poorvika, a large electronics and appliances retailer with 450+ stores in India. "
#             "Your job is to classify customer reviews into one or more categories. "
#             "Categories represent common themes in retail reviews.\n\n"

#             "Categories:\n"
#             "1. Product\n"
#             "2. Showroom (store experience, ambience, cleanliness, layout, billing counter)\n"
#             "3. Staff / Promoter (behaviour, support, arrogance, friendliness, knowledge)\n"
#             "4. Discount / Offer / Gift (offers, free gifts, festival sales, promotions)\n"
#             "5. Finance / EMI (loan, EMI, credit card, installment)\n"
#             "6. Customer Delight (overall satisfaction, happiness, appreciation, loyalty)\n"
#             "7. Price Variation (high price, low price, price difference)\n"
#             "8. Customer Suggestion (improvements, requests, suggestions)\n"
#             "9. Accessories (mobile cases, chargers, headphones, smartwatches)\n"
#             "10. TV Accessories (remotes, wall mounts, speakers)\n"
#             "11. Kitchen Appliances (mixers, grinders, ovens, refrigerators)\n"
#             "12. Home Appliances (washing machines, AC, fans, vacuum cleaners)\n\n"

#             "Instructions:\n"
#             "- Always return JSON array of category names (e.g. [\"Staff / Promoter\", \"Showroom\"]).\n"
#             "- If multiple categories are relevant, include all of them.\n"
#             "- If none match, return [].\n\n"
#         )},
#         {"role": "user", "content": f"Classify this review into categories:\n\n{text}"}
#     ]
#     response = _chat_call(prompt).strip()
#     try:
#         parsed = json.loads(response)
#         if isinstance(parsed, list):
#             return ",".join(parsed)
#         return ""
#     except Exception:
#         lowered = text.lower()
#         cats = set()
#         if any(x in lowered for x in ["charger", "battery", "mobile", "screen", "product", "phone"]):
#             cats.add("Product")
#         if any(x in lowered for x in ["staff", "billing", "counter", "manager", "promoter", "service"]):
#             cats.add("Staff / Promoter")
#         if any(x in lowered for x in ["discount", "offer", "sale", "gift", "coupon"]):
#             cats.add("Discount / Offer / Gift")
#         if any(x in lowered for x in ["happy", "delighted", "thanks", "good experience", "great", "awesome"]):
#             cats.add("Customer Delight")
#         if any(x in lowered for x in ["emi", "loan", "finance", "installment", "credit card"]):
#             cats.add("Finance / EMI")
#         if any(x in lowered for x in ["kitchen", "mixer", "grinder", "oven", "refrigerator"]):
#             cats.add("Kitchen Appliances")
#         if not cats and star_rating:
#             sr = str(star_rating).strip().upper()
#             if sr in ("ONE", "1", "TWO", "2"):
#                 cats.add("Staff / Promoter")
#         return ",".join(sorted(cats))


# # reply generator preserved (prompts kept exactly)
# def _format_showroom_reply(reviewer: str, body: str, full_title: str) -> str:
#     title_short = _normalize_title(full_title)
#     title_short_clean = re.sub(r"(?i)\b(showroom|store|shop|outlet|branch|buy|purchase|buy latest|buy now)\b", "", title_short)
#     title_short_clean = re.sub(r"\s{2,}", " ", title_short_clean).strip()
#     if not title_short_clean:
#         title_short_clean = "our store"
#     reply = f"\n\n{body.strip()}\n\nBest wishes,\n{title_short_clean}"
#     return _cap_90_words(reply)


# def generate_reply(
#     reviewer: str,
#     stars: str,
#     sentiment: Any,
#     review_text: str,
#     full_title: str,
#     attributes: Optional[str] = None,
# ) -> str:
#     # Implementation preserved from your utils file (prompts & heuristics unchanged)
#     try:
#         sentiment_str = sentiment.value if hasattr(sentiment, "value") else str(sentiment or "")
#     except Exception:
#         sentiment_str = str(sentiment or "")
#     s_norm = sentiment_str.strip().capitalize() if sentiment_str else "Neutral"
#     if s_norm not in ("Positive", "Neutral", "Negative"):
#         s_norm = "Neutral"

#     attr_list: List[str] = []
#     if attributes:
#         attr_list = [a.strip().lower() for a in str(attributes).split(",") if a.strip()]

#     title_short = _normalize_title(full_title)
#     title_short_clean = re.sub(r"(?i)\b(showroom|store|shop|outlet|branch|buy|purchase|buy latest|buy now)\b", "", title_short)
#     title_short_clean = re.sub(r"\s{2,}", " ", title_short_clean).strip()
#     if not title_short_clean:
#         title_short_clean = "our store"

#     style = random.choice([
#         "friendly and cheerful",
#         "professional and concise",
#         "warm and caring",
#         "polite and formal",
#         "casual and approachable"
#     ])
#     synonyms = ["delighted", "pleased", "excited", "glad", "happy", "overjoyed", "grateful", "thankful"]
#     openers = [
#         f"Thank you for your {stars}-star review!",
#         "We truly appreciate your feedback.",
#         "Your kind words made our day!",
#         "Thanks so much for sharing your experience."
#     ]

#     recent_history = ""
#     try:
#         recent_history = "\n".join(REPLY_HISTORY[-MAX_HISTORY:]) if isinstance(REPLY_HISTORY, list) else ""
#     except Exception:
#         recent_history = ""

#     attr_section = f"Attributes: {', '.join(attr_list) if attr_list else 'None'}\n"
#     prompt = (
#         f"Reviewer: {reviewer}\n"
#         f"Stars: {stars}\n"
#         f"Sentiment: {s_norm}\n"
#         f"{attr_section}"
#         f"Emotion: {classify_emotion(review_text)}\n"
#         f"Review: {review_text}\n\n"
#         f"Task: Write a short, warm, showroom-style reply (≤90 words) for Poorvika ({title_short_clean}).\n"
#         f"Guidelines:\n"
#         f"- Salutation: start with 'Dear <Reviewer>,' on its own line.\n"
#         f"- Body: 1-3 sentences, be personal and reference relevant attributes when helpful.\n"
#         f"- Sign-off: end with double-newline and 'Best wishes,\\n<Full showroom title>'.\n"
#         f"- Tone rules: Positive -> gratitude; Neutral -> acknowledge; Negative -> apologise and offer contact/feedback link.\n"
#         f"- Style: {style}\n"
#         f"- Avoid repeating recent replies:\n{recent_history}\n"
#     )

#     raw = None
#     try:
#         raw = _chat_call([
#             {"role": "system", "content": "You are a customer care rep for Poorvika. Keep replies ≤90 words and follow the formatting rules."},
#             {"role": "user", "content": prompt},
#         ])
#         if isinstance(raw, dict) and "content" in raw:
#             raw = str(raw["content"])
#     except Exception:
#         raw = None

#     def _build_attr_phrase(local_sentiment: str, attrs: List[str]) -> str:
#         if not attrs:
#             return ""
#         if any("product" in a for a in attrs):
#             return "We’re glad the product met your expectations." if local_sentiment == "Positive" else "We’re sorry the product didn’t meet expectations; we'll investigate."
#         if any("staff" in a or "promoter" in a for a in attrs):
#             return "Our team will be notified to keep delivering great service." if local_sentiment == "Positive" else "We sincerely apologise for the staff experience; we'll address this internally."
#         if any("discount" in a or "offer" in a for a in attrs):
#             return "Glad you found the offer useful." if local_sentiment == "Positive" else "Sorry about any confusion with offers; please reach out."
#         return f"We note your comment about {', '.join(attrs)}."

#     if not raw:
#         attr_phrase = _build_attr_phrase(s_norm, attr_list)
#         if s_norm == "Positive":
#             word = random.choice(synonyms)
#             body = (
#                 f"{random.choice(openers)} {attr_phrase} "
#                 f"We're {word} that our team could help you."
#             ).strip()
#         elif s_norm == "Negative":
#             body = (
#                 f"We're very sorry to hear about your experience. {attr_phrase} "
#                 f"We apologise and want to make this right—please contact us at {EMAIL} or share more at {FEEDBACK_LINK}."
#             ).strip()
#         else:
#             body = (
#                 f"{random.choice(openers)} {attr_phrase} "
#                 "Thank you for your feedback — we'll use this to improve our service."
#             ).strip()
#         reply = _format_showroom_reply(reviewer or "Customer", body, title_short_clean or "")
#     else:
#         candidate = str(raw).strip()
#         candidate = re.sub(r"(?mi)\n*best (?:wishes|regards|thanks|regard)[\s\S]*$", "", candidate).strip()
#         if s_norm == "Negative" and FEEDBACK_LINK not in candidate and EMAIL not in candidate:
#             if not candidate.endswith((".", "!", "…")):
#                 candidate = candidate + "."
#             candidate = candidate + f" Please share more at {FEEDBACK_LINK}"
#         reply = _format_showroom_reply(reviewer or "Customer", candidate, full_title or "")

#     reply = _cap_90_words(reply)
#     try:
#         if isinstance(REPLY_HISTORY, list):
#             REPLY_HISTORY.append(reply)
#             if len(REPLY_HISTORY) > MAX_HISTORY:
#                 del REPLY_HISTORY[0: len(REPLY_HISTORY) - MAX_HISTORY]
#     except Exception:
#         pass

#     return reply


# # -------------------------
# # Post reply to Google Business (v4)
# # -------------------------
# async def _post_reply_to_gmb(account_id: str, location_id: str, review_id: str, comment: str, token: str) -> Dict[str, Any]:
#     loc_short = location_id.split("/")[-1]
#     url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{loc_short}/reviews/{review_id}/reply"
#     headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
#     payload = {"comment": comment}
#     async with httpx.AsyncClient(timeout=20.0) as client:
#         resp = await client.post(url, headers=headers, json=payload)
#         resp.raise_for_status()
#         return resp.json()


# # -------------------------
# # Helper: ensure table exists including sentiment/attributes columns
# # -------------------------
# def _ensure_no_reply_table():
#     ns = settings.ICEBERG_NAMESPACE
#     spark.sql(
#         f"""
#         CREATE TABLE IF NOT EXISTS {ns}.no_reply_reviews (
#             location_id STRING,
#             storeCode STRING,
#             title STRING,
#             reviewId STRING,
#             reviewer STRING,
#             comment STRING,
#             starRating STRING,
#             createTime TIMESTAMP,
#             reviewReply STRING,
#             fetchedAt TIMESTAMP,
#             `Context Sentiment` STRING,
#             `Context Confidence` DOUBLE,
#             `Final Sentiment` STRING,
#             `Attributes` STRING,
#             `Emotion` STRING,
#             post_error STRING
#         ) USING iceberg
#         """
#     )


# # -------------------------
# # Small normalization helper for Spark Row creation
# # -------------------------
# def _normalize_row_for_spark(rec: Dict[str, Any]) -> Dict[str, Any]:
#     out = dict(rec)
#     for dt_field in ("createTime", "fetchedAt"):
#         v = out.get(dt_field)
#         if isinstance(v, str):
#             try:
#                 out[dt_field] = datetime.fromisoformat(v.replace("Z", "+00:00"))
#             except Exception:
#                 try:
#                     out[dt_field] = datetime.strptime(v.split(".")[0], "%Y-%m-%dT%H:%M:%S")
#                 except Exception:
#                     out[dt_field] = now_utc()
#         elif v is None:
#             out[dt_field] = now_utc()
#     cc = out.get("Context Confidence")
#     if cc is not None:
#         try:
#             out["Context Confidence"] = float(cc)
#         except Exception:
#             out["Context Confidence"] = None
#     return out


# # -------------------------
# # Endpoint: process all unreplied reviews with concurrency and dry_run support
# # -------------------------
# @router.post("/process_all")
# async def process_all_reviews(
#     payload: Dict[str, Any] = Body(...),
#     dry_run: bool = Query(False, description="If true, generate replies but do not post or persist"),
#     concurrency: int = Query(5, ge=1, le=200, description="Number of concurrent workers"),
# ):
#     """
#     Process reviews from my_catalog.{ICEBERG_NAMESPACE}.no_reply_reviews
#     where reviewReply is NULL, empty string, or ".".

#     Payload:
#       { "location_id": "<optional location short id or full name>" }

#     - dry_run=True: compute replies & analytics, return previews, DO NOT post or persist.
#     - dry_run=False: post replies to GMB and persist updated rows (MERGE on reviewId).
#     - concurrency: number of concurrent workers to process reviews.
#     """
#     errors: List[str] = []
#     google_responses: Dict[str, Any] = {}
#     processed_rows_for_persist: List[Dict[str, Any]] = []
#     preview_rows: List[Dict[str, Any]] = []

#     location_id = payload.get("location_id")

#     # Ensure credentials present and refreshed (even for dry_run we will not use token but keep behavior)
#     try:
#         creds = await get_google_credentials()
#     except Exception as exc:
#         logger.exception("Google credentials unavailable: %s", exc)
#         raise HTTPException(status_code=401, detail="Google credentials unavailable; authenticate first")

#     account_id = getattr(settings, "GOOGLE_ACCOUNT_ID", None)
#     if not account_id:
#         raise HTTPException(status_code=400, detail="GOOGLE_ACCOUNT_ID is not configured")

#     # Ensure table exists (create-if-not-exists)
#     try:
#         _ensure_no_reply_table()
#     except Exception as exc:
#         logger.exception("Failed to ensure no_reply_reviews table exists: %s", exc)
#         raise HTTPException(status_code=500, detail="Failed to ensure no_reply_reviews table exists")

#     table = f"my_catalog.{settings.ICEBERG_NAMESPACE}.no_reply_reviews"

#     try:
#         df = spark.table(table)
#     except Exception as exc:
#         logger.exception("Failed to read no_reply_reviews: %s", exc)
#         raise HTTPException(status_code=500, detail="Failed to read no_reply_reviews table")

#     if location_id:
#         short = location_id.split("/")[-1]
#         df = df.filter(col("location_id").like(f"%{short}%"))

#     df = df.filter((col("reviewReply").isNull()) | (col("reviewReply") == "") | (col("reviewReply") == "."))

#     try:
#         rows = df.collect()
#     except Exception as exc:
#         logger.exception("Failed to collect rows to process: %s", exc)
#         raise HTTPException(status_code=500, detail="Failed to collect reviews for processing")

#     if not rows:
#         return JSONResponse({"message": "No unreplied reviews found", "processed_count": 0, "processed": [], "google_responses": {}, "error_log": None})

#     sem = asyncio.Semaphore(concurrency)  # type: ignore[name-defined]
#     import asyncio

#     async def worker(row_obj):
#         nonlocal errors, google_responses
#         rec = row_obj.asDict()
#         review_id = rec.get("reviewId")
#         if not review_id:
#             errors.append("Skipping row with missing reviewId")
#             return None

#         location_name = rec.get("location_id")
#         reviewer = rec.get("reviewer") or "Customer"
#         comment = rec.get("comment") or ""
#         star_rating = rec.get("starRating") or ""
#         title = rec.get("title") or ""

#         # run classifiers (synchronously via _chat_call or fallback)
#         try:
#             s_label, s_conf = classify_sentiment(comment)
#         except Exception as exc:
#             logger.exception("classify_sentiment failed for %s: %s", review_id, exc)
#             s_label, s_conf = "Neutral", 0.5
#             errors.append(f"classify_sentiment failed for {review_id}: {exc}")

#         try:
#             attrs = extract_attributes(comment, star_rating)
#         except Exception as exc:
#             logger.exception("extract_attributes failed for %s: %s", review_id, exc)
#             attrs = ""
#             errors.append(f"extract_attributes failed for {review_id}: {exc}")

#         try:
#             emotion = classify_emotion(comment)
#         except Exception as exc:
#             logger.exception("classify_emotion failed for %s: %s", review_id, exc)
#             emotion = "Neutral"
#             errors.append(f"classify_emotion failed for {review_id}: {exc}")

#         try:
#             reply_text = generate_reply(reviewer, star_rating, s_label, comment, title, attributes=attrs)
#         except Exception as exc:
#             logger.exception("generate_reply failed for %s: %s", review_id, exc)
#             reply_text = f"Hi {reviewer}, thank you for your feedback."
#             errors.append(f"generate_reply failed for {review_id}: {exc}")

#         # If dry_run: don't post or persist. Return preview
#         if dry_run:
#             preview = {
#                 "location_id": location_name,
#                 "reviewId": review_id,
#                 "reviewer": reviewer,
#                 "comment": comment,
#                 "starRating": star_rating,
#                 "generated_reply": reply_text,
#                 "Context Sentiment": s_label,
#                 "Context Confidence": float(round(float(s_conf), 4)) if isinstance(s_conf, (int, float)) else None,
#                 "Final Sentiment": s_label,
#                 "Attributes": attrs,
#                 "Emotion": emotion,
#             }
#             preview_rows.append(preview)
#             return None

#         # not dry_run: post to GMB
#         post_ok = False
#         post_err = None
#         try:
#             async with sem:
#                 g_resp = await _post_reply_to_gmb(account_id, location_name, review_id, reply_text, creds.token)
#             google_responses[str(review_id)] = g_resp
#             post_ok = True
#         except httpx.HTTPStatusError as he:
#             logger.exception("GMB post HTTP error for %s: %s", review_id, he)
#             post_err = f"HTTP {he.response.status_code}: {he.response.text}"
#             errors.append(f"GMB post HTTP error for {review_id}: {he.response.status_code} {he.response.text}")
#         except Exception as exc:
#             logger.exception("GMB post failed for %s: %s", review_id, exc)
#             post_err = str(exc)
#             errors.append(f"GMB post failed for {review_id}: {exc}")

#         updated_row: Dict[str, Any] = {
#             "location_id": location_name,
#             "storeCode": rec.get("storeCode") or "",
#             "title": title,
#             "reviewId": review_id,
#             "reviewer": reviewer,
#             "comment": comment,
#             "starRating": star_rating,
#             "createTime": rec.get("createTime") or now_utc(),
#             "fetchedAt": now_utc(),
#         }

#         if post_ok:
#             updated_row["reviewReply"] = reply_text
#             updated_row["Context Sentiment"] = s_label
#             updated_row["Context Confidence"] = float(round(float(s_conf), 4)) if isinstance(s_conf, (int, float)) else None
#             updated_row["Final Sentiment"] = s_label
#             updated_row["Attributes"] = attrs
#             updated_row["Emotion"] = emotion
#             updated_row["post_error"] = None
#         else:
#             updated_row["reviewReply"] = rec.get("reviewReply") or "."
#             updated_row["Context Sentiment"] = s_label
#             updated_row["Context Confidence"] = float(round(float(s_conf), 4)) if isinstance(s_conf, (int, float)) else None
#             updated_row["Final Sentiment"] = s_label
#             updated_row["Attributes"] = attrs
#             updated_row["Emotion"] = emotion
#             updated_row["post_error"] = post_err or "unknown"

#         processed_rows_for_persist.append(updated_row)
#         return None

#     # schedule workers with bounded concurrency (we use a small wrapper to ensure semaphore used for posting only)
#     import asyncio

#     # We'll create tasks but the posting itself uses the semaphore so workers can run classifiers concurrently.
#     tasks = [worker(r) for r in rows]
#     await asyncio.gather(*tasks)

#     # If dry_run: return previews and do not persist anything
#     if dry_run:
#         return JSONResponse({
#             "message": "Dry run complete — generated replies (not posted or persisted)",
#             "preview_count": len(preview_rows),
#             "previews": preview_rows,
#             "error_log": errors or None
#         })

#     # persist processed_rows_for_persist in a MERGE
#     if processed_rows_for_persist:
#         try:
#             rows_to_write = [Row(**_normalize_row_for_spark(x)) for x in processed_rows_for_persist]
#             df_write = spark.createDataFrame(rows_to_write)
#             df_write.createOrReplaceTempView("processed_no_reply")
#             merge_sql = f"""
#             MERGE INTO my_catalog.{settings.ICEBERG_NAMESPACE}.no_reply_reviews AS target
#             USING processed_no_reply AS source
#             ON (target.reviewId = source.reviewId)
#             WHEN MATCHED THEN UPDATE SET *
#             WHEN NOT MATCHED THEN INSERT *
#             """
#             spark.sql(merge_sql)
#         except Exception as exc:
#             logger.exception("Failed to MERGE processed rows: %s", exc)
#             errors.append(f"Failed to persist processed rows: {exc}")

#     return JSONResponse({
#         "message": "Processing complete",
#         "processed_count": len(processed_rows_for_persist),
#         "processed": processed_rows_for_persist,
#         "google_responses": google_responses,
#         "error_log": errors or None
#     })



