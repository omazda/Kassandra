"""Протокол v3 (docs/ml_protocol.md: D1, D2, D3).

Цель D3 — остаток без нового внешнего финансирования после as_of. Метка D2 — разрыв: EOD ниже
порога или поступление финансирования, без которого EOD ниже порога. Метрики D1 считаются по
оконным записям, одинаковым для всех методов. Будущие данные видит только построитель целей
(AccountTruth); методы получают операции и дневные строки не позже as_of.
"""

import bisect
import csv
import gzip
import json
import multiprocessing
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from cashflow_domain.engine import assemble
from cashflow_domain.schemas import is_financing
from cashflow_ml.evaluation import eligible
from cashflow_ml.features import categories_at, feature_matrix, prepare
from cashflow_ml.models import load_model

HORIZON = 30
MONTH_DAYS = 30.4375
SPLIT_ORDER = ("calibration", "selection", "retrospective")


# ---------------------------------------------------------------- срезы


def expand_origins(config: dict) -> dict[str, list[date]]:
    """Развернуть срезы из p3.json; проверить, что калибровочные цели не заходят в выбор."""
    result = {}
    for split, spec in config["splits"].items():
        if "origins" in spec:
            origins = [date.fromisoformat(x) for x in spec["origins"]]
        else:
            origins, cursor = [], date.fromisoformat(spec["start"])
            end = date.fromisoformat(spec["end"])
            while cursor <= end:
                origins.append(cursor)
                cursor += timedelta(days=spec["step_days"])
        result[split] = origins
    limit = config.get("calibration_targets_until")
    if limit and "calibration" in result:
        horizon = config.get("horizon_days", HORIZON)
        if max(result["calibration"]) + timedelta(days=horizon) > date.fromisoformat(limit):
            raise ValueError("Цели калибровки заходят за границу calibration_targets_until")
        if "selection" in result and date.fromisoformat(limit) >= min(result["selection"]):
            raise ValueError("Калибровочные цели пересекаются со срезами выбора")
    return result


def split_step_days(config: dict, split: str) -> int:
    spec = config["splits"][split]
    if "step_days" in spec:
        return int(spec["step_days"])
    origins = sorted(spec["origins"])
    gaps = [
        (date.fromisoformat(b) - date.fromisoformat(a)).days
        for a, b in zip(origins, origins[1:], strict=False)
    ]
    return min(gaps) if gaps else 7


# ---------------------------------------------------------------- истина: D3 и D2


class AccountTruth:
    """Истина по счёту. Использует всё архивное будущее: только для целей и оценки."""

    def __init__(self, transactions: list[dict], daily: list[dict], threshold_minor: int = 0):
        self.threshold = threshold_minor
        self.balance = {date.fromisoformat(d["date"]): d["balance_minor"] for d in daily}
        self.valid = {date.fromisoformat(d["date"]): bool(d["valid"]) for d in daily}
        self.financing = defaultdict(int)
        for tx in transactions:
            if is_financing(tx["category"], tx["direction"]):
                self.financing[date.fromisoformat(tx["operation_date"])] += tx["amount_minor"]
        self.stress = {}
        for day, balance in self.balance.items():
            funded = self.financing.get(day, 0)
            self.stress[day] = self.valid[day] and (
                balance < threshold_minor or (funded > 0 and balance - funded < threshold_minor)
            )
        self.events = self._episodes()

    def _episodes(self) -> list[dict]:
        events, current = [], None
        for day in sorted(self.balance):
            previous = day - timedelta(days=1)
            if self.stress[day]:
                shortage = self.threshold - (self.balance[day] - self.financing.get(day, 0))
                if current is not None and current["end"] == previous:
                    current["end"], current["deficit_minor"] = (
                        day,
                        max(current["deficit_minor"], shortage),
                    )
                elif self.valid.get(previous, False) and not self.stress.get(previous, True):
                    current = {
                        "start": day,
                        "end": day,
                        "kind": "negative_eod"
                        if self.balance[day] < self.threshold
                        else "financing",
                        "deficit_minor": shortage,
                    }
                    events.append(current)
                else:
                    current = None
            else:
                current = None
        return events

    def window(self, origin: date) -> dict:
        """Цели окна (origin, origin+30]: наблюдаемый EOD, цель D3, события D2."""
        days = [origin + timedelta(days=h) for h in range(1, HORIZON + 1)]
        observed = np.asarray([self.balance[d] for d in days], dtype=np.int64)
        funded = np.cumsum([self.financing.get(d, 0) for d in days])
        end = days[-1]
        offsets = [(e["start"] - origin).days for e in self.events if origin < e["start"] <= end]
        opening = self.balance[origin]
        return {
            "opening_minor": int(opening),
            "observed": observed,
            "target": observed - funded,
            "financing_total_minor": int(funded[-1]),
            "event_offsets": offsets,
            "current_deficit": bool(opening < self.threshold),
        }


