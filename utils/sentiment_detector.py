from config.review_config import ATTRIBUTE_KEYWORDS, EMOTION_KEYWORDS


def detect_attributes_and_emotion(text):

    text_l = (text or "").lower()

    attributes = []

    for attr, kws in ATTRIBUTE_KEYWORDS.items():
        if any(k in text_l for k in kws):
            attributes.append(attr)

    emotion = "other"
    score = 0

    for emo, kws in EMOTION_KEYWORDS.items():
        s = sum(text_l.count(k) for k in kws)

        if s > score:
            emotion = emo
            score = s

    return {
        "attributes": attributes if attributes else ["other"],
        "emotion": emotion,
    }