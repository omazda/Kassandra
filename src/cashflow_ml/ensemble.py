"""X10: ансамбль честных прогнозов с весами по горизонту.

Прогноз остатка = остаток на as_of + сумма взвешенных изменений базовых методов (hgb без
календаря, hgb с календарём — путь продукта, профиль месяца shape_only). У каждого метода свой
вес на каждой корзине горизонта (1-7, 8-14, 15-21, 22-30 дней). Веса неотрицательны и не
больше 1: остаток веса уходит постоянному остатку (затухание к constant). Спецификация
(часть "shape"/"drift") поддерживается для экспериментов, в рецепте X10 — целые изменения.

Веса подбираются только на окнах, чьи цели закончились до срезов выбора: июль-декабрь 2025
(HGB на моделях, обученных до 2025-06-30) и январь 2026 (боевые модели до 2025-12-31).
Артефакт — artifacts/ensemble/weights.json, команда — cashflow build-ensemble.
"""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from cashflow_domain.engine import assemble
from cashflow_domain.money import model_minor

HORIZON = 30
BUCKETS = ((1, 7), (8, 14), (15, 21), (22, 30))
PARTS = ("all", "shape", "drift")
COMPONENTS = ("shape", "hgb_v2", "hgb_cal", "events_cal", "weekday")
WEIGHTS_FILE = Path("ensemble") / "weights.json"
BANK_FILE = Path("ensemble") / "error_bank.json"
FOLD_MODELS = Path("ensemble") / "fold_models"
# Рецепт X10 заморожен до прогона срезов выбора (experiments/registry.json, X10).
RECIPE = {
    "spec": [["hgb_v2", "all"], ["hgb_cal", "all"], ["shape", "all"]],
    "variants": {"traj": 0.0, "bal": 0.5},
    "fold_trained_to": "2025-06-30",
    "fold_step_days": 7,
}
DEFAULT_VARIANT = "bal"


# ---------------------------------------------------------------- базовые изменения


def _closing(as_of: date, values, events) -> np.ndarray:
    return np.asarray([p.closing_balance_minor for p in assemble(as_of, 0, values, events)], float)


def component_deltas(ctx) -> dict:
    """Изменение остатка к as_of по каждому базовому методу, копейки (30 значений).

    ctx — protocol_v3.Context, привязанный к истории <= as_of. Кэшируется в ctx: в протоколе
    несколько вариантов ансамбля делят один расчёт. Ключ "_carrier" — потоки и события
    hgb с календарём (их показывает продукт)."""
    cache = ctx.__dict__.setdefault("_ensemble_cache", {})
    if "deltas" in cache:
        return cache["deltas"]
    from cashflow_ml.features import feature_matrix
    from cashflow_ml.models import load_model
    from cashflow_ml.protocol_v3 import shape_profile
    from cashflow_ml.regularity import predicted_events

    as_of = ctx.as_of
    profile, _ = shape_profile(ctx.net_flow_ex_financing(), ctx.valid_days(), as_of)
    flows, _, scale = ctx.prepared(False)
    meta_v2 = ctx.registry.select(as_of, False)
    cal_flows, components, cal_scale = ctx.prepared(True)
    meta_cal = ctx.registry.select(as_of, True)
    with threadpool_limits(limits=1):
        v2 = load_model(meta_v2["path"]).predict(feature_matrix(flows, as_of, scale)) * scale
        cal = load_model(meta_cal["path"]).predict(feature_matrix(cal_flows, as_of, cal_scale))
    v2, cal = np.maximum(0, v2), np.maximum(0, cal * cal_scale)
    events = predicted_events(components, ctx.account_id, as_of)
    past = flows.tail(90)
    by_dow = past.groupby(past.index.dayofweek).mean()
    weekday = np.asarray(
        [
            by_dow.loc[(as_of + timedelta(days=h)).weekday()].to_numpy()
            if (as_of + timedelta(days=h)).weekday() in by_dow.index
            else past.mean().to_numpy()
            for h in range(1, HORIZON + 1)
        ]
    )
    deltas = {
        "shape": np.asarray(profile, float),
        "hgb_v2": _closing(as_of, v2, []),
        "hgb_cal": _closing(as_of, cal, events),
        "events_cal": _closing(as_of, np.zeros((HORIZON, 3)), events),
        "weekday": _closing(as_of, np.maximum(0, weekday), []),
    }
    cache["deltas"] = deltas
    cache["carrier"] = {
        "calendar": (cal, components, events),
        "plain": (v2, {}, []),
        "scale": scale,
        "versions": (meta_v2["model_version"], meta_cal["model_version"]),
        "trained_to": max(meta_v2["trained_to"], meta_cal["trained_to"]),
    }
    return deltas


