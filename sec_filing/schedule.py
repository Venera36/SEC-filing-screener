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


def is_active(now=None, start=None):
    """Inside the window on an open day. start overrides ACTIVE_START (the press-release scan opens earlier)."""
    n = now_local(now)
    return _open_day(n.date()) and _t(start or C.ACTIVE_START) <= n.time() < _t(C.ACTIVE_END)


def seconds_until_active(now=None, start=None):
    """Seconds until the next window opens (0 if active now)."""
    n = now_local(now)
    if is_active(n, start):
        return 0
    for add in range(0, 8):
        d = n.date() + dt.timedelta(days=add)
        if not _open_day(d):
            continue
        opens = dt.datetime.combine(d, _t(start or C.ACTIVE_START), tzinfo=n.tzinfo)
        if opens > n:
            # subtract in UTC: same-zone subtraction ignores a daylight-saving change in between
            return int((opens.astimezone(dt.timezone.utc) - n.astimezone(dt.timezone.utc)).total_seconds())
    return 3600
