import google.generativeai as genai
from llm.config import MODEL


def generate_response(prompt: str):

    model = genai.GenerativeModel(MODEL)

    response = model.generate_content(prompt)

    return response.text