import json
import random
import logging
import google.generativeai as genai
from google.generativeai import types

from config.review_config import (
    MODEL,
    GENAI_API_KEY,
    DEFAULT_MAX_OUTPUT_TOKENS,
    EMAIL,
    REPLY_TONES,
)

logger = logging.getLogger(__name__)


if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)


MODEL_INSTANCE = genai.GenerativeModel(model_name=MODEL)


def safe_parse_json(raw):

    try:
        return json.loads(raw)
    except Exception:
        return None


def build_reply_template(name, store, stars, sentiment):

    if sentiment == "positive":
        return (
            f"Thank you so much for your wonderful {stars}-star review!"
            f" We're delighted you had a great experience at {store}. "
            "We look forward to welcoming you back soon!"
        )

    if sentiment == "negative":
        return (
            "We sincerely apologize for your experience. "
            f"Please contact us at {EMAIL} so we can resolve this for you."
        )

    return (
        f"Thank you for sharing your feedback about {store}. "
        "We appreciate your comments and will continue improving."
    )


def call_gemini(review_text, star_rating, customer_name, store):

    tone = random.choice(REPLY_TONES)

    prompt = f"""
Analyze review and generate reply.

Review: {review_text}
Stars: {star_rating}
Customer: {customer_name}
Store: {store}

Tone: {tone}

Return JSON:
{{
 "sentiment": "",
 "emotion": "",
 "attributes": [],
 "reply": ""
}}
"""

    config = types.GenerationConfig(
        temperature=0.3,
        top_p=0.95,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )

    try:

        response = MODEL_INSTANCE.generate_content(
            prompt, generation_config=config
        )

        text = response.text

        parsed = safe_parse_json(text)

        if parsed:
            return parsed

    except Exception as e:

        logger.error(f"Gemini failed: {e}")

    return None

######### review analysis heuristics as fallback when Gemini fails or returns invalid response #########    
from utils.sentiment_detector import detect_attributes_and_emotion
from utils.text_normalizer import normalize_name, parse_star_rating
from config.review_config import EMAIL



def generate_review_reply(review_text, star_rating, customer_name, store_location):

    customer_name = normalize_name(customer_name) or "Customer"

    stars = parse_star_rating(star_rating)

    sentiment = "positive" if stars >= 4 else ("negative" if stars <= 2 else "neutral")

    gemini_result = call_gemini(
        review_text,
        stars,
        customer_name,
        store_location
    )

    if gemini_result:
        return gemini_result

    heuristics = detect_attributes_and_emotion(review_text)

    reply = build_reply_template(
        customer_name,
        store_location,
        stars,
        sentiment
    )

    return {
        "sentiment": sentiment,
        "emotion": heuristics["emotion"],
        "attributes": heuristics["attributes"],
        "reply": reply
    }