def event_manifest(truths: dict, parts: dict) -> dict:
    """Число D2-событий и компаний по частям архива; v2-совместимый счёт — только отрицательный EOD."""
    rows = [
        {"account_id": a, **e, "start": str(e["start"]), "end": str(e["end"])}
        for a, truth in sorted(truths.items())
        for e in truth.events
    ]
    manifest = {}
    for name, (low, high) in parts.items():
        part = [
            r
            for r in rows
            if (low is None or r["start"] >= low) and (high is None or r["start"] <= high)
        ]
        negative = [r for r in part if r["kind"] == "negative_eod"]
        manifest[name] = {
            "from": low,
            "to": high,
            "events": len(part),
            "companies": len({r["account_id"] for r in part}),
            "events_negative_eod": len(negative),
            "companies_negative_eod": len({r["account_id"] for r in negative}),
            "events_financing_rescue": len(part) - len(negative),
        }
    manifest["total"] = {
        "events": len(rows),
        "companies": len({r["account_id"] for r in rows}),
        "companies_total": len(truths),
    }
    return {"parts": manifest, "events": rows}


# ---------------------------------------------------------------- интерфейс метода


@dataclass
class Forecast:
    """Прогноз метода: 30 дневных остатков (копейки), цель D3 — без нового финансирования."""

    balances_minor: list
    p_gap_30d: float | None = None
    p10_minor: list | None = None
    p90_minor: list | None = None
    meta: dict = field(default_factory=dict)


class Context:
    """Контекст одного среза (счёт, as_of). Кэширует подготовку, общую для всех методов."""

    def __init__(self, account_id, as_of, opening_minor, registry, config, threshold_minor=0):
        self.account_id, self.as_of = account_id, as_of
        self.opening_minor, self.registry = opening_minor, registry
        self.config, self.threshold_minor = config, threshold_minor
        self._prepared, self._net = {}, None
        self._transactions, self._daily = [], []

    def bind(self, transactions, daily):
        self._transactions, self._daily = transactions, daily
        return self

    def prepared(self, calendar: bool = False):
        """(flows, components, scale) из features.prepare; считается один раз на срез."""
        if calendar not in self._prepared:
            self._prepared[calendar] = prepare(self._transactions, self.as_of, calendar)
        return self._prepared[calendar]

    def net_flow_ex_financing(self) -> dict:
        """date -> (поступления - списания - финансирование) по истории до as_of, копейки."""
        if self._net is None:
            funded = defaultdict(int)
            for tx in self._transactions:
                if is_financing(tx["category"], tx["direction"]):
                    funded[tx["operation_date"]] += tx["amount_minor"]
            self._net = {
                d["date"]: d["inflow_minor"] - d["outflow_minor"] - funded.get(d["date"], 0)
                for d in self._daily
            }
        return self._net

    def valid_days(self) -> set:
        return {d["date"] for d in self._daily if d["valid"]}


def balances_from_flows(ctx: Context, values) -> list:
    points = assemble(ctx.as_of, ctx.opening_minor, np.maximum(0, values), [])
    return [p.closing_balance_minor for p in points]