# ---------------------------------------------------------------- признаки и комбинация


def feature_paths(deltas: dict, spec: list) -> np.ndarray:
    """(k, 30): для каждой пары (метод, часть) и корзины — путь, ненулевой только в корзине."""
    h = np.arange(1, HORIZON + 1)
    rows = []
    for name, part in spec:
        d = np.asarray(deltas[name], float)
        drift = d[-1] * h / HORIZON
        path = {"all": d, "shape": d - drift, "drift": drift}[part]
        for lo, hi in BUCKETS:
            rows.append(np.where((h >= lo) & (h <= hi), path, 0.0))
    return np.asarray(rows)


def combine(deltas: dict, model: dict) -> np.ndarray:
    """Целочисленное изменение остатка ансамбля по дням (копейки)."""
    x = feature_paths(deltas, [tuple(s) for s in model["spec"]])
    return np.rint(np.asarray(model["theta"], float) @ x).astype(np.int64)


def residual_flows(path_delta, events: list, carrier, as_of: date) -> np.ndarray:
    """Потоки (revenue, other_in, out), с которыми assemble(события) даёт путь ансамбля.

    Берутся потоки несущей модели; расхождение дня с ансамблем добавляется к прочим
    поступлениям (если ансамбль выше) или к списаниям (если ниже), так что B[d] = B[d-1] +
    события + остаток выполняется точно."""
    path_delta = np.asarray(path_delta, dtype=np.int64)
    step = np.diff(np.concatenate([[0], path_delta]))
    event_net = np.zeros(HORIZON, dtype=np.int64)
    for e in events:
        offset = (e.expected_date - as_of).days
        if 1 <= offset <= HORIZON and e.status not in {"cancelled", "paid", "review"}:
            event_net[offset - 1] += (
                e.remaining_minor if e.direction == "in" else -e.remaining_minor
            )
    values = np.asarray([[model_minor(v) for v in row] for row in carrier], dtype=np.int64)
    gap = step - event_net - (values[:, 0] + values[:, 1] - values[:, 2])
    values[:, 1] += np.maximum(gap, 0)
    values[:, 2] += np.maximum(-gap, 0)
    return values.astype(float)


def ensemble_flows(ctx, model: dict, calendar: bool = True):
    """(values, components, events, scale, carrier) ансамбля для продукта и протокола."""
    deltas = component_deltas(ctx)
    carrier = ctx._ensemble_cache["carrier"]
    flows, components, events = carrier["calendar" if calendar else "plain"]
    path = combine(deltas, model)
    values = residual_flows(path, events, flows, ctx.as_of)
    return values, components, events, carrier["scale"], carrier


def protocol_method(model: dict, calendar: bool = True):
    """Forecaster для protocol_v3 — тот же путь, что в продукте (models.predict)."""
    from cashflow_ml.protocol_v3 import Forecast

    def forecaster(transactions, daily, as_of, ctx):
        if not available(model, as_of):
            raise ValueError("Срез раньше конца целей набора подбора весов ансамбля")
        values, _, events, _, _ = ensemble_flows(ctx, model, calendar)
        points = assemble(as_of, ctx.opening_minor, values, events)
        return Forecast([p.closing_balance_minor for p in points], meta={"model": model["version"]})

    return forecaster


def component_method(name: str):
    """Forecaster одного базового изменения (для набора подбора весов)."""
    from cashflow_ml.protocol_v3 import Forecast

    def forecaster(transactions, daily, as_of, ctx):
        delta = component_deltas(ctx)[name]
        return Forecast([int(ctx.opening_minor + round(v)) for v in delta])

    return forecaster


