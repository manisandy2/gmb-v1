from utils.sentiment_detector import detect_attributes


def fallback_response(data):

    heuristics = detect_attributes(data.get("review_text", ""))

    return {
        "sentiment": heuristics["sentiment"],
        "emotion": heuristics["emotion"],
        "attributes": heuristics["attributes"],
        "reply": "Thank you for your feedback. We appreciate your review and will continue improving our service."
    }