def forecast_constant(transactions, daily, as_of, ctx):
    return Forecast([ctx.opening_minor] * HORIZON)


def forecast_mean60(transactions, daily, as_of, ctx):
    flows, _, _ = ctx.prepared(False)
    values = np.tile(flows.tail(60).mean().to_numpy(), (HORIZON, 1))
    return Forecast(balances_from_flows(ctx, values))


def forecast_weekday(transactions, daily, as_of, ctx):
    # Те же формулы, что models.predict(model='weekday'); эквивалентность проверяет тест.
    flows, _, _ = ctx.prepared(False)
    past = flows.tail(90)
    by_dow = past.groupby(past.index.dayofweek).mean()
    values = np.asarray(
        [
            by_dow.loc[(as_of + timedelta(days=h)).weekday()].to_numpy()
            if (as_of + timedelta(days=h)).weekday() in by_dow.index
            else past.mean().to_numpy()
            for h in range(1, HORIZON + 1)
        ]
    )
    return Forecast(balances_from_flows(ctx, values))


def forecast_hgb_v2(transactions, daily, as_of, ctx):
    # models.predict(model='hgb', calendar=False): модель из реестра, trained_to <= as_of.
    flows, _, scale = ctx.prepared(False)
    meta = ctx.registry.select(as_of, False)
    with threadpool_limits(limits=1):
        values = load_model(meta["path"]).predict(feature_matrix(flows, as_of, scale)) * scale
    return Forecast(balances_from_flows(ctx, values), meta={"model_version": meta["model_version"]})


def forecast_hgb_cal(transactions, daily, as_of, ctx):
    # Путь продукта по умолчанию: models.predict(model='hgb', calendar=True) + события регулярности.
    from cashflow_ml.regularity import predicted_events

    flows, components, scale = ctx.prepared(True)
    meta = ctx.registry.select(as_of, True)
    with threadpool_limits(limits=1):
        values = load_model(meta["path"]).predict(feature_matrix(flows, as_of, scale)) * scale
    events = predicted_events(components, ctx.account_id, as_of)
    points = assemble(as_of, ctx.opening_minor, np.maximum(0, values), events)
    return Forecast(
        [p.closing_balance_minor for p in points], meta={"model_version": meta["model_version"]}
    )


def _months_back(day: date, k: int) -> date:
    month = day.month - k
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def shape_profile(net: dict, valid: set, as_of: date, months: int = 3, search: int = 8):
    """Профиль месяца без тренда: медиана накопленного потока (без финансирования) за три
    предыдущих полных 30-дневных окна, сдвинутых на целые месяцы назад, минус линейный дрейф.

    Берутся первые `months` окон origin_k = as_of - k месяцев, у которых origin_k+30 <= as_of
    и все 30 дней валидны. Возвращает (профиль из 30 значений в копейках, число использованных
    окон); пустой набор даёт нули (равно constant).
    """
    profiles = []
    for k in range(1, search + 1):
        origin = _months_back(as_of, k)
        if origin + timedelta(days=HORIZON) > as_of:
            continue
        days = [str(origin + timedelta(days=h)) for h in range(1, HORIZON + 1)]
        if not all(d in valid for d in days):
            continue
        cumulative = np.cumsum([net.get(d, 0) for d in days]).astype(float)
        drift = cumulative[-1] / HORIZON * np.arange(1, HORIZON + 1)
        profiles.append(cumulative - drift)
        if len(profiles) == months:
            break
    if not profiles:
        return np.zeros(HORIZON), 0
    return np.median(np.asarray(profiles), axis=0), len(profiles)


def forecast_shape_only(transactions, daily, as_of, ctx):
    profile, used = shape_profile(ctx.net_flow_ex_financing(), ctx.valid_days(), as_of)
    balances = [int(round(ctx.opening_minor + v)) for v in profile]
    return Forecast(balances, meta={"profile_months": used})


