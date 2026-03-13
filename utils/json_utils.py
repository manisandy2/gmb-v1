import json
import re


def safe_parse_json(text):

    try:
        text = re.sub(r"```json|```", "", text)
        return json.loads(text)
    except Exception:
        return None