from datetime import timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def display_timezone(value='local'):
    if value in ('', 'local', None):
        return None  # Resolve the OS local zone for each date, including DST.
    if value == 'UTC':
        return timezone.utc
    match = re.fullmatch(r'([+-])(\d{2}):(\d{2})', value)
    if match:
        hours, minutes = int(match[2]), int(match[3])
        if hours > 23 or minutes > 59:
            raise ValueError('invalid centre UTC offset')
        offset = timedelta(hours=hours, minutes=minutes)
        return timezone(offset if match[1] == '+' else -offset)
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f'unknown centre timezone {value!r}; install requirements (tzdata) or use UTC/+03:00/local') from exc
