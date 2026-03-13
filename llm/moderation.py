BLOCKED_WORDS = [
    "stupid",
    "idiot",
    "fake review",
]

REQUIRED_NEGATIVE_ELEMENTS = [
    "sorry",
    "apolog",
]

class ModerationResult:
    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason = reason


def moderate_reply(reply: str, review_rating: int) -> ModerationResult:

    text = reply.lower()

    # 1️⃣ Block toxic words
    for word in BLOCKED_WORDS:
        if word in text:
            return ModerationResult(False, f"Toxic word detected: {word}")

    # 2️⃣ Ensure apology for negative review
    if review_rating <= 3:
        if not any(word in text for word in REQUIRED_NEGATIVE_ELEMENTS):
            return ModerationResult(False, "Missing apology")

    # 3️⃣ Length control
    if len(reply) > 500:
        return ModerationResult(False, "Reply too long")

    return ModerationResult(True)