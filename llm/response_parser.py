from utils.json_utils import safe_parse_json


def parse_response(text: str):

    parsed = safe_parse_json(text)

    if not parsed:
        raise ValueError("Invalid JSON response")

    return parsed