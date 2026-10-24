"""X6 / D7: интервалы и вероятность разрыва для модели structural.

Состав:
- split-conformal интервалы накопленной ошибки остатка по горизонту (нормировка оборотом);
- симуляция траекторий: целые траектории ошибок из банка + разброс дат и сумм событий;
- калибровка вероятности (изотоническая или Platt) по метке D2 на калибровочных срезах;
- порог тревоги при бюджете ложных тревог;
- правило статуса: calibrated_on_jan_feb только если надёжность на выборе в пределах ±10 п.п.

Всё, что входит в банк и калибровку, построено по срезам, чьи цели закончились не позже
available_to; на более ранней дате as_of калибровка недоступна (утечки нет).
"""

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

VERSION = "x6-v1"
HORIZON = 30
MONTH_DAYS = 30.4375
FILE = Path("calibration") / "structural.json"
STATUS_OK = "calibrated_on_jan_feb"
STATUS_EXPERIMENTAL = "experimental_uncalibrated"
N_SIMS = 1000
SEED = 42
INTERVAL_ALPHA = 0.2  # P10 / P90
RELIABILITY_TOLERANCE = 0.10
RELIABILITY_MIN_BIN = 30

# Разброс события по источнику: половина окна даты (дни) и относительная сигма суммы.
# Половина окна подбирается по окнам P10..P90 механизмов и реестра на калибровочных срезах;
# сигмы заданы заранее по отчётам X2/X3/X7 (WAPE сумм механизмов 5-25%, ЕНП 34-38%).
DEFAULT_SOURCE_PARAMS = {
    "regular": {"half_days": 1, "sigma": 0.10},
    "tax_calendar": {"half_days": 2, "sigma": 0.35},
    "documents": {"half_days": 7, "sigma": 0.10},
    "other": {"half_days": 1, "sigma": 0.10},
}


# ---------------------------------------------------------------- ошибки и интервалы


def normalized_error(target, pred, scale: float) -> np.ndarray:
    """Накопленная ошибка остатка по горизонту (факт D3 минус прогноз), в долях оборота."""
    return (np.asarray(target, dtype=float) - np.asarray(pred, dtype=float)) / max(scale, 1.0)


def conformal_bands(errors, alpha: float = INTERVAL_ALPHA) -> dict:
    """Split-conformal полосы по горизонту: квантили знаковой нормированной ошибки.

    Уровни с поправкой на конечную выборку: нижний floor((n+1)a/2)/n, верхний ceil((n+1)(1-a/2))/n.
    """
    errors = np.asarray(errors, dtype=float)
    n = len(errors)
    if n < 20:
        raise ValueError("Для конформных полос нужно не меньше 20 калибровочных траекторий")
    low_level = max(0.0, math.floor((n + 1) * alpha / 2) / n)
    high_level = min(1.0, math.ceil((n + 1) * (1 - alpha / 2)) / n)
    return {
        "alpha": alpha,
        "n": n,
        "lo": np.quantile(errors, low_level, axis=0, method="lower").tolist(),
        "hi": np.quantile(errors, high_level, axis=0, method="higher").tolist(),
    }


def apply_bands(central, scale: float, bands: dict) -> tuple[np.ndarray, np.ndarray]:
    central = np.asarray(central, dtype=float)
    lo = central + np.asarray(bands["lo"]) * scale
    hi = central + np.asarray(bands["hi"]) * scale
    return np.minimum(lo, central), np.maximum(hi, central)


# ---------------------------------------------------------------- события и симуляция


def source_key(source: str) -> str:
    return source if source in DEFAULT_SOURCE_PARAMS else "other"


def event_specs(events, as_of: date, params: dict | None = None) -> list[tuple]:
    """События горизонта как (день 1..30, сумма со знаком, полуокно дней, сигма суммы)."""
    params = params or DEFAULT_SOURCE_PARAMS
    out = []
    for e in events:
        if e.known_at > as_of or e.status in {"cancelled", "paid", "review"}:
            continue
        day = (e.expected_date - as_of).days
        amount = e.remaining_minor
        if not 1 <= day <= HORIZON or amount <= 0:
            continue
        p = params.get(source_key(e.source), params["other"])
        out.append((day, amount if e.direction == "in" else -amount, p["half_days"], p["sigma"]))
    return out


