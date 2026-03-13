import os
import re
from app.config import settings

GENAI_API_KEY = getattr(settings, "GENAI_API_KEY", None) or os.environ.get("GENAI_API_KEY")

MODEL = getattr(settings, "POORVIKA_GEMINI_MODEL", "gemini-2.5-flash")

DEFAULT_MAX_OUTPUT_TOKENS = int(
    getattr(settings, "POORVIKA_MAX_OUTPUT_TOKENS", 1024)
)

EMAIL = getattr(settings, "POORVIKA_CONTACT_EMAIL", "response@poorvika.com")


REPLY_TONES = [
    "friendly and warm",
    "professional and courteous",
    "empathetic and caring",
    "enthusiastic and grateful",
    "sincere and understanding",
]

POSITIVE_OPENINGS = [
    "Thank you so much for your wonderful feedback!",
    "We're delighted to hear about your positive experience!",
    "Your kind words truly made our day!",
]

NEUTRAL_OPENINGS = [
    "Thank you for taking the time to share your feedback.",
    "We appreciate you sharing your experience with us.",
]

NEGATIVE_OPENINGS = [
    "We sincerely apologize for your experience.",
    "We're truly sorry to hear about the issues you faced.",
]


ATTRIBUTE_KEYWORDS = {
    "product": ["product", "device", "phone", "model", "iphone", "samsung"],
    "service": ["service", "support", "warranty", "repair"],
    "delivery": ["delivery", "shipping", "courier"],
    "pricing": ["price", "cost", "expensive", "cheap", "emi"],
    "staff": ["staff", "salesperson", "manager"],
    "store experience": ["store", "showroom", "queue"],
}


EMOTION_KEYWORDS = {
    "joy": ["happy", "excellent", "great", "good", "love"],
    "sadness": ["sad", "disappointed", "unhappy"],
    "anger": ["angry", "hate", "terrible", "worst"],
}


_COMPILED_PATTERNS = {
    "digits": re.compile(r"[0-9]"),
    "special": re.compile(r"[^\w\s\-\']"),
    "whitespace": re.compile(r"\s+"),
}