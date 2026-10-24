"""Короткая история и новые компании (docs/ml_protocol.md: X8, E07).

Правила по длине истории (дней от первой операции до as_of включительно):

* меньше 7 дней: прогноз не строится, ``ColdStartRefusal`` с объяснением;
* 7-29 дней: отраслевой prior, широкий интервал, предупреждение «очень низкое доверие»;
* 30-89 дней: отраслевой prior с большим весом собственной истории, интервал, низкое доверие;
* 90-364 дня: обычная структурная модель, доверие среднее (нет годовой сезонности);
* от 365 дней: обычная модель, доверие высокое.

Prior: профиль потока на оборот по отрасли, дню недели и трети месяца. Строится по другим
компаниям отрасли (без самой компании), у которых на as_of есть не менее 90 дней истории, и только
по их операциям не позже as_of. Потоки без финансирования, три потока как в structural:
поступления выручки, прочие поступления, списания. Оборот компании оценивается по её собственной
короткой истории. Собственный профиль по дням недели смешивается с prior с весом
``w = min(1, n / FULL_AT)``.

Интервал баланса: ``sigma_h = sqrt(h * s^2 + (h * s / sqrt(n))^2)``, где ``s`` - дневной разброс
чистого потока (медиана по донорам, на оборот, умноженная на оборот компании), второй член -
неопределённость среднего уровня по n дням. Для истории < 30 дней множитель ``SHORT_WIDEN``.
Поток умножается на ``shrink`` (сжатие к нулевому чистому потоку, как у constant). ``k``,
``FULL_AT`` и ``shrink`` подбираются на калибровочных срезах (params.json).
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from cashflow_domain.schemas import is_financing, is_revenue
from cashflow_ml.calendar_ru import is_working_day

HORIZON = 30
STREAMS = 3
REFUSE_BELOW = 7
VERY_SHORT_BELOW = 30
COLD_BELOW = 90
YEAR = 365
OWN_WINDOW = 90
DONOR_WINDOW = 180
MIN_DONOR_DAYS = 90
MIN_INDUSTRY_DONORS = 3
Z80 = 1.2816  # квантиль 90% нормального распределения: границы P10-P90
SHORT_WIDEN = 1.5
DEFAULT_FULL_AT = 90.0
DEFAULT_K = 1.0
DEFAULT_SHRINK = 1.0
PARAMS_FILE = Path(__file__).resolve().parents[2] / "experiments/results/v3/x8/params.json"


def params() -> dict:
    """{'full_at', 'k'} из params.json (калибровка X8); иначе значения по умолчанию."""
    try:
        data = json.loads(PARAMS_FILE.read_text())
        return {
            "full_at": float(data["full_at"]),
            "k": float(data["k"]),
            "shrink": float(data.get("shrink", DEFAULT_SHRINK)),
        }
    except (OSError, KeyError, ValueError):
        return {"full_at": DEFAULT_FULL_AT, "k": DEFAULT_K, "shrink": DEFAULT_SHRINK}


# ---------------------------------------------------------------- доверие и отказ


@dataclass(frozen=True)
class Confidence:
    level: str  # none | very_low | low | medium | high
    history_days: int
    refuse: bool
    label: str
    limitation: str
    warnings: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "history_days": self.history_days,
            "refuse": self.refuse,
            "label": self.label,
            "limitation": self.limitation,
            "warnings": list(self.warnings),
        }


def assess(history_days: int) -> Confidence:
    """Уровень доверия и текст ограничения по длине истории."""
    n = int(history_days)
    if n < REFUSE_BELOW:
        return Confidence(
            "none",
            n,
            True,
            "Прогноз не строится",
            f"В истории {n} дн. Нужно не менее {REFUSE_BELOW} дней операций: на меньшем сроке "
            "нельзя отличить обычный день от разового платежа.",
            ("Недостаточно истории для прогноза",),
        )
    if n < VERY_SHORT_BELOW:
        return Confidence(
            "very_low",
            n,
            False,
            "Очень низкое доверие",
            f"В истории {n} дн. (меньше {VERY_SHORT_BELOW}). Прогноз построен по профилю "
            "отрасли и масштабу первых дней, интервал широкий; регулярные платежи не выявлены. "
            "Используйте как ориентир.",
            ("Меньше 30 дней истории: прогноз ориентировочный, интервал широкий",),
        )
    if n < COLD_BELOW:
        return Confidence(
            "low",
            n,
            False,
            "Низкое доверие",
            f"В истории {n} дн. (меньше {COLD_BELOW}). Зарплата, налоги и аренда ещё не выявлены, "
            "прогноз опирается на профиль отрасли и собственную историю.",
            ("Меньше 90 дней истории: регулярные платежи и налоги не выявлены",),
        )
    if n < YEAR:
        return Confidence(
            "medium",
            n,
            False,
            "Среднее доверие",
            f"В истории {n} дн. (меньше года): годовые платежи и сезонность не учитываются.",
            (),
        )
    return Confidence("high", n, False, "Высокое доверие", "История не короче года.", ())


class ColdStartRefusal(ValueError):
    """Прогноз отклонён: слишком короткая история. .confidence объясняет причину."""

    def __init__(self, confidence: Confidence):
        super().__init__(confidence.limitation)
        self.confidence = confidence


def history_days(transactions: list[dict], as_of: date) -> int:
    """Дней от первой операции не позже as_of до as_of включительно; 0 без операций."""
    first = [t["operation_date"] for t in transactions if t["operation_date"] <= as_of.isoformat()]
    if not first:
        return 0
    return (as_of - date.fromisoformat(min(first))).days + 1


# ---------------------------------------------------------------- данные доноров

_ARR: dict[str, tuple[date, np.ndarray]] = {}
_INDUSTRY: dict[str, str] = {}
_STATS: dict[tuple, object] = {}


def stream_of(tx: dict) -> int:
    if tx["direction"] == "out":
        return 2
    return 0 if is_revenue(tx["category"], "in") else 1


def daily_array(transactions: list[dict], end: date | None = None) -> tuple[date, np.ndarray]:
    """(первый день, массив (дни, 3) в копейках) без финансирования; end - не позже этой даты."""
    rows = [
        t
        for t in transactions
        if (end is None or t["operation_date"] <= end.isoformat())
        and not is_financing(t["category"], t["direction"])
    ]
    every = [t["operation_date"] for t in transactions]
    if end is not None:
        every = [d for d in every if d <= end.isoformat()]
    if not every:
        return (end or date.today()), np.zeros((0, STREAMS))
    start = date.fromisoformat(min(every))
    last = end if end is not None else date.fromisoformat(max(every))
    arr = np.zeros(((last - start).days + 1, STREAMS))
    for t in rows:
        arr[(date.fromisoformat(t["operation_date"]) - start).days, stream_of(t)] += t[
            "amount_minor"
        ]
    return start, arr


def configure(account_data: dict | None, industries: dict | None = None) -> None:
    """Задать данные доноров {счёт: (операции, daily)} и отрасли {счёт: отрасль}.

    Вызывать до расчёта; процессы раннера наследуют состояние через fork. None - сбросить."""
    _ARR.clear()
    _INDUSTRY.clear()
    _STATS.clear()
    if not account_data:
        return
    _INDUSTRY.update(industries or {})
    for account, (txs, _) in account_data.items():
        if txs:
            _ARR[account] = daily_array(txs)


def available() -> bool:
    return bool(_ARR)


@lru_cache(maxsize=4096)
def _day_features(day: date) -> tuple[int, int, bool]:
    return day.weekday(), min(2, (day.day - 1) // 10), is_working_day(day)


@dataclass
class Donor:
    ratio: np.ndarray  # (3, 7, 3): поток на рабочий день / оборот, по потоку, дню недели, трети
    sigma: float  # std чистого дневного потока / оборот


def _donor_stats(account: str, as_of: date) -> Donor | None:
    key = (account, as_of)
    if key in _STATS:
        return _STATS[key]
    stats = None
    start, arr = _ARR[account]
    end = (as_of - start).days
    if 0 <= end < len(arr) and end + 1 >= MIN_DONOR_DAYS:
        lo = max(0, end + 1 - DONOR_WINDOW)
        seg = arr[lo : end + 1]
        turnover = seg.sum() / len(seg)
        if turnover > 0:
            feats = np.asarray(
                [_day_features(start + timedelta(days=lo + i)) for i in range(len(seg))]
            )
            ratio = np.full((STREAMS, 7, 3), np.nan)
            for dow in range(7):
                for third in range(3):
                    mask = (feats[:, 0] == dow) & (feats[:, 1] == third) & (feats[:, 2] == 1)
                    if mask.sum() >= 2:
                        ratio[:, dow, third] = seg[mask].mean(axis=0) / turnover
            net = seg[:, 0] + seg[:, 1] - seg[:, 2]
            stats = Donor(ratio, float(net.std() / turnover))
    _STATS[key] = stats
    return stats


def donors_for(account_id: str, industry: str | None, as_of: date) -> tuple[list[Donor], str]:
    """Доноры отрасли без самой компании; меньше MIN_INDUSTRY_DONORS - все компании."""
    same, other = [], []
    for account in sorted(_ARR):
        if account == account_id:
            continue
        donor = _donor_stats(account, as_of)
        if donor is None:
            continue
        other.append(donor)
        if industry and _INDUSTRY.get(account) == industry:
            same.append(donor)
    if len(same) >= MIN_INDUSTRY_DONORS:
        return same, "industry"
    return other, "all"


def prior_profile(donors: list[Donor]) -> tuple[np.ndarray, float]:
    """Медиана по донорам: профиль (3, 7, 3) и разброс чистого потока на оборот."""
    stack = np.stack([d.ratio for d in donors])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        profile = np.nanmedian(stack, axis=0)
        by_dow = np.nanmean(profile, axis=2, keepdims=True)
    profile = np.where(np.isnan(profile), np.nan_to_num(by_dow, nan=0.0), profile)
    return np.maximum(0.0, profile), float(np.median([d.sigma for d in donors]))


# ---------------------------------------------------------------- прогноз


@dataclass
class ColdForecast:
    values: np.ndarray  # (30, 3) копейки: поступления выручки, прочие поступления, списания
    half_width_minor: np.ndarray  # (30,) полуширина интервала P10-P90 для остатка
    mode: str  # prior_blend | own_only
    weight_own: float
    turnover_minor: float
    donors: int
    donor_level: str  # industry | all | none
    confidence: Confidence

    def meta(self) -> dict:
        return {
            "mode": self.mode,
            "weight_own": self.weight_own,
            "turnover_minor_per_day": self.turnover_minor,
            "donors": self.donors,
            "donor_level": self.donor_level,
            "interval": {
                "level": 0.8,
                "half_width_minor": [int(round(x)) for x in self.half_width_minor],
            },
        }


def _own_profile(arr: np.ndarray, start: date, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Средний поток на рабочий день по дням недели за последние window дней: (7, 3), (3,)."""
    seg = arr[-window:]
    first = len(arr) - len(seg)
    feats = np.asarray([_day_features(start + timedelta(days=first + i)) for i in range(len(seg))])
    work = feats[:, 2] == 1
    overall = seg[work].mean(axis=0) if work.any() else np.zeros(STREAMS)
    by_dow = np.tile(overall, (7, 1))
    for dow in range(7):
        mask = work & (feats[:, 0] == dow)
        if mask.any():
            by_dow[dow] = seg[mask].mean(axis=0)
    return by_dow, overall