METHODS = {
    "constant": forecast_constant,
    "mean60": forecast_mean60,
    "weekday": forecast_weekday,
    "hgb_v2": forecast_hgb_v2,
    "hgb_cal": forecast_hgb_cal,
    "shape_only": forecast_shape_only,
}


def register_method(name: str, forecaster) -> None:
    """Добавить метод: forecaster(transactions, daily, as_of, context) -> Forecast."""
    METHODS[name] = forecaster


# ---------------------------------------------------------------- оконные записи


def window_record(split, account, origin, method, forecast, truth_window, scale, threshold):
    pred = np.asarray(forecast.balances_minor, dtype=np.int64)
    if pred.shape != (HORIZON,):
        raise ValueError(f"Метод {method} вернул {pred.shape} вместо 30 остатков")
    target, observed = truth_window["target"], truth_window["observed"]
    err = np.abs(target - pred)
    obs_err = np.abs(observed - pred)
    below = np.flatnonzero(pred < threshold)
    current = truth_window["current_deficit"]
    offsets = truth_window["event_offsets"]
    return {
        "split": split,
        "account_id": account,
        "origin": str(origin),
        "method": method,
        "opening_minor": truth_window["opening_minor"],
        "scale_minor": float(scale),
        "current_deficit": current,
        "mae_traj_minor": float(err.mean()),
        "mae_d30_minor": float(err[-1]),
        "mae_min_minor": float(abs(target.min() - pred.min())),
        "norm_mae": float(err.mean() / scale),
        "obs_mae_traj_minor": float(obs_err.mean()),
        "obs_mae_d30_minor": float(obs_err[-1]),
        "obs_mae_min_minor": float(abs(observed.min() - pred.min())),
        "actual_min_minor": int(target.min()),
        "pred_min_minor": int(pred.min()),
        "alarm": bool((not current) and below.size > 0),
        "alarm_offset": int(below[0]) + 1 if below.size else None,
        "pred_deficit_minor": int(max(0, threshold - pred.min())),
        "actual_deficit_minor": int(max(0, threshold - target.min())),
        "event_offsets": list(offsets),
        "first_event_offset": min(offsets) if offsets else None,
        "p_gap_30d": forecast.p_gap_30d,
        "path": pred.tolist(),
        "p10": list(forecast.p10_minor) if forecast.p10_minor is not None else None,
        "p90": list(forecast.p90_minor) if forecast.p90_minor is not None else None,
    }


_STATE: dict = {}


def _slice(account, origin):
    """Данные, доступные методу на as_of: операции и дневные строки не позже origin."""
    txs, daily = _STATE["data"][account]
    dates = _STATE["dates"][account]
    sliced = txs[: bisect.bisect_right(dates, str(origin))]
    visible = [d for d in daily if d["date"] <= str(origin)]
    return categories_at(sliced, origin), visible


def _account_job(account):
    config, methods = _STATE["config"], _STATE["methods"]
    threshold = config.get("threshold_minor", 0)
    truth, (_, daily) = _STATE["truth"][account], _STATE["data"][account]
    records, dropped = [], []
    for split, origins in _STATE["origins"].items():
        for origin in origins:
            if not eligible(daily, origin):
                continue
            window = truth.window(origin)
            txs, visible = _slice(account, origin)
            ctx = Context(
                account, origin, window["opening_minor"], _STATE["registry"], config, threshold
            ).bind(txs, visible)
            produced = {}
            try:
                for name, forecaster in methods.items():
                    produced[name] = forecaster(txs, visible, origin, ctx)
                scale = ctx.prepared(False)[2]
            except Exception as error:  # окно выбывает для всех методов сразу
                dropped.append(
                    {
                        "split": split,
                        "account_id": account,
                        "origin": str(origin),
                        "reason": f"{type(error).__name__}: {error}"[:200],
                    }
                )
                continue
            for name, forecast in produced.items():
                records.append(
                    window_record(split, account, origin, name, forecast, window, scale, threshold)
                )
    return records, dropped


