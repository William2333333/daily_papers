"""Shared defaults and validation for daily scheduling and output limits."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIME = "09:00"
DEFAULT_TIMEZONE = "Europe/Paris"
DEFAULT_MAX_PAPERS = 2
MAX_PAPERS = 10


def schedule(settings):
    time = settings.get("publish_time", DEFAULT_TIME)
    timezone = settings.get("timezone", DEFAULT_TIMEZONE)
    if not isinstance(time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time, re.ASCII):
        raise ValueError("Enter the time in 24-hour HH:MM format, such as 09:00.")
    if not isinstance(timezone, str) or not re.fullmatch(r"[A-Za-z0-9_+/-]+", timezone):
        raise ValueError("Enter an IANA time zone, such as Europe/Paris or Asia/Shanghai.")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown time zone. Use a name such as Europe/Paris or Asia/Shanghai.") from None
    return time, zone


def paper_limit(settings):
    value = settings.get("max_papers", DEFAULT_MAX_PAPERS)
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_PAPERS:
        return value
    raise ValueError(f"Maximum papers must be an integer from 1 to {MAX_PAPERS}.")