def simulate(
    central,
    scale: float,
    bank,
    specs,
    threshold: int = 0,
    *,
    jitter: bool = True,
    n: int = N_SIMS,
    seed: int = SEED,
) -> dict:
    """Симуляция: целая траектория ошибок из банка плюс сдвиги дат и сумм событий.

    Возвращает вероятность min(остаток) < порога, день первого пересечения (медиана по
    пересекающим траекториям) и медианный дефицит среди них."""
    central = np.asarray(central, dtype=float)
    bank = np.asarray(bank, dtype=float)
    if len(bank) == 0:
        raise ValueError("Пустой банк ошибок")
    rng = np.random.default_rng(seed)
    paths = central[None, :] + bank[rng.integers(0, len(bank), size=n)] * scale
    if jitter and specs:
        delta = np.zeros((n, HORIZON + 2))
        for day, amount, half, sigma in specs:
            shift = rng.integers(-half, half + 1, size=n) if half > 0 else np.zeros(n, dtype=int)
            new_day = np.maximum(day + shift, 1)
            mult = np.exp(rng.normal(-sigma * sigma / 2, sigma, size=n)) if sigma > 0 else 1.0
            delta[:, day] -= amount
            delta[np.arange(n), np.minimum(new_day, HORIZON + 1)] += amount * mult
        paths = paths + np.cumsum(delta[:, 1 : HORIZON + 1], axis=1)
    below = paths < threshold
    hit = below.any(axis=1)
    first = np.argmax(below, axis=1) + 1
    deficit = np.maximum(0.0, threshold - paths.min(axis=1))
    return {
        "p": float(hit.mean()),
        "offset": int(np.median(first[hit])) if hit.any() else None,
        "deficit_minor": int(np.median(deficit[hit])) if hit.any() else 0,
    }


# ---------------------------------------------------------------- калибровка


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


class Calibrator:
    """Монотонное отображение сырой вероятности симуляции в калиброванную."""

    def __init__(self, kind: str, params: dict):
        self.kind, self.params = kind, params

    def __call__(self, p):
        p = np.asarray(p, dtype=float)
        if self.kind == "isotonic":
            return np.interp(p, self.params["x"], self.params["y"])
        if self.kind == "platt":
            z = self.params["a"] * _logit(p) + self.params["b"]
            return 1 / (1 + np.exp(-z))
        return p  # identity

    def to_dict(self) -> dict:
        return {"kind": self.kind, **self.params}

    @classmethod
    def from_dict(cls, data: dict) -> "Calibrator":
        data = dict(data)
        return cls(data.pop("kind"), data)


def fit_calibrator(p, y, kind: str) -> Calibrator:
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    if kind == "isotonic":
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y)
        return Calibrator(
            "isotonic",
            {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()},
        )
    if kind == "platt":
        if len(set(y.tolist())) < 2:
            return Calibrator("identity", {})
        lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p)[:, None], y)
        return Calibrator("platt", {"a": float(lr.coef_[0, 0]), "b": float(lr.intercept_[0])})
    raise ValueError(f"Неизвестный калибратор {kind}")


