import random

from app.templates.positive_config import POSITIVE_TONES, POSITIVE_OPENINGS
from app.templates.neutral_config import NEUTRAL_TONES, NEUTRAL_OPENINGS
from app.templates.negative_config import NEGATIVE_TONES, NEGATIVE_OPENINGS


def get_reply_style(rating: int):

    if rating >= 4:
        return {
            "sentiment": "positive",
            "tone": random.choice(POSITIVE_TONES),
            "opening": random.choice(POSITIVE_OPENINGS)
        }

    if rating == 3:
        return {
            "sentiment": "neutral",
            "tone": random.choice(NEUTRAL_TONES),
            "opening": random.choice(NEUTRAL_OPENINGS)
        }

    return {
        "sentiment": "negative",
        "tone": random.choice(NEGATIVE_TONES),
        "opening": random.choice(NEGATIVE_OPENINGS)
    }