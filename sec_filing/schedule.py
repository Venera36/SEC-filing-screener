import datetime as dt
from zoneinfo import ZoneInfo
from . import config as C


def _t(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def _days():
    return {int(x) for x in C.ACTIVE_DAYS.split(",") if x.strip()}


_hol = None


def _is_holiday(d):
    global _hol
    if not C.HONOR_HOLIDAYS:
        return False
    if _hol is None:
        import holidays
        _hol = holidays.US()          # federal holidays only (observed dates included)
    return d in _hol


def _open_day(d):
    return d.weekday() in _days() and not _is_holiday(d)


def now_local(now=None):
    tz = ZoneInfo(C.SCHEDULE_TZ)
    return (now or dt.datetime.now(tz)).astimezone(tz)


def is_active(now=None):
    n = now_local(now)
    return _open_day(n.date()) and _t(C.ACTIVE_START) <= n.time() < _t(C.ACTIVE_END)


def seconds_until_active(now=None):
    """Seconds until the next window opens (0 if active now)."""
    n = now_local(now)
    if is_active(n):
        return 0
    for add in range(0, 8):
        d = n.date() + dt.timedelta(days=add)
        if not _open_day(d):
            continue
        start = dt.datetime.combine(d, _t(C.ACTIVE_START), tzinfo=n.tzinfo)
        if start > n:
            return int((start - n).total_seconds())
    return 3600
