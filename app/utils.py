from datetime import datetime, timezone, timedelta
import time


def get_local_tz():
    try:
        offset = time.timezone if time.daylight == 0 else time.altzone
        return timezone(timedelta(seconds=-offset))
    except Exception:
        return timezone(timedelta(hours=8))


LOCAL_TZ = get_local_tz()
UTC_TZ = timezone.utc


def now_utc():
    return datetime.now(UTC_TZ)


def now_local():
    return datetime.now(LOCAL_TZ)


def utc_to_local(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(LOCAL_TZ)


def local_now_str():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def local_date_str():
    return now_local().strftime("%Y-%m-%d")


def local_hour():
    return now_local().hour
