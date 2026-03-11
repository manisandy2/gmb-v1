import re

COMPILED_PATTERNS = {
    "digits": re.compile(r"[0-9]"),
    "special": re.compile(r"[^\w\s\-\']"),
    "whitespace": re.compile(r"\s+"),
    "promo": re.compile(
        r"\b(Buy\s+Latest|Buy\s+Now|Buy|Latest|Premium|Offers?|Sale|Discount)\b",
        re.IGNORECASE
    ),
    "trailing_punct": re.compile(r"[\.,;:\s]+$"),
}