"""Production calendar of the Russian Federation, 2024-2027 (D4).

Source: Government of the Russian Federation decrees on the transfer of days off for 2024, 2025,
2026 and 2027 (Labour Code art. 112). The decree numbers are intentionally not quoted: there was
no network access to check them against the primary source. The table is therefore verified in
two ways instead:

* 2024-10-01 .. 2026-09-30 against the bank archive (dataset/all_operations.csv): there are no
  operations on any non-working weekday of the table and the working Saturdays 2024-11-02,
  2024-12-28, 2025-11-01 carry operations (see experiments/results/v3/mechanisms/calendar_check.json);
* 2024-01-01 .. 2024-09-30 and the whole of 2027 are NOT covered by the archive. 2027 is
  provisional and must be re-checked against the published decree before production use.

Outside 2024-2027 the calendar falls back to "Monday-Friday are working days" and
`is_covered(d)` is False.

All functions work on `datetime.date`.
"""

from datetime import date, timedelta
from typing import Literal

ShiftRule = Literal["next", "prev", "none"]


def _days(year: int, *spans: tuple[int, int, int, int]) -> set[date]:
    """Inclusive (month, day, month, day) spans -> set of dates."""
    out = set()
    for m1, d1, m2, d2 in spans:
        cur, end = date(year, m1, d1), date(year, m2, d2)
        while cur <= end:
            out.add(cur)
            cur += timedelta(days=1)
    return out


# Non-working days (holidays and transferred days off), only dates that fall on Mon-Fri matter,
# weekends are non-working anyway. Weekend dates are listed where they are part of the official
# block of days off, for readability.
HOLIDAYS: frozenset[date] = frozenset(
    # 2024: Jan 1-8; Feb 23; Mar 8; Apr 29-30; May 1, 9, 10; Jun 12; Nov 4; Dec 30-31.
    _days(
        2024,
        (1, 1, 1, 8),
        (2, 23, 2, 23),
        (3, 8, 3, 8),
        (4, 29, 4, 30),
        (5, 1, 5, 1),
        (5, 9, 5, 10),
        (6, 12, 6, 12),
        (11, 4, 11, 4),
        (12, 30, 12, 31),
    )
    # 2025: Jan 1-8; May 1-4, 8-11; Jun 12-15; Nov 3-4; Dec 31.
    | _days(
        2025,
        (1, 1, 1, 8),
        (5, 1, 5, 4),
        (5, 8, 5, 11),
        (6, 12, 6, 15),
        (11, 3, 11, 4),
        (12, 31, 12, 31),
    )
    # 2026: Jan 1-11 (Jan 9 is moved from Sat Jan 3); Feb 23; Mar 9 (Mar 8 is Sunday);
    # May 1; May 11 (May 9 is Saturday); Jun 12; Nov 4; Dec 31 (moved from Sun Jan 4).
    | _days(
        2026,
        (1, 1, 1, 11),
        (2, 23, 2, 23),
        (3, 9, 3, 9),
        (5, 1, 5, 1),
        (5, 11, 5, 11),
        (6, 12, 6, 12),
        (11, 4, 11, 4),
        (12, 31, 12, 31),
    )
    # 2027 (PROVISIONAL, not checked against the archive): Jan 1-8; Feb 22-23 (Feb 22 is moved
    # from Sat Jan 2); Mar 8; May 3 and May 10 (May 1 and 9 fall on the weekend); Nov 4;
    # Dec 31 (moved from Sun Jan 3). Jun 12 falls on Saturday; a transfer to Jun 14 is NOT assumed.
    | _days(
        2027,
        (1, 1, 1, 8),
        (2, 22, 2, 23),
        (3, 8, 3, 8),
        (5, 3, 5, 3),
        (5, 10, 5, 10),
        (11, 4, 11, 4),
        (12, 31, 12, 31),
    )
)

# Saturdays that are working days (transferred from a weekday).
WORKING_SATURDAYS: frozenset[date] = frozenset(
    {
        date(2024, 4, 27),
        date(2024, 11, 2),
        date(2024, 12, 28),
        date(2025, 11, 1),
    }
)

COVERED_YEARS = frozenset({2024, 2025, 2026, 2027})
PROVISIONAL_YEARS = frozenset({2027})


def is_covered(d: date) -> bool:
    return d.year in COVERED_YEARS


def is_working_day(d: date) -> bool:
    if d in WORKING_SATURDAYS:
        return True
    return d.weekday() < 5 and d not in HOLIDAYS


def next_working_day(d: date, include_self: bool = True) -> date:
    """First working day >= d (or > d when include_self is False)."""
    cur = d if include_self else d + timedelta(days=1)
    while not is_working_day(cur):
        cur += timedelta(days=1)
    return cur


def prev_working_day(d: date, include_self: bool = True) -> date:
    """Last working day <= d (or < d when include_self is False)."""
    cur = d if include_self else d - timedelta(days=1)
    while not is_working_day(cur):
        cur -= timedelta(days=1)
    return cur


def add_working_days(d: date, n: int) -> date:
    """Move n working days forward (n > 0) or back (n < 0). n == 0 returns d unchanged."""
    cur = d
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        cur = (
            next_working_day(cur + timedelta(days=1))
            if step > 0
            else prev_working_day(cur - timedelta(days=1))
        )
    return cur


def working_days_between(a: date, b: date) -> int:
    """Signed number of working days from a to b: count of working days in (a, b]; negative if b < a."""
    if a == b:
        return 0
    lo, hi, sign = (a, b, 1) if a < b else (b, a, -1)
    count, cur = 0, lo
    while cur < hi:
        cur += timedelta(days=1)
        count += is_working_day(cur)
    return sign * count


def shift_to_working_day(d: date, rule: ShiftRule) -> date:
    """Apply a mechanism's transfer rule: 'next' (tax, loans), 'prev' (salary) or 'none'."""
    if rule == "next":
        return next_working_day(d)
    if rule == "prev":
        return prev_working_day(d)
    return d


def nominal_date(year: int, month: int, day: int, rule: ShiftRule) -> date:
    """Nominal day-of-month (clipped to month length) moved by the rule."""
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return shift_to_working_day(date(year, month, min(day, last)), rule)
