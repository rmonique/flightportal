"""Tests for clock.py against Python's timezone database: python3 tools/test_clock.py"""
import os
import random
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import clock  # noqa: E402

CASES = [  # (zone, standard UTC offset hours, rule)
    ("America/New_York", -5, "US"),
    ("America/Los_Angeles", -8, "US"),
    ("Europe/London", 0, "EU"),
    ("Europe/Berlin", 1, "EU"),
    ("Asia/Kolkata", 5.5, "none"),
    ("Asia/Tokyo", 9, "none"),
]


def expected(utc, zone):
    dt = datetime.fromtimestamp(utc, timezone.utc).astimezone(ZoneInfo(zone))
    return dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.weekday()


def main():
    rng = random.Random(42)
    # Random instants 2020-2040, plus every hour around each DST transition
    instants = [rng.randint(1577836800, 2208988800) for _ in range(20000)]
    for y in range(2024, 2036):
        for m, d in ((3, 1), (10, 20), (11, 1)):
            base = clock.days_from_civil(y, m, d) * 86400
            instants += [base + h * 900 for h in range(0, 4 * 24 * 14)]

    for zone, hours, rule in CASES:
        offset = clock.parse_offset(str(hours))
        for utc in instants:
            got = clock.local_time(utc, offset, rule)
            exp = expected(utc, zone)
            assert got == exp, f"{zone} {utc}: got {got} expected {exp}"
        print(f"{zone:<20} OK ({len(instants)} instants)")

    for z in range(-1000, 30000, 7):
        assert clock.days_from_civil(*clock.civil_from_days(z)) == z

    assert clock.format_time(0, 5, False) == "12:05 AM"
    assert clock.format_time(12, 0, False) == "12:00 PM"
    assert clock.format_time(18, 52, False) == "6:52 PM"
    assert clock.format_time(18, 52, True) == "18:52"
    assert clock.format_date(11, 9, 0) == "Mon Nov 9"

    assert clock.is_night(23, 22, 7) and clock.is_night(3, 22, 7) and not clock.is_night(7, 22, 7)
    assert not clock.is_night(12, 22, 7) and clock.is_night(1, 0, 6) and not clock.is_night(6, 0, 6)
    assert not clock.is_night(3, None, 7) and not clock.is_night(3, 5, 5)
    assert clock.parse_offset("-5") == -18000 and clock.parse_offset(5.5) == 19800
    assert clock.parse_offset(None) is None and clock.parse_offset("abc") is None
    from email.utils import formatdate
    for utc in instants[:2000]:
        assert clock.parse_http_date(formatdate(utc, usegmt=True)) == utc
    assert clock.parse_http_date(None) is None and clock.parse_http_date("garbage") is None
    assert clock.parse_http_date("Sun, 09 Foo 2026 23:53:51 GMT") is None
    print("formatting / night / offset parsing: OK")
    print("ALL TESTS PASSED")


main()
