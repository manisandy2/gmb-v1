from functools import lru_cache
from config.review_config import _COMPILED_PATTERNS


@lru_cache(maxsize=10000)
def normalize_name(name):

    if not name:
        return None

    n = name.strip()

    n = _COMPILED_PATTERNS["digits"].sub("", n)
    n = _COMPILED_PATTERNS["special"].sub("", n)
    n = _COMPILED_PATTERNS["whitespace"].sub(" ", n)

    return n.title()


def parse_star_rating(raw, default=3):

    try:
        return max(1, min(5, int(round(float(raw)))))
    except Exception:
        return default


def normalize_store_title(store):

    if not store:
        return "Poorvika"

    return store.strip()