def run_protocol(
    account_data: dict,
    methods: dict,
    origins: dict,
    registry=None,
    config: dict | None = None,
    out_dir=None,
    n_jobs: int = 2,
    progress=print,
) -> dict:
    """Прогнать методы на срезах. methods: имя -> forecaster; origins: split -> [date]."""
    config = config or {}
    threshold = config.get("threshold_minor", 0)
    truths = {a: AccountTruth(txs, daily, threshold) for a, (txs, daily) in account_data.items()}
    _STATE.update(
        data=account_data,
        dates={a: [t["operation_date"] for t in txs] for a, (txs, _) in account_data.items()},
        truth=truths,
        config=config,
        methods=methods,
        origins=origins,
        registry=registry,
    )
    accounts = sorted(account_data)
    records, dropped = [], []
    if n_jobs > 1:
        with multiprocessing.get_context("fork").Pool(min(n_jobs, 2)) as pool:
            for i, (r, d) in enumerate(pool.imap(_account_job, accounts, chunksize=1), 1):
                records += r
                dropped += d
                if i % 25 == 0:
                    progress(f"{i}/{len(accounts)} счетов")
    else:
        for a in accounts:
            r, d = _account_job(a)
            records += r
            dropped += d
    records.sort(key=lambda r: (SPLIT_ORDER.index(r["split"]), r["origin"], r["account_id"]))
    parts = config.get("manifest_parts") or {}
    manifest = event_manifest(truths, parts) if parts else {"parts": {}, "events": []}
    result = {
        "records": records,
        "dropped_windows": dropped,
        "manifest": manifest,
        "summary": summarize(records, config, list(methods)),
    }
    if out_dir is not None:
        save_results(result, config, out_dir)
    return result


# ---------------------------------------------------------------- метрики D1


def bootstrap_median(values: list, resamples: int = 1000, seed: int = 42) -> dict:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return {"median": None, "ci_low": None, "ci_high": None, "n_companies": 0}
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, values.size, size=(resamples, values.size))
    medians = np.median(values[draws], axis=1)
    return {
        "median": float(np.median(values)),
        "ci_low": float(np.percentile(medians, 2.5)),
        "ci_high": float(np.percentile(medians, 97.5)),
        "n_companies": int(values.size),
        "share_positive": float(np.mean(values > 0)),
    }


def wilson(successes: int, total: int, z: float = 1.96):
    if total == 0:
        return None, None
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * np.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return float(centre - half), float(centre + half)


def company_skills(rows: list, constant_rows: dict, key: str) -> list:
    """Skill компании = 1 - sum(ошибка метода) / sum(ошибка constant) по её окнам."""
    method, base = defaultdict(float), defaultdict(float)
    for r in rows:
        method[r["account_id"]] += r[key]
        base[r["account_id"]] += constant_rows[(r["account_id"], r["origin"])][key]
    return [1 - method[a] / base[a] for a in sorted(method) if base[a] > 0]


def macro(rows: list, key: str) -> float | None:
    by_company = defaultdict(list)
    for r in rows:
        by_company[r["account_id"]].append(r[key])
    if not by_company:
        return None
    return float(np.mean([np.mean(v) for v in by_company.values()]))


