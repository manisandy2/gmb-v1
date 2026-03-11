import json


def parse_response(response_text: str):

    try:
        return json.loads(response_text)

    except Exception:
        return {
            "sentiment": "unknown",
            "emotion": "other",
            "attributes": [],
            "reply": ""
        }