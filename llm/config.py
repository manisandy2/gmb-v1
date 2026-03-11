import os
from app.config import settings
import google.generativeai as genai

GENAI_API_KEY = getattr(settings, "GENAI_API_KEY", None) or os.environ.get("GENAI_API_KEY")

MODEL = getattr(settings, "POORVIKA_GEMINI_MODEL", "gemini-2.5-flash")

MAX_OUTPUT_TOKENS = int(
    getattr(settings, "POORVIKA_MAX_OUTPUT_TOKENS", 1024)
)

EMAIL = getattr(settings, "POORVIKA_CONTACT_EMAIL", "response@poorvika.com")

if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)