def risk_metrics(rows: list, config: dict, step_days: int) -> dict:
    """Событийные метрики D2. Окна с текущим дефицитом исключены из тревог и событий."""
    low = config.get("min_warning_days", 7)
    tolerance = config.get("date_tolerance_days", 3)
    open_rows = [r for r in rows if not r["current_deficit"]]
    events, caught = set(), set()
    alarms = [r for r in open_rows if r["alarm"]]
    false_alarms = []
    for r in open_rows:
        origin = date.fromisoformat(r["origin"])
        # Тревога засчитывается только если её дата в пределах допуска от даты события.
        hit = r["alarm"] and r["alarm_offset"] is not None
        matched = False
        for offset in r["event_offsets"]:
            near = hit and abs(r["alarm_offset"] - offset) <= tolerance
            matched = matched or near
            if offset >= low:
                key = (r["account_id"], str(origin + timedelta(days=offset)))
                events.add(key)
                if near:
                    caught.add(key)
        if r["alarm"] and not matched:
            false_alarms.append(r)
    account_months = len(open_rows) * step_days / MONTH_DAYS
    dated = [r for r in alarms if r["first_event_offset"] is not None]
    date_err = [abs(r["alarm_offset"] - r["first_event_offset"]) for r in dated]
    deficit_err = [abs(r["pred_deficit_minor"] - r["actual_deficit_minor"]) / 100 for r in dated]
    low_ci, high_ci = wilson(len(caught), len(events))
    return {
        "windows_at_risk": len(open_rows),
        "windows_current_deficit": len(rows) - len(open_rows),
        "events_scorable": len(events),
        "events_detected": len(caught),
        "recall_7d": len(caught) / len(events) if events else None,
        "recall_7d_ci": [low_ci, high_ci],
        "alarm_windows": len(alarms),
        "false_alarm_windows": len(false_alarms),
        "account_months": account_months,
        "false_alarms_per_account_month": len(false_alarms) / account_months
        if account_months
        else None,
        "date_error_n": len(date_err),
        "date_within_tolerance": float(np.mean([e <= tolerance for e in date_err]))
        if date_err
        else None,
        "date_error_median_days": float(np.median(date_err)) if date_err else None,
        "deficit_error_n": len(deficit_err),
        "deficit_mae_rub": float(np.mean(deficit_err)) if deficit_err else None,
        "deficit_median_ae_rub": float(np.median(deficit_err)) if deficit_err else None,
    }


def probability_metrics(rows: list, calibration_rate: float | None, bins: int = 10) -> dict | None:
    scored = [r for r in rows if not r["current_deficit"] and r["p_gap_30d"] is not None]
    if not scored:
        return None
    p = np.asarray([r["p_gap_30d"] for r in scored])
    y = np.asarray([bool(r["event_offsets"]) for r in scored], dtype=float)
    brier = float(np.mean((p - y) ** 2))
    own_rate = float(y.mean())

    def skill(rate):
        base = float(np.mean((rate - y) ** 2))
        return {"brier_base": base, "brier_skill": 1 - brier / base if base > 0 else None}

    edges = np.linspace(0, 1, bins + 1)
    index = np.minimum(np.digitize(p, edges[1:-1]), bins - 1)
    reliability = [
        {
            "bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}",
            "n": int(np.sum(index == b)),
            "mean_p": float(p[index == b].mean()) if np.any(index == b) else None,
            "observed": float(y[index == b].mean()) if np.any(index == b) else None,
        }
        for b in range(bins)
    ]
    return {
        "n": len(scored),
        "brier": brier,
        "base_rate_split": own_rate,
        "vs_split_rate": skill(own_rate),
        "base_rate_calibration": calibration_rate,
        "vs_calibration_rate": skill(calibration_rate) if calibration_rate is not None else None,
        "reliability": reliability,
    }


