from llm.gemini_client import generate_response


def safe_generate(prompt):

    try:
        return generate_response(prompt)

    except Exception:
        return None