def cold_forecast(
    history: list[dict],
    as_of: date,
    account_id: str,
    industry: str | None = None,
    *,
    full_at: float | None = None,
    k: float | None = None,
    shrink: float | None = None,
    use_prior: bool = True,
) -> ColdForecast:
    """Прогноз 30 дней по короткой истории (7 <= n < 90) по операциям не позже as_of."""
    n = history_days(history, as_of)
    conf = assess(n)
    if conf.refuse:
        raise ColdStartRefusal(conf)
    par = params()
    full_at = par["full_at"] if full_at is None else full_at
    k = par["k"] if k is None else k
    shrink = par["shrink"] if shrink is None else shrink
    start, arr = daily_array(history, as_of)
    window = min(OWN_WINDOW, len(arr))
    own, _ = _own_profile(arr, start, window)
    turnover = float(arr[-window:].sum() / window)
    industry = industry or _INDUSTRY.get(account_id)
    donors, level = (
        donors_for(account_id, industry, as_of) if use_prior and available() else ([], "none")
    )
    own_net = arr[-window:, 0] + arr[-window:, 1] - arr[-window:, 2]
    if donors:
        prior, sigma_ratio = prior_profile(donors)
        weight = min(1.0, n / full_at)
        mode = "prior_blend"
    else:
        prior, sigma_ratio, weight, mode = None, 0.0, 1.0, "own_only"
    values = np.zeros((HORIZON, STREAMS))
    for h in range(1, HORIZON + 1):
        day = as_of + timedelta(days=h)
        dow, third, work = _day_features(day)
        if not work:
            continue
        row = weight * own[dow]
        if prior is not None:
            row = row + (1 - weight) * turnover * prior[:, dow, third]
        values[h - 1] = row
    sigma_daily = (
        (sigma_ratio * turnover if donors and turnover > 0 else float(own_net.std()))
        * k
        * (SHORT_WIDEN if n < VERY_SHORT_BELOW else 1.0)
    )
    steps = np.arange(1, HORIZON + 1)
    half = Z80 * np.sqrt(steps * sigma_daily**2 + (steps * sigma_daily / np.sqrt(n)) ** 2)
    return ColdForecast(values * shrink, half, mode, weight, turnover, len(donors), level, conf)


def interval_bounds(balances, half_width) -> tuple[list[int], list[int]]:
    """(P10, P90) остатка вокруг траектории balances с полушириной half_width."""
    b = np.asarray(balances, dtype=float)
    h = np.asarray(half_width, dtype=float)
    return [int(round(x)) for x in b - h], [int(round(x)) for x in b + h]