def summarize(records: list, config: dict, methods: list | None = None) -> dict:
    boot = config.get("bootstrap", {"resamples": 1000, "seed": 42})
    methods = methods or sorted({r["method"] for r in records})
    base_rows = [r for r in records if r["split"] == "calibration" and r["method"] == methods[0]]
    open_base = [r for r in base_rows if not r["current_deficit"]]
    calibration_rate = (
        float(np.mean([bool(r["event_offsets"]) for r in open_base])) if open_base else None
    )
    summary = {}
    for split in SPLIT_ORDER:
        split_rows = [r for r in records if r["split"] == split]
        if not split_rows:
            continue
        constant = {
            (r["account_id"], r["origin"]): r for r in split_rows if r["method"] == "constant"
        }
        step = split_step_days(config, split) if config.get("splits") else 7
        summary[split] = {}
        for method in methods:
            rows = [r for r in split_rows if r["method"] == method]
            if not rows:
                continue
            entry = {
                "windows": len(rows),
                "companies": len({r["account_id"] for r in rows}),
                "macro_mae_rub": macro(rows, "mae_traj_minor") / 100,
                "macro_mae_d30_rub": macro(rows, "mae_d30_minor") / 100,
                "macro_mae_min_rub": macro(rows, "mae_min_minor") / 100,
                "normalized_mae": macro(rows, "norm_mae"),
                "observed_macro_mae_rub": macro(rows, "obs_mae_traj_minor") / 100,
                "observed_macro_mae_min_rub": macro(rows, "obs_mae_min_minor") / 100,
            }
            if constant:
                for name, key in [
                    ("skill_trajectory", "mae_traj_minor"),
                    ("skill_minimum", "mae_min_minor"),
                    ("skill_day30", "mae_d30_minor"),
                    ("observed_skill_trajectory", "obs_mae_traj_minor"),
                    ("observed_skill_minimum", "obs_mae_min_minor"),
                ]:
                    entry[name] = bootstrap_median(
                        company_skills(rows, constant, key), boot["resamples"], boot["seed"]
                    )
            entry["risk"] = risk_metrics(rows, config, step)
            entry["probability"] = probability_metrics(
                rows, calibration_rate, config.get("reliability_bins", 10)
            )
            summary[split][method] = entry
    return summary


# ---------------------------------------------------------------- решение D1


def d1_decision(summary_selection: dict, candidate: str, default: str, config: dict) -> dict:
    """Правило D1: skill минимума > 0, skill траектории >= -2%, recall@7d не хуже default
    при бюджете ложных тревог. Constant не может быть default."""
    budget = config.get("alarm_budget_per_account_month", 1.0)
    cand, base = summary_selection[candidate], summary_selection[default]
    cand_risk, base_risk = cand["risk"], base["risk"]
    checks = {
        "skill_minimum_positive": (cand["skill_minimum"]["median"] or 0) > 0,
        "skill_trajectory_ge_minus_2pct": (cand["skill_trajectory"]["median"] or -1) >= -0.02,
        "false_alarm_budget": (cand_risk["false_alarms_per_account_month"] or 0) <= budget,
        "recall_not_worse": (cand_risk["recall_7d"] or 0) >= (base_risk["recall_7d"] or 0),
        "candidate_not_constant": candidate != "constant",
    }
    return {
        "candidate": candidate,
        "default": default,
        "checks": checks,
        "pass": all(checks.values()),
    }


# ---------------------------------------------------------------- сохранение


def _fmt(x, digits=3):
    return "—" if x is None else f"{x:.{digits}f}"


