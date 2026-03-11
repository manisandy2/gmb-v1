"""
Centralized timezone utilities for Indian Standard Time (IST) handling.

This module provides standardized functions for working with IST across the application,
ensuring consistent timezone handling for all timestamps and date operations.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Indian Standard Time constant
IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """
    Get current datetime in Indian Standard Time (IST).
    
    Returns:
        datetime: Current datetime with IST timezone information
    
    Example:
        >>> ts = now_ist()
        >>> print(ts.isoformat())  # e.g., "2024-01-15T14:30:45.123456+05:30"
    """
    return datetime.now(IST)


def now_utc() -> datetime:
    """
    Get current datetime in UTC.
    
    Returns:
        datetime: Current datetime with UTC timezone information
    
    Used only for external integrations that require UTC timestamps.
    """
    return datetime.now(timezone.utc)


def today_ist() -> datetime:
    """
    Get today's date at midnight in Indian Standard Time (IST).
    
    Returns:
        datetime: Today's date (midnight) with IST timezone
    
    Example:
        >>> today = today_ist()
        >>> print(today.isoformat())  # e.g., "2024-01-15T00:00:00+05:30"
    """
    return datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)


def timestamp_ist() -> str:
    """
    Get current timestamp as a formatted string in IST.
    
    Returns:
        str: Formatted timestamp "YYYY-MM-DD_HH-MM-SS"
    
    Useful for filenames and log entries.
    
    Example:
        >>> stamp = timestamp_ist()
        >>> print(stamp)  # e.g., "2024-01-15_14-30-45"
    """
    return now_ist().strftime("%Y-%m-%d_%H-%M-%S")


def to_ist(dt: datetime) -> datetime:
    """
    Convert any datetime to Indian Standard Time (IST).
    
    Args:
        dt: datetime object (naive or timezone-aware)
    
    Returns:
        datetime: Converted datetime in IST
    
    Example:
        >>> from datetime import datetime, timezone
        >>> utc_dt = datetime.now(timezone.utc)
        >>> ist_dt = to_ist(utc_dt)
    """
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Assume naive datetimes are UTC
        dt = dt.replace(tzinfo=timezone.utc)
    
    return dt.astimezone(IST)


def to_utc(dt: datetime) -> datetime:
    """
    Convert any datetime to UTC.
    
    Args:
        dt: datetime object (naive or timezone-aware)
    
    Returns:
        datetime: Converted datetime in UTC
    
    Example:
        >>> ist_dt = datetime.now(IST)
        >>> utc_dt = to_utc(ist_dt)
    """
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Assume naive datetimes are IST
        dt = dt.replace(tzinfo=IST)
    
    return dt.astimezone(timezone.utc)