# ---------------------------------------------------------------- подбор весов


def company_weights(groups, opening, target) -> tuple[np.ndarray, np.ndarray]:
    """Веса окон: 1 / сумма ошибки constant компании (траектория и минимум). Так каждая
    компания входит в цель почти как её skill к constant, как в метрике D1."""
    groups = np.asarray(groups)
    err_t = np.abs(target - opening[:, None]).mean(1)
    err_m = np.abs(target.min(1) - opening)
    wt, wm = np.zeros(len(groups)), np.zeros(len(groups))
    for g in np.unique(groups):
        m = groups == g
        if err_t[m].sum() > 0:
            wt[m] = 1 / err_t[m].sum()
        if err_m[m].sum() > 0:
            wm[m] = 1 / err_m[m].sum()
    return wt, wm


def fit_theta(x, y, ymin, wt, wm, lam: float = 0.0, upper: float = 1.0) -> np.ndarray:
    """Веса theta >= 0 (<= upper): сглаженная взвешенная L1 траектории + lam * L1 минимума.

    x: (n, k, 30) признаки, y: (n, 30) фактическое изменение, ymin: (n,) минимум изменения."""
    from scipy.optimize import minimize

    x, y, ymin = (np.asarray(a, float) for a in (x, y, ymin))
    unit = np.abs(y).mean() + 1.0
    eps, sharp = 0.01 * unit, 20.0 / unit

    def objective(theta):
        pred = np.einsum("k,nkh->nh", theta, x)
        r = pred - y
        a = np.sqrt(r * r + eps * eps)
        loss = (a.mean(1) * wt).sum()
        grad = np.einsum("nkh,nh->k", x, r / a / HORIZON * wt[:, None])
        if lam:
            z = -sharp * pred
            soft = np.exp(z - z.max(1, keepdims=True))
            soft /= soft.sum(1, keepdims=True)
            rm = (pred * soft).sum(1) - ymin
            am = np.sqrt(rm * rm + eps * eps)
            loss += lam * (am * wm).sum()
            grad += lam * np.einsum("nkh,nh->k", x, soft * (rm / am * wm)[:, None])
        return loss, grad

    k = x.shape[1]
    result = minimize(
        objective, np.full(k, 0.3), jac=True, method="L-BFGS-B", bounds=[(0.0, upper)] * k
    )
    return np.clip(result.x, 0.0, upper)


def make_model(spec: list, theta, lam: float, fit: dict) -> dict:
    body = {
        "spec": [list(s) for s in spec],
        "buckets": [list(b) for b in BUCKETS],
        "theta": [round(float(t), 6) for t in theta],
        "lam": lam,
        "fit": fit,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:10]
    return {"version": f"ens-{fit.get('fit_to', 'na')}-{digest}", **body}


def describe(model: dict) -> list[dict]:
    """Веса в читаемом виде: метод, часть, корзина, вес."""
    rows, i = [], 0
    for name, part in model["spec"]:
        for lo, hi in model["buckets"]:
            rows.append({"component": name, "part": part, "days": f"{lo}-{hi}",
                         "weight": model["theta"][i]})  # fmt: skip
            i += 1
    return rows


def load(root) -> dict:
    path = Path(root) / WEIGHTS_FILE
    if not path.exists():
        raise ValueError("Нет весов ансамбля: выполните cashflow build-ensemble")
    return json.loads(path.read_text())


def available(model: dict, as_of: date) -> bool:
    """Веса подобраны по окнам с целями до fit_to: раньше этой даты ансамбль недоступен."""
    return str(as_of) >= model["fit"]["available_from"]


# ---------------------------------------------------------------- набор для подбора весов