def cross_validated(p, y, groups, kind: str, folds: int = 5) -> np.ndarray:
    """Out-of-fold калиброванная вероятность; фолды по счетам (окна одной компании зависимы)."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    groups = np.asarray(groups)
    names = sorted(set(groups.tolist()))
    fold_of = {g: i % folds for i, g in enumerate(names)}
    index = np.asarray([fold_of[g] for g in groups])
    out = np.zeros(len(p))
    for f in range(folds):
        test = index == f
        if not test.any() or test.all():
            out[test] = p[test]
            continue
        out[test] = fit_calibrator(p[~test], y[~test], kind)(p[test])
    return out


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def pick_alarm_threshold(p, y, open_mask, step_days: float, budget: float) -> float:
    """Наименьший порог p >= t, при котором ложных тревог не больше budget на счёт-месяц.

    Ложная тревога - окно без текущего дефицита, где p >= t, но события D2 нет."""
    p, y, open_mask = np.asarray(p, dtype=float), np.asarray(y), np.asarray(open_mask)
    months = open_mask.sum() * step_days / MONTH_DAYS
    if months <= 0:
        return 1.0
    for t in np.unique(np.concatenate([p[open_mask], [1.0]])):
        false_alarms = np.sum(open_mask & (p >= t) & (y == 0))
        if false_alarms / months <= budget:
            return float(t)
    return 1.0


def reliability_status(reliability: list[dict]) -> dict:
    """Правило D7: в каждой корзине с n >= 30 окон |наблюдаемая частота - средняя p| <= 0.10,
    и таких корзин не меньше двух."""
    checked = [b for b in reliability if b["n"] >= RELIABILITY_MIN_BIN and b["mean_p"] is not None]
    gaps = [abs(b["observed"] - b["mean_p"]) for b in checked]
    ok = len(checked) >= 2 and all(g <= RELIABILITY_TOLERANCE for g in gaps)
    return {
        "ok": bool(ok),
        "bins_checked": len(checked),
        "max_gap": max(gaps) if gaps else None,
        "tolerance": RELIABILITY_TOLERANCE,
        "min_bin_windows": RELIABILITY_MIN_BIN,
    }


# ---------------------------------------------------------------- артефакт и продукт

_CACHE: dict = {}


def load_calibration(root) -> dict | None:
    path = Path(root) / FILE
    if not path.exists():
        return None
    stamp = path.stat().st_mtime_ns
    cached = _CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    data = json.loads(path.read_text())
    for variant in data.get("variants", {}).values():
        variant["_bank"] = np.asarray(variant["bank"], dtype=float)
    _CACHE[str(path)] = (stamp, data)
    return data


def status_for(variant: dict) -> str:
    return STATUS_OK if variant["status"]["ok"] else STATUS_EXPERIMENTAL


def add_calibrated_uncertainty(
    risk,
    points,
    events,
    as_of: date,
    scale: float,
    root,
    docs: bool,
    *,
    scenario: bool = False,
):
    """Вероятность разрыва и P10/P90 для structural по артефакту калибровки.

    Если калибровки нет, она ещё недоступна на as_of или история слишком короткая, риск
    возвращается без изменений (вероятность недоступна)."""
    data = load_calibration(root)
    if data is None or str(as_of) <= data["available_to"]:
        return risk
    variant = data["variants"].get("docs" if docs else "nodocs")
    if variant is None:
        return risk
    central = np.asarray([p.closing_balance_minor for p in points], dtype=float)
    specs = event_specs(events, as_of, data["source_params"])
    sim = simulate(
        central,
        scale,
        variant["_bank"],
        specs,
        risk.threshold_minor,
        jitter=variant["jitter"],
        n=data["simulations"],
    )
    probability = float(Calibrator.from_dict(variant["calibrator"])(sim["p"]))
    lo, hi = apply_bands(central, scale, variant["conformal"])
    for i, point in enumerate(points):
        point.p10_minor, point.p90_minor = int(lo[i]), int(hi[i])
    risk.probability_30d = min(1.0, max(0.0, probability))
    risk.calibration_status = status_for(variant)
    risk.trajectory_count, risk.simulations = variant["bank_size"], data["simulations"]
    risk.probability_source = (
        "Симуляция: целые траектории ошибок structural (калибровка январь 2026, "
        f"{variant['bank_size']} окон)"
        + (" + разброс дат и сумм событий" if variant["jitter"] else "")
        + f"; {variant['calibrator']['kind']}-калибровка по метке D2 на январе-феврале"
    )
    note = (
        f"Статус {risk.calibration_status}: надёжность на срезах выбора "
        + ("в пределах ±10 п.п." if variant["status"]["ok"] else "вне ±10 п.п. или данных мало")
        + ". Окна одной компании зависимы, событий мало. Один счёт не отражает всю ликвидность."
    )
    if scenario:
        risk.probability_source = (
            "Сценарная траектория, та же симуляция. " + risk.probability_source
        )
        note = (
            "Условный сценарий: калибровка выполнена на базовых прогнозах, на сценарных "
            "траекториях не проверялась. " + note
        )
    risk.limitation = note
    return risk


def horizon_end(as_of: date) -> date:
    return as_of + timedelta(days=HORIZON)


# ================================================================ конвейер построения (X6)

CALIBRATION_STEP_DAYS = 2  # плотные калибровочные срезы внутри января (цели до 28 февраля)
VARIANTS = (("docs", "structural", True), ("nodocs", "structural_nodocs", False))


def dense_calibration_origins(config: dict) -> list[date]:
    """Калибровочные срезы X6: от первого до последнего среза p3.json с шагом 2 дня."""
    given = sorted(date.fromisoformat(x) for x in config["splits"]["calibration"]["origins"])
    out, day = [], given[0]
    while day <= given[-1]:
        out.append(day)
        day += timedelta(days=CALIBRATION_STEP_DAYS)
    return out


def prepare_structural(data: dict, companies_path) -> None:
    """Общий реестр документов и сведения о компаниях (как в evaluate_x4 и сервисе)."""
    from cashflow_ml import structural as S

    info = S.load_company_info(companies_path)
    for account_id in data:
        info.setdefault(account_id, {"legal_form": None, "industry": ""})
    industries = {a: v.get("industry", "") for a, v in info.items()}
    S.configure(S.build_universe(data, industries), info)


def collector(docs: bool, work: Path):
    """Метод протокола: structural + запись событий горизонта (источник, окно даты) на диск."""
    from cashflow_ml import structural as S
    from cashflow_ml.protocol_v3 import Forecast

    tag = "docs" if docs else "nodocs"

    def forecaster(transactions, daily, as_of, ctx):
        cache = ctx.__dict__.setdefault("_structural_cache", {})
        result = S.structural_forecast(
            transactions,
            as_of,
            ctx.account_id,
            tax=True,
            docs=docs,
            cache=cache,
            residual="profile",
        )
        parts = S.compute_parts(
            transactions, as_of, ctx.account_id, tax=True, docs=docs, cache=cache
        )
        windows = {}
        for e in parts["mech_events"]:
            if e.window:
                pid = S.mechanism_planned(e, ctx.account_id, as_of, S.event_source(e)).id
                windows[pid] = (e.window[1] - e.window[0]).days / 2
        for e in parts["registry"]:
            if e.window:
                windows[S.registry_planned(e, ctx.account_id).id] = (
                    e.window[1] - e.window[0]
                ).days / 2
        rows = []
        for e in result.events:
            day = (e.expected_date - as_of).days
            if 1 <= day <= HORIZON and e.known_at <= as_of:
                sign = 1 if e.direction == "in" else -1
                rows.append([day, sign * e.remaining_minor, e.source, windows.get(e.id)])
        work.mkdir(parents=True, exist_ok=True)
        (work / f"{tag}__{ctx.account_id}__{as_of}.json").write_text(json.dumps(rows))
        return Forecast(S.balances_of(result, as_of, ctx.opening_minor), meta=result.meta)

    return forecaster


def collect(data: dict, config: dict, registry, work: Path, n_jobs: int = 2, progress=print):
    """Прогон протокола X0 для structural (с реестром и без) на калибровке, выборе и ретро."""
    from cashflow_ml.protocol_v3 import METHODS, expand_origins, run_protocol

    origins = expand_origins(config)
    origins["calibration"] = dense_calibration_origins(config)
    limit = config.get("calibration_targets_until")
    if limit and max(origins["calibration"]) + timedelta(days=HORIZON) > date.fromisoformat(limit):
        raise ValueError("Цели калибровки заходят за calibration_targets_until")
    methods = {"constant": METHODS["constant"]}
    for _key, name, docs in VARIANTS:
        methods[name] = collector(docs, work)
    return run_protocol(
        data,
        methods,
        origins,
        registry=registry,
        config=config,
        n_jobs=n_jobs,
        progress=progress,
    )


def fit_source_params(rows: list[dict]) -> dict:
    """Половина окна даты по источнику: медиана полуокон P10..P90 на калибровочных срезах."""
    params = {k: dict(v) for k, v in DEFAULT_SOURCE_PARAMS.items()}
    halves: dict[str, list[float]] = {}
    for r in rows:
        if r["split"] != "calibration":
            continue
        for _, _, source, half in r["_events"]:
            if half is not None:
                halves.setdefault(source_key(source), []).append(half)
    for key, values in halves.items():
        params[key]["half_days"] = max(1, int(round(float(np.median(values)))))
        params[key]["fitted_n"] = len(values)
    return params


def _alarm_rows(rows, p, threshold, sims):
    """Строки для risk_metrics: тревога по p >= порога; дата и дефицит из симуляции."""
    out = []
    for r, prob, sim in zip(rows, p, sims, strict=True):
        alarm = (not r["current_deficit"]) and prob >= threshold
        offset = sim["offset"] if sim["offset"] is not None else (r["alarm_offset"] or HORIZON)
        out.append(
            {
                "account_id": r["account_id"],
                "origin": r["origin"],
                "current_deficit": r["current_deficit"],
                "event_offsets": r["event_offsets"],
                "first_event_offset": r["first_event_offset"],
                "alarm": bool(alarm),
                "alarm_offset": offset if alarm else None,
                "pred_deficit_minor": sim["deficit_minor"] if alarm else 0,
                "actual_deficit_minor": r["actual_deficit_minor"],
                "p_gap_30d": float(prob),
            }
        )
    return out


def _coverage(rows, bands) -> dict:
    """Покрытие P10..P90 фактической траекторией D3: по дням и по компаниям."""
    inside, by_account = [], {}
    for r in rows:
        central = np.asarray(r["path"], dtype=float)
        lo, hi = apply_bands(central, r["scale_minor"], bands)
        ok = (r["_target"] >= lo) & (r["_target"] <= hi)
        inside.append(ok)
        by_account.setdefault(r["account_id"], []).append(ok.mean())
    inside = np.asarray(inside)
    return {
        "windows": len(rows),
        "companies": len(by_account),
        "nominal": 1 - bands["alpha"],
        "coverage_all_days": float(inside.mean()),
        "coverage_by_company_median": float(np.median([np.mean(v) for v in by_account.values()])),
        "coverage_day7": float(inside[:, 6].mean()),
        "coverage_day14": float(inside[:, 13].mean()),
        "coverage_day30": float(inside[:, 29].mean()),
        "below_p10_share": float(
            np.mean(
                [
                    (
                        r["_target"]
                        < apply_bands(np.asarray(r["path"], dtype=float), r["scale_minor"], bands)[
                            0
                        ]
                    ).mean()
                    for r in rows
                ]
            )
        ),
        "above_p90_share": float(
            np.mean(
                [
                    (
                        r["_target"]
                        > apply_bands(np.asarray(r["path"], dtype=float), r["scale_minor"], bands)[
                            1
                        ]
                    ).mean()
                    for r in rows
                ]
            )
        ),
    }


def analyze(records: list, data: dict, work: Path, config: dict, progress=print) -> dict:
    """Интервалы, симуляция, калибровка и метрики D1 по риску. Возвращает отчёт и артефакт."""
    from cashflow_ml.protocol_v3 import (
        AccountTruth,
        probability_metrics,
        risk_metrics,
        split_step_days,
    )

    threshold = config.get("threshold_minor", 0)
    budget = config.get("alarm_budget_per_account_month", 1.0)
    truths = {a: AccountTruth(t, d, threshold) for a, (t, d) in data.items()}
    # Ложные тревоги считаются как у выбора и ретро (недельные окна): плотные калибровочные
    # срезы (шаг 2 дня) дают то же число окон на единицу времени, поэтому берётся шаг 7 дней.
    steps = {
        "calibration": 7,
        "selection": split_step_days(config, "selection"),
        "retrospective": split_step_days(config, "retrospective"),
    }
    report: dict = {"variants": {}, "steps": steps, "budget": budget}
    artifact: dict = {
        "version": VERSION,
        "recipe": "structural",
        "available_to": config["calibration_targets_until"],
        "simulations": N_SIMS,
        "seed": SEED,
        "interval_alpha": INTERVAL_ALPHA,
        "alarm_budget_per_account_month": budget,
        "variants": {},
    }
    shared_params = None
    for key, method, _ in VARIANTS:
        rows = [r for r in records if r["method"] == method]
        for r in rows:
            window = truths[r["account_id"]].window(date.fromisoformat(r["origin"]))
            r["_target"] = np.asarray(window["target"], dtype=float)
            r["_err"] = normalized_error(r["_target"], r["path"], r["scale_minor"])
            path = work / f"{key}__{r['account_id']}__{r['origin']}.json"
            r["_events"] = json.loads(path.read_text())
        if shared_params is None:
            shared_params = fit_source_params(rows)
        params = shared_params
        cal = [r for r in rows if r["split"] == "calibration"]
        errors = np.asarray([r["_err"] for r in cal])
        cal_accounts = np.asarray([r["account_id"] for r in cal])
        bands = conformal_bands(errors)
        progress(f"[{key}] калибровка: {len(cal)} окон; симуляция {len(rows)} окон")
        # симуляции: для калибровочных окон банк без своего счёта (leave-one-company-out)
        sims = {False: [], True: []}
        for r in rows:
            central = np.asarray(r["path"], dtype=float)
            if r["split"] == "calibration":
                bank = errors[cal_accounts != r["account_id"]]
            else:
                bank = errors
            specs = [
                (d, a, params[source_key(s)]["half_days"], params[source_key(s)]["sigma"])
                for d, a, s, _ in r["_events"]
            ]
            for jitter in (False, True):
                sims[jitter].append(
                    simulate(central, r["scale_minor"], bank, specs, threshold, jitter=jitter)
                )
        idx = {s: [i for i, r in enumerate(rows) if r["split"] == s] for s in steps}
        cal_open = [i for i in idx["calibration"] if not rows[i]["current_deficit"]]
        y_cal = np.asarray([bool(rows[i]["event_offsets"]) for i in cal_open], dtype=float)
        groups = [rows[i]["account_id"] for i in cal_open]
        base_rate = float(y_cal.mean())
        variants_out: dict = {}
        final_cands = {}
        for jitter in (False, True):
            name = "events" if jitter else "bank"
            p_raw = np.asarray([s["p"] for s in sims[jitter]])
            oof = {
                kind: cross_validated(p_raw[cal_open], y_cal, groups, kind)
                for kind in ("isotonic", "platt")
            }
            cv_brier = {kind: brier(oof[kind], y_cal) for kind in oof}
            cv_brier["raw"] = brier(p_raw[cal_open], y_cal)
            kind = min(("isotonic", "platt"), key=lambda k: (cv_brier[k], k))
            calibrator = fit_calibrator(p_raw[cal_open], y_cal, kind)
            p_cal = calibrator(p_raw)
            p_cal[cal_open] = oof[kind]  # на калибровке только out-of-fold
            thr_raw = pick_alarm_threshold(
                p_raw[cal_open], y_cal, np.ones(len(cal_open), bool), steps["calibration"], budget
            )
            thr_cal = pick_alarm_threshold(
                p_cal[cal_open], y_cal, np.ones(len(cal_open), bool), steps["calibration"], budget
            )
            budgets = {}
            for b in (0.25, 0.5, 1.0):
                budgets[str(b)] = {
                    "raw": pick_alarm_threshold(
                        p_raw[cal_open],
                        y_cal,
                        np.ones(len(cal_open), bool),
                        steps["calibration"],
                        b,
                    ),
                    "calibrated": pick_alarm_threshold(
                        p_cal[cal_open],
                        y_cal,
                        np.ones(len(cal_open), bool),
                        steps["calibration"],
                        b,
                    ),
                }
            for label, p, thr in (("raw", p_raw, thr_raw), ("calibrated", p_cal, thr_cal)):
                entry = {
                    "threshold": thr,
                    "thresholds_by_budget": {b: v[label] for b, v in budgets.items()},
                }
                for split, ids in idx.items():
                    sub = [rows[i] for i in ids]
                    alarm_rows = _alarm_rows(sub, p[ids], thr, [sims[jitter][i] for i in ids])
                    entry[split] = {
                        "risk": risk_metrics(alarm_rows, config, steps[split]),
                        "probability": probability_metrics(alarm_rows, base_rate),
                    }
                    entry[split]["risk_by_budget"] = {}
                    for b, tv in budgets.items():
                        ar = _alarm_rows(sub, p[ids], tv[label], [sims[jitter][i] for i in ids])
                        m = risk_metrics(ar, config, steps[split])
                        entry[split]["risk_by_budget"][b] = {
                            k: m[k]
                            for k in (
                                "recall_7d",
                                "events_scorable",
                                "events_detected",
                                "false_alarms_per_account_month",
                                "date_within_tolerance",
                                "deficit_mae_rub",
                            )
                        }
                variants_out[f"sim_{label}_{name}"] = entry
            final_cands[name] = {
                "cv_brier": cv_brier,
                "calibrator": calibrator,
                "kind": kind,
                "thr": thr_cal,
                "p_cal": p_cal,
            }
        # метод 1: порог на центральной траектории (тревога структурной модели)
        central_entry = {}
        for split, ids in idx.items():
            sub = []
            for i in ids:
                r = rows[i]
                sub.append({**r, "p_gap_30d": None})
            central_entry[split] = {
                "risk": risk_metrics(sub, config, steps[split]),
                "probability": None,
            }
        variants_out["central_threshold"] = central_entry
        best = min(final_cands, key=lambda n: final_cands[n]["cv_brier"][final_cands[n]["kind"]])
        chosen = final_cands[best]
        selection = chosen["p_cal"][idx["selection"]]
        sel_prob = variants_out[f"sim_calibrated_{best}"]["selection"]["probability"]
        status = reliability_status(sel_prob["reliability"])
        coverage = {
            split: _coverage([rows[i] for i in idx[split]], bands)
            for split in ("selection", "retrospective")
        }
        report["variants"][key] = {
            "method": method,
            "windows": {s: len(v) for s, v in idx.items()},
            "calibration_base_rate": base_rate,
            "calibration_open_windows": len(cal_open),
            "calibration_events_windows": int(y_cal.sum()),
            "cv_brier": {n: final_cands[n]["cv_brier"] for n in final_cands},
            "calibrator_kind": {n: final_cands[n]["kind"] for n in final_cands},
            "chosen_simulation": best,
            "reliability_status": status,
            "coverage": coverage,
            "methods": variants_out,
            "selection_mean_p": float(selection.mean()),
        }
        artifact["variants"][key] = {
            "bank": np.round(errors, 5).tolist(),
            "bank_size": len(errors),
            "conformal": bands,
            "jitter": best == "events",
            "simulation_variant": best,
            "calibrator": chosen["calibrator"].to_dict(),
            "alarm_threshold": chosen["thr"],
            "status": status,
            "calibration_base_rate": base_rate,
        }
    artifact["source_params"] = shared_params
    report["source_params"] = shared_params
    return {"report": report, "artifact": artifact}


def save_artifact(artifact: dict, root) -> Path:
    path = Path(root) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, ensure_ascii=False, separators=(",", ":")))
    _CACHE.pop(str(path), None)
    return path


def build(svc, config: dict, root, work: Path, n_jobs: int = 2, progress=print) -> dict:
    """Полный цикл: прогон, анализ, запись артефакта калибровки в root/calibration/."""
    import os
    import shutil

    from cashflow_services.cli import account_data

    data = account_data(svc)
    prepare_structural(data, os.getenv("CASHFLOW_COMPANIES", "dataset/companies.csv"))
    shutil.rmtree(work, ignore_errors=True)
    result = collect(data, config, svc.registry, work, n_jobs, progress)
    out = analyze(result["records"], data, work, config, progress)
    out["records"] = result["records"]
    out["dropped_windows"] = result["dropped_windows"]
    save_artifact(out["artifact"], root)
    return out