def baseline_markdown(result: dict, config: dict) -> str:
    summary, manifest = result["summary"], result["manifest"]["parts"]
    lines = ["# Базовая таблица протокола v3", ""]
    lines += [
        "Цель D3: остаток без нового внешнего финансирования после as_of. Skill к constant: "
        "медиана по компаниям, 95% бутстрэп-интервал по компаниям (1000, seed 42).",
        "",
        "## Манифест событий D2 по частям",
        "",
        "| Часть | События | Компании | Отриц. EOD | Компании (отриц.) | Через финансирование |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, m in manifest.items():
        if name == "total":
            continue
        lines.append(
            f"| {name} | {m['events']} | {m['companies']} | {m['events_negative_eod']} | "
            f"{m['companies_negative_eod']} | {m['events_financing_rescue']} |"
        )
    if "total" in manifest:
        t = manifest["total"]
        lines.append(
            f"| всего | {t['events']} | {t['companies']} из {t['companies_total']} | | | |"
        )
    for split in ("selection", "retrospective", "calibration"):
        if split not in summary:
            continue
        lines += ["", f"## Срез: {split}", ""]
        lines += [
            "| Метод | Окон | Компаний | Skill траект. | Skill миним. | MAE руб | Норм. MAE | "
            "MAE д.30 руб | MAE мин. руб |",
            "|---|---:|---:|---|---|---:|---:|---:|---:|",
        ]
        for method, e in summary[split].items():

            def sk(name, e=e):
                s = e.get(name)
                if not s or s["median"] is None:
                    return "—"
                return f"{s['median']:+.3f} [{s['ci_low']:+.3f}; {s['ci_high']:+.3f}]"

            lines.append(
                f"| {method} | {e['windows']} | {e['companies']} | {sk('skill_trajectory')} | "
                f"{sk('skill_minimum')} | {e['macro_mae_rub']:,.0f} | {_fmt(e['normalized_mae'])} | "
                f"{e['macro_mae_d30_rub']:,.0f} | {e['macro_mae_min_rub']:,.0f} |"
            )
        lines += [
            "",
            "| Метод | Событий (≥7 дн.) | Найдено | Recall@7d | Ложных тревог / счёт-мес | "
            "Дата ±3 дн. | MAE дефицита руб |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for method, e in summary[split].items():
            r = e["risk"]
            lines.append(
                f"| {method} | {r['events_scorable']} | {r['events_detected']} | "
                f"{_fmt(r['recall_7d'])} | {_fmt(r['false_alarms_per_account_month'])} "
                f"({r['false_alarm_windows']} окон) | {_fmt(r['date_within_tolerance'])} "
                f"(n={r['date_error_n']}) | {_fmt(r['deficit_mae_rub'], 0)} |"
            )
        if split != "calibration":
            lines += [
                "",
                "Вторичная цель (наблюдаемый EOD, со всем финансированием): "
                + "; ".join(
                    f"{m}: skill траект. {_fmt(e['observed_skill_trajectory']['median'])}, "
                    f"миним. {_fmt(e['observed_skill_minimum']['median'])}"
                    for m, e in summary[split].items()
                    if m != "constant" and "observed_skill_trajectory" in e
                ),
            ]
    lines += [
        "",
        "## Примечания",
        "",
        "- Окна зависимы (недельные срезы перекрываются на 23 дня); интервалы бутстрэпа "
        "по компаниям не учитывают эту зависимость между срезами одной компании.",
        "- Recall считается по событиям с хотя бы одним срезом без текущего дефицита, "
        "где событие наступает через 7-30 дней; одно событие даёт максимум один TP.",
        "- Ложная тревога: срез без текущего дефицита, где метод ждёт EOD ниже порога, "
        "а событий D2 в 30 днях нет. Счёт-месяц = окон x шаг / 30,4375.",
        "- Ретроспектива июля-августа уже просматривалась и служит только отчётом.",
        "- Определения: metrics_spec.md.",
    ]
    dropped = result["dropped_windows"]
    lines += [
        "",
        f"Окон отброшено (ошибка метода, отбрасывается для всех методов): {len(dropped)}.",
    ]
    return "\n".join(lines) + "\n"


def save_results(result: dict, config: dict, out_dir) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    records = result["records"]
    flat = [
        {
            **{k: v for k, v in r.items() if k not in ("path", "p10", "p90")},
            "event_offsets": json.dumps(r["event_offsets"]),
        }
        for r in records
    ]
    if flat:
        with (out / "windows.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
        with gzip.open(out / "paths.csv.gz", "wt", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["split", "account_id", "origin", "method", *[f"d{h}" for h in range(1, 31)]]
            )
            for r in records:
                writer.writerow([r["split"], r["account_id"], r["origin"], r["method"], *r["path"]])
    events = result["manifest"]["events"]
    if events:
        with (out / "events.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(events[0]))
            writer.writeheader()
            writer.writerows(events)
    (out / "event_manifest.json").write_text(
        json.dumps(result["manifest"]["parts"], ensure_ascii=False, indent=2)
    )
    (out / "dropped_windows.json").write_text(
        json.dumps(result["dropped_windows"], ensure_ascii=False, indent=2)
    )
    payload = {
        "config": config,
        "summary": result["summary"],
        "manifest": result["manifest"]["parts"],
    }
    (out / "baseline.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (out / "baseline.md").write_text(baseline_markdown(result, config))
