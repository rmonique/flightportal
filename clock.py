# Date/time helpers for the idle clock and night mode.
#
# Pure integer maths (no time.mktime/localtime) so it behaves the same on
# CircuitPython and on a computer, where it's tested by tools/test_clock.py.

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a calendar date."""
    if m <= 2:
        y -= 1
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    """Calendar date (y, m, d) for days since 1970-01-01."""
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    if m <= 2:
        y += 1
    return y, m, d


def weekday(days):
    """Monday=0 ... Sunday=6, for days since 1970-01-01 (a Thursday)."""
    return (days + 3) % 7


def nth_sunday(y, m, n):
    """Day of the month of the nth Sunday (n >= 1)."""
    first = weekday(days_from_civil(y, m, 1))
    return 1 + (6 - first) % 7 + 7 * (n - 1)


def last_sunday(y, m, days_in_month):
    d = nth_sunday(y, m, 1)
    while d + 7 <= days_in_month:
        d += 7
    return d


def _epoch(y, m, d, h):
    return days_from_civil(y, m, d) * 86400 + h * 3600


def is_dst(utc, offset_seconds, rule):
    """Whether daylight saving applies. rule: "US", "EU" or anything else for none."""
    if rule == "US":
        # 2am local standard time on the 2nd Sunday of March, until 2am local
        # daylight time (1am standard) on the 1st Sunday of November
        std = utc + offset_seconds
        y = civil_from_days(std // 86400)[0]
        start = _epoch(y, 3, nth_sunday(y, 3, 2), 2)
        end = _epoch(y, 11, nth_sunday(y, 11, 1), 1)
        return start <= std < end
    if rule == "EU":
        # 1am UTC on the last Sunday of March until 1am UTC on the last Sunday of October
        y = civil_from_days(utc // 86400)[0]
        start = _epoch(y, 3, last_sunday(y, 3, 31), 1)
        end = _epoch(y, 10, last_sunday(y, 10, 31), 1)
        return start <= utc < end
    return False


def local_time(utc, offset_seconds, rule):
    """(year, month, day, hour, minute, second, weekday) in local time."""
    t = utc + offset_seconds
    if is_dst(utc, offset_seconds, rule):
        t += 3600
    days = t // 86400
    secs = t - days * 86400
    y, m, d = civil_from_days(days)
    return y, m, d, secs // 3600, (secs // 60) % 60, secs % 60, weekday(days)


def format_time(hour, minute, use_24h):
    if use_24h:
        return "%d:%02d" % (hour, minute)
    return "%d:%02d %s" % (hour % 12 or 12, minute, "AM" if hour < 12 else "PM")


def format_date(month, day, wday):
    return "%s %s %d" % (DAYS[wday], MONTHS[month - 1], day)


def is_night(hour, start, end):
    """Whether hour falls in [start, end), wrapping past midnight. Off if either is None or they match."""
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def parse_http_date(value):
    """UTC epoch seconds from an HTTP Date header ("Sun, 09 Nov 2026 23:53:51 GMT"), or None."""
    try:
        parts = value.split()
        day = int(parts[1])
        month = MONTHS.index(parts[2]) + 1
        year = int(parts[3])
        h, m, s = parts[4].split(":")
        return days_from_civil(year, month, day) * 86400 + int(h) * 3600 + int(m) * 60 + int(s)
    except (AttributeError, IndexError, ValueError):
        return None


def parse_offset(value):
    """UTC offset in hours from settings ("-5", "5.5", -5) to seconds, or None."""
    if value is None or value == "":
        return None
    try:
        return int(float(value) * 3600)
    except (ValueError, TypeError):
        return None
