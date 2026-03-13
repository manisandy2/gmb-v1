import asyncio
import time
from datetime import datetime, timedelta

from app.services.gemini_service import call_gemini, build_reply_template
from utils.text_normalizer import normalize_name, parse_star_rating
from utils.sentiment_detector import detect_attributes_and_emotion
from app.repositories.review_repository import fetch_reviews


async def process_reviews():

    query = """
    SELECT reviewId, comment, starRating, reviewer_displayName, name
    FROM location_reviews
    WHERE reviewReply IS NULL
    LIMIT 100
    """

    reviews = fetch_reviews(query, None)

    results = []

    for r in reviews:

        name = normalize_name(r["reviewer_displayName"])

        stars = parse_star_rating(r["starRating"])

        comment = r["comment"]

        store = r["name"]

        analysis = await asyncio.to_thread(
            call_gemini,
            comment,
            stars,
            name,
            store,
        )

        if not analysis:

            heuristics = detect_attributes_and_emotion(comment)

            sentiment = "positive" if stars >= 4 else "negative"

            reply = build_reply_template(name, store, stars, sentiment)

        else:

            reply = analysis["reply"]

        results.append(reply)

        await asyncio.sleep(0.1)

    return {"processed": len(results)}