def collect(data: dict, registry, origins: list, config: dict, n_jobs: int = 2) -> dict:
    """Изменения базовых методов и цель D3 на окнах origins (только для подбора весов).

    registry — реестр моделей, обученных до начала целей этих окон (внеобучающие прогнозы)."""
    from cashflow_ml.protocol_v3 import METHODS, AccountTruth, run_protocol

    methods = {"constant": METHODS["constant"]}
    methods.update({name: component_method(name) for name in COMPONENTS})
    result = run_protocol(
        data, methods, {"calibration": origins}, registry=registry, config=config,
        n_jobs=n_jobs, progress=lambda _: None,
    )  # fmt: skip
    by_window: dict = {}
    for r in result["records"]:
        by_window.setdefault((r["account_id"], r["origin"]), {})[r["method"]] = r
    keys = sorted(by_window)
    truths = {a: AccountTruth(txs, daily, config.get("threshold_minor", 0)) for a, (txs, daily)
              in data.items() if any(k[0] == a for k in keys)}  # fmt: skip
    opening = np.asarray([by_window[k]["constant"]["opening_minor"] for k in keys], float)
    target = np.stack([truths[a].window(date.fromisoformat(o))["target"] for a, o in keys]).astype(
        float
    )
    deltas = {
        name: np.stack([np.asarray(by_window[k][name]["path"], float) for k in keys])
        - opening[:, None]
        for name in COMPONENTS
    }
    return {
        "accounts": np.asarray([k[0] for k in keys]),
        "origins": np.asarray([k[1] for k in keys]),
        "opening": opening,
        "target": target,
        "deltas": deltas,
        "scale": np.asarray([by_window[k]["constant"]["scale_minor"] for k in keys], float),
    }


def merge(*sets: dict) -> dict:
    return {
        key: (
            {n: np.concatenate([s[key][n] for s in sets]) for n in sets[0][key]}
            if key == "deltas"
            else np.concatenate([s[key] for s in sets])
        )
        for key in sets[0]
    }


def fit_on(fitset: dict, spec: list, lam: float, upper: float = 1.0, mask=None) -> np.ndarray:
    """Подобрать theta на наборе (маска — подмножество окон, например для кросс-проверки)."""
    mask = np.ones(len(fitset["opening"]), bool) if mask is None else np.asarray(mask)
    opening, target = fitset["opening"][mask], fitset["target"][mask]
    x = np.stack(
        [feature_paths({n: d[i] for n, d in fitset["deltas"].items()}, spec)
         for i in np.flatnonzero(mask)]
    )  # fmt: skip
    wt, wm = company_weights(fitset["accounts"][mask], opening, target)
    y = target - opening[:, None]
    return fit_theta(x, y, y.min(1), wt, wm, lam, upper)


def predict_on(fitset: dict, spec: list, theta) -> np.ndarray:
    """Остатки ансамбля на окнах набора (копейки, float)."""
    theta = np.asarray(theta, float)
    out = []
    for i in range(len(fitset["opening"])):
        x = feature_paths({n: d[i] for n, d in fitset["deltas"].items()}, spec)
        out.append(fitset["opening"][i] + np.rint(theta @ x))
    return np.asarray(out)


# ---------------------------------------------------------------- сборка артефакта


def fold_origins(fold_to: date, trained_to: date, step: int) -> list[date]:
    out, day = [], fold_to + timedelta(days=1)
    while day + timedelta(days=HORIZON) <= trained_to:
        out.append(day)
        day += timedelta(days=step)
    return out


def build_fitset(data, registry, artifacts, config, p1, digest, n_jobs=2, progress=print):
    """Набор подбора: июль-декабрь 2025 на fold-моделях (до 2025-06-30) + плотный январь 2026
    на боевых моделях. Цели всех окон заканчиваются до первого среза выбора."""
    from cashflow_ml.calibration import dense_calibration_origins
    from cashflow_ml.models import ModelRegistry, train_model

    fold_to = date.fromisoformat(RECIPE["fold_trained_to"])
    trained_to = date.fromisoformat(config["trained_to"])
    fold = ModelRegistry(Path(artifacts) / FOLD_MODELS)
    for calendar in (False, True):
        try:
            fold.select(fold_to, calendar)
        except ValueError:
            meta = train_model(data, fold_to, calendar, fold, digest, p1)
            progress(f"fold-модель {meta['model_version']}: {meta['training_windows']} окон")
    origins = fold_origins(fold_to, trained_to, RECIPE["fold_step_days"])
    part_fold = collect(data, fold, origins, config, n_jobs)
    progress(f"набор июль-декабрь 2025: {len(part_fold['opening'])} окон")
    january = dense_calibration_origins(config)
    part_jan = collect(data, registry, january, config, n_jobs)
    progress(f"набор январь 2026: {len(part_jan['opening'])} окон")
    fitset = merge(part_fold, part_jan)
    fitset["part"] = np.asarray(
        ["fold"] * len(part_fold["opening"]) + ["jan"] * len(part_jan["opening"])
    )
    limit = max(o + timedelta(days=HORIZON) for o in origins + january)
    fitset["meta"] = {
        "fold_trained_to": str(fold_to),
        "fold_origins": [str(origins[0]), str(origins[-1]), len(origins)],
        "january_origins": [str(january[0]), str(january[-1]), len(january)],
        "windows": int(len(fitset["opening"])),
        "companies": int(len(np.unique(fitset["accounts"]))),
        "targets_until": str(limit),
    }
    return fitset


