import google.generativeai as genai
import asyncio
from app.config import settings

genai.configure(api_key=settings.GENAI_API_KEY)

# model = genai.GenerativeModel(settings.POORVIKA_GEMINI_MODEL)

model = getattr(settings, "POORVIKA_GEMINI_MODEL", "gemini-2.5-flash")
async def call_gemini(prompt: str):

    response = await asyncio.to_thread(
        model.generate_content,
        prompt
    )

    return response.text