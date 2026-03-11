from __future__ import annotations


import re
from typing import  Dict,  Optional
from datetime import datetime
from app.timezone_utils import timestamp_ist

#     SMALL UTILITIES REQUESTED
# =============================================================
def to_date(d: str) -> Dict[str, int]:
    """
    Convert 'YYYY-MM-DD' to dict {"year":..., "month":..., "day":...}
    Used to construct Google 'Date' objects in event payloads.
    """
    dt = datetime.strptime(d, "%Y-%m-%d")
    return {"year": dt.year, "month": dt.month, "day": dt.day}


def to_time(t: str) -> Dict[str, int]:
    """
    Convert 'HH:MM' to dict {"hours":..., "minutes":..., "seconds": 0, "nanos": 0}
    Used to construct Google 'TimeOfDay' objects.
    """
    tm = datetime.strptime(t, "%H:%M")
    return {"hours": tm.hour, "minutes": tm.minute, "seconds": 0, "nanos": 0}


def slugify(text: str, max_len: int = 60) -> str:
    """
    Turn text into a filesystem-safe slug (used for filenames).
    """
    text = re.sub(r"\s+", "-", (text or "").strip())
    text = re.sub(r"[^A-Za-z0-9\-_]+", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_len] or "untitled"


def stamp() -> str:
    """
    Get current timestamp in IST format (YYYY-MM-DD_HH-MM-SS).
    Used for filenames and logs.
    """
    return timestamp_ist()


# Small helper used in earlier routers
def _safe_round_conf(v: Optional[float]) -> Optional[float]:
    try:
        if v is None:
            return None
        return round(float(v), 2)
    except Exception:
        return None