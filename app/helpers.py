# R2/app/helpers.py
import re
import uuid
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def sanitize_view_name(name: str, prefix: str = "tmp", max_len: int = 120) -> str:
    """
    Convert arbitrary string into a Spark-safe identifier:
      - keep only letters, digits and underscore
      - convert runs of invalid chars to single underscore
      - ensure it starts with a letter by prefixing `prefix` if needed
      - truncate to max_len characters
    """
    if not name:
        return f"{prefix}_empty"

    # replace invalid chars with underscore
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name)
    # collapse repeated underscores and trim
    safe = re.sub(r"_+", "_", safe).strip("_")

    # ensure it begins with a letter
    if not re.match(r"^[A-Za-z]", safe):
        safe = f"{prefix}_{safe}"

    # enforce max length
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip("_")

    return safe or f"{prefix}_{uuid.uuid4().hex[:8]}"


def safe_temp_view_name(base: str, suffix: Optional[str] = None) -> str:
    """
    Create a readable, safe temp view name from base and optional suffix.
    """
    if suffix:
        combined = f"{base}_{suffix}"
    else:
        combined = base
    # ensure prefix starts with a letter
    return sanitize_view_name(combined, prefix="tmp")
