from datetime import date

from cashflow_ml import calendar_ru as cal


def test_working_saturdays_and_holidays():
    for d in (date(2024, 11, 2), date(2024, 12, 28), date(2025, 11, 1)):
        assert d.weekday() == 5 and cal.is_working_day(d)
    for d in (
        date(2024, 11, 4),
        date(2025, 1, 3),
        date(2025, 5, 2),
        date(2025, 6, 13),
        date(2025, 11, 3),
        date(2025, 12, 31),
        date(2026, 1, 9),
        date(2026, 3, 9),
        date(2026, 5, 11),
    ):
        assert d.weekday() < 5 and not cal.is_working_day(d)
    assert cal.is_working_day(date(2025, 3, 10))  # 8 March 2025 is Saturday, no transfer
    assert not cal.is_working_day(date(2025, 11, 2))  # ordinary Sunday


def test_next_prev_and_shift_rules():
    sat = date(2026, 2, 28)
    assert cal.next_working_day(sat) == date(2026, 3, 2)
    assert cal.prev_working_day(sat) == date(2026, 2, 27)
    assert cal.next_working_day(date(2026, 3, 2)) == date(2026, 3, 2)
    assert cal.next_working_day(date(2026, 3, 2), include_self=False) == date(2026, 3, 3)
    # salary on 10 May 2025 (Saturday) goes back over the holidays 8-9 May to Wednesday the 7th
    assert cal.nominal_date(2025, 5, 10, "prev") == date(2025, 5, 7)
    assert cal.nominal_date(2025, 5, 28, "next") == date(2025, 5, 28)
    assert cal.nominal_date(2026, 2, 31, "none") == date(2026, 2, 28)  # clipped to month length
    assert cal.shift_to_working_day(sat, "none") == sat


def test_add_working_days_and_distance():
    assert cal.add_working_days(date(2025, 12, 30), 1) == date(2026, 1, 12)
    assert cal.add_working_days(date(2026, 1, 12), -1) == date(2025, 12, 30)
    assert cal.add_working_days(date(2026, 3, 2), 0) == date(2026, 3, 2)
    assert cal.add_working_days(date(2026, 3, 6), 2) == date(2026, 3, 11)  # 9 March is a holiday
    assert cal.working_days_between(date(2026, 3, 6), date(2026, 3, 11)) == 2
    assert cal.working_days_between(date(2026, 3, 11), date(2026, 3, 6)) == -2
    assert cal.working_days_between(date(2026, 3, 3), date(2026, 3, 3)) == 0


def test_acquiring_settlement_is_next_working_day_after_registry():
    # registry date Fri 2025-12-26 credits Mon 2025-12-29; Tue 30th credits Mon 12 Jan 2026.
    assert cal.next_working_day(date(2025, 12, 26) + cal.timedelta(days=1)) == date(2025, 12, 29)
    assert cal.next_working_day(date(2025, 12, 30) + cal.timedelta(days=1)) == date(2026, 1, 12)


def test_coverage_flags():
    assert cal.is_covered(date(2026, 6, 1)) and not cal.is_covered(date(2028, 1, 3))
    assert not cal.is_working_day(date(2028, 1, 1)) and cal.is_working_day(date(2028, 1, 3))
    assert 2027 in cal.PROVISIONAL_YEARS