def fit_variant(fitset: dict, variant: str) -> dict:
    lam = RECIPE["variants"][variant]
    spec = [tuple(s) for s in RECIPE["spec"]]
    theta = fit_on(fitset, spec, lam)
    meta = fitset["meta"]
    fit = {
        **meta,
        "variant": variant,
        "fit_to": meta["targets_until"],
        "available_from": meta["targets_until"],
        "objective": "сглаженная L1 траектории с весом компании 1/ошибка constant"
        + (f" + {lam} * L1 минимума" if lam else ""),
    }
    return make_model(spec, theta, lam, fit)


def error_bank(fitset: dict, model: dict) -> list[dict]:
    """Ошибки остатка ансамбля на январских окнах (D3, доля оборота) для вероятности разрыва.
    Окна входят в набор подбора 12 весов, поэтому банк слегка оптимистичен."""
    jan = fitset["part"] == "jan"
    spec = [tuple(s) for s in model["spec"]]
    pred = predict_on({k: v for k, v in fitset.items() if k not in ("meta", "part")}, spec,
                      model["theta"])  # fmt: skip
    bank = []
    for i in np.flatnonzero(jan):
        origin = date.fromisoformat(fitset["origins"][i])
        error = (fitset["target"][i] - pred[i]) / max(fitset["scale"][i], 1.0)
        bank.append(
            {
                "account_id": str(fitset["accounts"][i]),
                "origin": str(origin),
                "available_to": str(origin + timedelta(days=HORIZON)),
                "recipe": f"ensemble|{model['version']}",
                "normalized_balance_error": [round(float(e), 6) for e in error],
            }
        )
    return bank


def write_artifacts(artifacts, model: dict, bank: list[dict]) -> Path:
    path = Path(artifacts) / WEIGHTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, ensure_ascii=False, indent=2))
    (Path(artifacts) / BANK_FILE).write_text(json.dumps(bank))
    return path


# ---------------------------------------------------------------- продукт


def predict(transactions, daily, as_of: date, account_id, registry, calendar=True,
            model_version=None):  # fmt: skip
    """models.predict(model='ensemble'): (values, components, scale, version, trained_to)."""
    from cashflow_ml.features import categories_at
    from cashflow_ml.protocol_v3 import Context

    if not daily:
        raise ValueError("Для ансамбля нужен дневной календарь счёта")
    model = load(Path(registry.root).parent)
    if model_version is not None and model_version != model["version"]:
        raise ValueError("Запрошенная версия модели недоступна на выбранную дату")
    if not available(model, as_of):
        raise ValueError(
            f"Веса ансамбля подобраны по данным до {model['fit']['available_from']}: "
            "на более раннюю дату выберите hgb"
        )
    # Категории на as_of, как в протоколе: поздняя правка категории не меняет прошлый прогноз.
    history = categories_at([t for t in transactions if t["operation_date"] <= str(as_of)], as_of)
    rows = [d for d in daily if d["date"] <= str(as_of)]
    ctx = Context(account_id, as_of, 0, registry, {}).bind(history, rows)
    values, components, _, scale, carrier = ensemble_flows(ctx, model, calendar)
    return values, components, scale, model["version"], date.fromisoformat(carrier["trained_to"])
