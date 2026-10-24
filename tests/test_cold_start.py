from datetime import date, timedelta

import numpy as np
import pytest

from cashflow_ml import cold_start as C
from cashflow_ml import structural as S

AS_OF = date(2026, 3, 31)
_ID = iter(range(10**9))


def tx(day, amount, category="customer_payment", direction="in"):
    return dict(
        operation_id=f"T{next(_ID)}",
        operation_date=day.isoformat(),
        amount_minor=amount,
        direction=direction,
        counterparty_inn="111",
        counterparty_name="Контрагент",
        payment_purpose="",
        category=category,
    )


def company(days, scale=1, end=AS_OF):
    """Выручка 100 000 руб. и расходы 60 000 руб. в каждый рабочий день за `days` дней."""
    out = []
    for k in range(days):
        day = end - timedelta(days=k)
        if C.is_working_day(day):
            out.append(tx(day, 100_000_00 * scale))
            out.append(tx(day, 60_000_00 * scale, "supplier", "out"))
    return sorted(out, key=lambda t: t["operation_date"])


def teardown_function():
    C.configure(None)
    S.configure(None, {})


def donors(n=5, scale=1):
    data = {f"D{i}": (company(200, scale), []) for i in range(n)}
    C.configure(data, {a: "Торговля" for a in data})
    return data


def test_assess_levels_and_refusal():
    assert C.assess(3).refuse and C.assess(3).level == "none"
    assert C.assess(7).level == "very_low" and not C.assess(7).refuse
    assert C.assess(29).level == "very_low"
    assert C.assess(30).level == "low"
    assert C.assess(89).level == "low"
    assert C.assess(90).level == "medium"
    assert C.assess(364).level == "medium"
    assert C.assess(365).level == "high"
    assert "7" in C.assess(3).limitation


def test_history_days_counts_from_first_operation_up_to_as_of():
    txs = company(20)
    n = C.history_days(txs, AS_OF)
    assert 15 <= n <= 20
    assert C.history_days(txs, AS_OF - timedelta(days=100)) == 0
    future = txs + [tx(AS_OF + timedelta(days=5), 1)]
    assert C.history_days(future, AS_OF) == n


def test_refuses_under_seven_days_with_explanation():
    donors()
    with pytest.raises(C.ColdStartRefusal) as error:
        S.structural_forecast(company(4), AS_OF, "NEW", tax=False, docs=False, refuse=True)
    assert error.value.confidence.refuse
    assert "не менее 7" in str(error.value)
    # по умолчанию отказ не бросается: прогноз есть, но в meta стоит флаг отказа и текст
    default = S.structural_forecast(company(4), AS_OF, "NEW", tax=False, docs=False)
    assert default.meta["confidence"]["refuse"] and default.meta["confidence"]["level"] == "none"
    assert "cold_start" not in default.meta


def test_prior_used_for_short_history_and_scales_with_own_turnover():
    donors()
    base = S.structural_forecast(company(14), AS_OF, "NEW", tax=False, docs=False)
    assert base.meta["cold_start"]["mode"] == "prior_blend"
    assert base.meta["cold_start"]["donor_level"] == "all"
    assert base.meta["confidence"]["level"] == "very_low"
    assert base.events == []
    double = S.structural_forecast(company(14, scale=2), AS_OF, "NEW", tax=False, docs=False)
    assert np.allclose(double.values, 2 * base.values, rtol=1e-9)
    assert base.values[:, 0].sum() > 0


def test_normal_route_from_ninety_days_with_confidence():
    S.configure(None, {})
    result = S.structural_forecast(company(120), AS_OF, "OLD", tax=False, docs=False)
    assert "cold_start" not in result.meta
    assert result.meta["confidence"]["level"] == "medium"
    long = S.structural_forecast(company(400), AS_OF, "OLD", tax=False, docs=False)
    assert long.meta["confidence"]["level"] == "high"


def test_prior_excludes_the_company_itself():
    data = donors(4)
    data["SELF"] = (company(200, scale=50), [])
    C.configure(data, {a: "Торговля" for a in data})
    with_self = C.cold_forecast(company(20), AS_OF, "SELF", "Торговля")
    assert with_self.donors == 4
    other = C.cold_forecast(company(20), AS_OF, "NEW", "Торговля")
    assert other.donors == 5


def test_no_future_leakage_into_donor_profile():
    data = donors()
    first = C.cold_forecast(company(20), AS_OF, "NEW", "Торговля", full_at=1e12)
    # после as_of у доноров появляются огромные операции: профиль не должен измениться
    leaked = {}
    for a, (txs, d) in data.items():
        leaked[a] = (txs + [tx(AS_OF + timedelta(days=3), 10**12)], d)
    C.configure(leaked, {a: "Торговля" for a in leaked})
    second = C.cold_forecast(company(20), AS_OF, "NEW", "Торговля", full_at=1e12)
    assert np.allclose(first.values, second.values)


def test_interval_wider_for_shorter_history_and_grows_with_horizon():
    donors()

    # шумная собственная история: расходы по дням разные
    def noisy(days):
        out = []
        for k in range(days):
            day = AS_OF - timedelta(days=k)
            if C.is_working_day(day):
                out.append(tx(day, 100_000_00))
                out.append(tx(day, (30 + 40 * (k % 3)) * 1000_00, "supplier", "out"))
        return out

    short = C.cold_forecast(noisy(12), AS_OF, "NEW", "Торговля")
    mid = C.cold_forecast(noisy(60), AS_OF, "NEW", "Торговля")
    assert np.all(np.diff(short.half_width_minor) > 0)
    assert short.half_width_minor[-1] > 0
    # доноры одинаковы, оборот близок: короче история - шире относительно оборота
    assert (
        short.half_width_minor[-1] / short.turnover_minor
        > mid.half_width_minor[-1] / mid.turnover_minor
    )
    low, high = C.interval_bounds([100, 200], [10, 20])
    assert low == [90, 180] and high == [110, 220]


def test_own_only_without_donors_has_warning_mode():
    C.configure(None)
    result = C.cold_forecast(company(20), AS_OF, "NEW", None)
    assert result.mode == "own_only"
    assert result.weight_own == 1.0
    assert result.donors == 0


def test_blend_weight_grows_with_history():
    donors(scale=3)
    short = C.cold_forecast(company(10), AS_OF, "NEW", "Торговля", full_at=90)
    mid = C.cold_forecast(company(60), AS_OF, "NEW", "Торговля", full_at=90)
    assert short.weight_own < mid.weight_own <= 1.0


def test_protocol_method_returns_interval_for_cold_history():
    donors()
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        account_id="NEW", opening_minor=1_000_000_00, registry=SimpleNamespace(root=None)
    )
    forecast = S.protocol_method(tax=False, docs=False)(company(15), [], AS_OF, ctx)
    assert len(forecast.balances_minor) == 30
    assert all(
        a <= b <= c
        for a, b, c in zip(
            forecast.p10_minor, forecast.balances_minor, forecast.p90_minor, strict=True
        )
    )
