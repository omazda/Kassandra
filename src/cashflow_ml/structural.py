"""Структурная модель (docs/ml_protocol.md: D3, D5, D6, X4).

Прогноз из пяти частей, у каждой будущей операции один владелец:

1. события механизмов X2 (зарплата, фиксированные платежи, кредиты, эквайринг, СБП, инкассация);
2. налоговый календарь X7 (ЕНП; владелец группы ``tax|out``, а если режим неизвестен, события X2);
3. реестр документов X3 (оплаты счетов, УПД, актов и расчёты по договорам);
4. остаточный поток: профиль по дням недели по истории после вычитания операций, которыми
   владеют части 1-3;
5. затухающий тренд остатка с коэффициентом alpha (подбирается на калибровочных срезах X0).

Правило владения зависит только от самой операции, поэтому одинаково для истории (вычесть из
остатка) и для будущего: группа механизма (model.owns) или документ с датой/договор с
предоплатой. Операция документа покрыта реестром на горизонте h, если лаг оплаты (дата операции
минус дата документа) не меньше h: документ уже выставлен на as_of. Короче лаг h не покрывается,
этот поток остаётся в остатке. Цель D3: остаток без нового внешнего финансирования.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor
from threadpoolctl import threadpool_limits

from cashflow_domain.engine import assemble
from cashflow_domain.schemas import CATEGORY_LABELS, PlannedEvent, is_financing, is_revenue
from cashflow_ml import cold_start
from cashflow_ml.calendar_ru import is_working_day
from cashflow_ml.documents import (
    CONTRACT_CATEGORIES,
    DOC_CATEGORIES,
    DocumentUniverse,
    build_contract_cycles,
    extract_payments,
    parse_purpose,
    party_key,
)
from cashflow_ml.features import (
    FEATURE_VERSION,
    ExtraContext,
    categories_at,
    feature_matrix,
    feature_matrix_ext,
    industry_column,
)
from cashflow_ml.mechanisms import ExpectedEvent, MechanismModel, detect_mechanisms, group_key
from cashflow_ml.tax_calendar import build_tax_calendar

HORIZON = 30
WINDOW = 90  # окно профиля остатка, как у weekday
TREND_SHORT = 28
PHI = 0.9  # затухание дневного наклона тренда
ALPHA_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
DEFAULT_ALPHA = 0.0  # переопределяется записью experiments/results/v3/x4/alpha.json
RECIPE_VERSION = "structural-v1"
STREAMS = 3  # revenue, other_in, out

ALPHA_FILE = Path(__file__).resolve().parents[2] / "experiments/results/v3/x4/alpha.json"
RESIDUAL_FILE = Path(__file__).resolve().parents[2] / "experiments/results/v3/x5/decision.json"
RESIDUAL_HORIZONS = (7, 14, 30)  # E05: прямой прогноз накопленного остатка


def default_alpha() -> float:
    """Alpha, выбранная на калибровочных срезах (alpha.json); иначе DEFAULT_ALPHA."""
    try:
        return float(json.loads(ALPHA_FILE.read_text())["alpha"])
    except (OSError, KeyError, ValueError):
        return DEFAULT_ALPHA


def residual_decision() -> dict:
    """Решение X5 (decision.json): {"residual": "profile" | <рецепт GBM>, "blend": w}.

    Нет файла - профиль по дням недели (поведение X4)."""
    try:
        data = json.loads(RESIDUAL_FILE.read_text())
        return {"residual": str(data["residual"]), "blend": float(data.get("blend", 1.0))}
    except (OSError, KeyError, ValueError):
        return {"residual": "profile", "blend": 1.0}


# ---------------------------------------------------------------- общие данные (реестр, компании)

_UNIVERSE: DocumentUniverse | None = None
_INFO: dict[str, dict] = {}
_ARCHIVE_END: dict[str, date] = {}


def build_universe(
    account_data: dict, industries: dict | None = None, config=None
) -> DocumentUniverse:
    """Реестр документов по всему архиву (ретро-реестр D6). Будущее используется только для
    сумм документов и факта их существования; даты задержек берутся по оплатам <= as_of."""
    payments = []
    for account, (txs, _) in sorted(account_data.items()):
        payments += extract_payments(txs, account)
    return DocumentUniverse(payments, industries or {}, config)


def configure(universe: DocumentUniverse | None = None, info: dict | None = None) -> None:
    """Задать общий реестр документов и сведения о компаниях {id: {legal_form, industry}}.

    Вызывать до run_protocol: процессы раннера наследуют состояние через fork.
    """
    global _UNIVERSE, _INFO, _ARCHIVE_END
    _UNIVERSE, _INFO = universe, info or {}
    _ARCHIVE_END = {}
    if universe is not None:
        for p in universe.payments:
            if p.op_date > _ARCHIVE_END.get(p.account_id, date.min):
                _ARCHIVE_END[p.account_id] = p.op_date


def load_company_info(path) -> dict:
    """{company_id: {legal_form, industry}} из companies.csv (UTF-8 BOM, ';'); нет файла - {}."""
    import csv

    try:
        with open(path, encoding="utf-8-sig") as f:
            return {
                r["company_id"]: {"legal_form": r["legal_form"], "industry": r["industry"]}
                for r in csv.DictReader(f, delimiter=";")
            }
    except OSError:
        return {}


def legal_form_from_name(name: str | None) -> str | None:
    text = (name or "").strip().upper()
    return (
        "ИП" if text.startswith("ИП") else "ООО" if text.startswith(("ООО", "АО", "ПАО")) else None
    )


# ---------------------------------------------------------------- принадлежность операций


@lru_cache(maxsize=300_000)
def _parsed(purpose: str):
    return parse_purpose(purpose)


def doc_lags(history: list[dict], as_of: date, since: date) -> dict[str, int]:
    """operation_id -> лаг (дней) для операций документов и расчётов по договорам.

    Только по операциям <= as_of. Документ: лаг = дата операции - дата документа. Расчёт по
    договору (есть предоплата): лаг = дата расчёта - дата предоплаты. Платежи документов,
    которые вернули деньги (stage=return), владельцем-реестром не считаются.
    """
    lags: dict[str, int] = {}
    for tx in history:
        if tx["operation_date"] < since.isoformat():
            continue
        parsed = _parsed(tx.get("payment_purpose") or "")
        if parsed is None or parsed.stage == "return":
            continue
        ref = parsed.document
        if ref is not None and tx["category"] in DOC_CATEGORIES.get(tx["direction"], ()):
            op_date = date.fromisoformat(tx["operation_date"])
            lags[tx["operation_id"]] = max(0, (op_date - ref.doc_date).days)
    # расчёты по договорам: пары предоплата -> расчёт ищем по более длинной истории
    contract_from = (as_of - timedelta(days=240)).isoformat()
    payments = extract_payments([t for t in history if t["operation_date"] >= contract_from])
    cycles = build_contract_cycles(payments)
    settled = {}
    for c in cycles:
        if c.settle is not None and c.settle[0] <= as_of:
            settled[(c.settle[0], c.settle[1], c.direction, c.inn)] = (
                c.settle[0] - c.prepay[0]
            ).days
    if settled:
        for tx in history:
            if tx["operation_date"] < since.isoformat():
                continue
            key = (
                date.fromisoformat(tx["operation_date"]),
                tx["amount_minor"],
                tx["direction"],
                party_key(tx),
            )
            if key in settled and tx["category"] in CONTRACT_CATEGORIES.get(tx["direction"], ()):
                parsed = _parsed(tx.get("payment_purpose") or "")
                if parsed is not None and parsed.stage == "settlement":
                    lags.setdefault(tx["operation_id"], max(0, settled[key]))
    return lags


def owner_of(tx: dict, model: MechanismModel | None, lags: dict | None) -> str:
    """Владелец операции: financing | mechanism | documents | residual."""
    if is_financing(tx["category"], tx["direction"]):
        return "financing"
    if model is not None and model.owns(tx):
        return "mechanism"
    if lags is not None and tx["operation_id"] in lags:
        return "documents"
    return "residual"


# ---------------------------------------------------------------- остаточный поток


def _stream(tx: dict) -> int:
    if tx["direction"] == "out":
        return 2
    return 0 if is_revenue(tx["category"], "in") else 1


@dataclass
class Residual:
    """Профиль остатка: values (30, 3) в копейках и сводка тренда."""

    values: np.ndarray
    trend_slope: float  # копеек в день: средний чистый остаток за 28 дней минус за 90
    base_net_90: float
    owned: dict = field(default_factory=dict)  # число операций окна по владельцам


def residual_profile(
    history: list[dict],
    as_of: date,
    model: MechanismModel | None,
    lags: dict | None,
    window: int = WINDOW,
) -> Residual:
    """Остаток: операции окна без финансирования, без механизмов и (если lags) без документов.

    Профиль по дням недели считается по рабочим дням окна; нерабочие будущие дни дают 0. Поток
    документов с лагом >= h на горизонте h принадлежит реестру и в остаток не входит; с лагом < h
    входит: такой документ на as_of ещё не выставлен.
    """
    start = as_of - timedelta(days=window - 1)
    wd_count = defaultdict(int)
    for k in range(window):
        d = start + timedelta(days=k)
        if is_working_day(d):
            wd_count[d.weekday()] += 1
    base = np.zeros((7, STREAMS))
    doc_ops: list[tuple[int, int, int, int]] = []  # (dow, stream, amount, lag)
    owned = defaultdict(int)
    short_net = long_net = 0.0
    short_start = as_of - timedelta(days=TREND_SHORT - 1)
    for tx in history:
        day = date.fromisoformat(tx["operation_date"])
        if day < start or day > as_of:
            continue
        who = owner_of(tx, model, lags)
        owned[who] += 1
        if who in ("financing", "mechanism"):
            continue
        stream = _stream(tx)
        if who == "documents":
            doc_ops.append((day.weekday(), stream, tx["amount_minor"], lags[tx["operation_id"]]))
            continue
        base[day.weekday(), stream] += tx["amount_minor"]
        signed = -tx["amount_minor"] if stream == 2 else tx["amount_minor"]
        long_net += signed
        if day >= short_start:
            short_net += signed
    overall = base.sum(axis=0) / max(1, sum(wd_count.values()))
    values = np.zeros((HORIZON, STREAMS))
    doc_arr = np.asarray(doc_ops, dtype=np.int64).reshape(-1, 4)
    for h in range(1, HORIZON + 1):
        day = as_of + timedelta(days=h)
        if not is_working_day(day):
            continue
        w = day.weekday()
        n = wd_count.get(w, 0)
        row = (base[w] / n if n else overall).copy()
        if len(doc_arr):
            sel = doc_arr[(doc_arr[:, 0] == w) & (doc_arr[:, 3] < h)]
            for stream, amount in zip(sel[:, 1], sel[:, 2], strict=True):
                row[stream] += amount / max(1, n)
        values[h - 1] = row
    slope = short_net / TREND_SHORT - long_net / window
    return Residual(values, float(slope), long_net / window, dict(owned))


def apply_trend(values: np.ndarray, slope: float, alpha: float) -> np.ndarray:
    """Затухающий тренд: к чистому дневному потоку добавляется alpha * slope * PHI**h."""
    out = values.copy()
    if not alpha or not slope:
        return out
    for h in range(1, HORIZON + 1):
        step = alpha * slope * PHI**h
        if step >= 0:
            out[h - 1, 0] += step
        else:
            out[h - 1, 2] += -step
    return out


# ---------------------------------------------------------------- события -> PlannedEvent


def _party(value: str) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    return (value, None) if value.isdigit() else (None, value[:240])


def mechanism_planned(
    ev: ExpectedEvent, account_id: str, as_of: date, source: str = "regular"
) -> PlannedEvent:
    inn, name = _party(ev.counterparty)
    return PlannedEvent(
        id=f"mech:{account_id}:{ev.component_id}:{ev.date}",
        account_id=account_id,
        component_id=ev.component_id,
        category=ev.category,
        amount_minor=ev.amount_minor,
        direction=ev.direction,
        due_date=ev.date,
        expected_date=ev.date,
        known_at=as_of,
        source=source,
        status="assumption",
        label=f"{CATEGORY_LABELS[ev.category]} · {SOURCE_TITLES[source]}",
        counterparty_inn=inn,
        counterparty_name=name,
    )


SOURCE_TITLES = {
    "regular": "механизм по истории",
    "tax_calendar": "налоговый календарь",
    "documents": "реестр документов",
}


def event_source(ev: ExpectedEvent) -> str:
    return "tax_calendar" if ev.mechanism == "tax_calendar" else "regular"


def registry_planned(ev, account_id: str) -> PlannedEvent:
    label = f"{CATEGORY_LABELS[ev.category]} · {ev.document_ref} · {ev.counterparty_name}"
    key = ev.counterparty_inn or ""
    inn = key if key.isdigit() and len(key) <= 32 else None  # без ИНН ключ контрагента - имя
    cid = ev.component_id or "doc"
    if len(cid) > 100:  # дата должна остаться в конце id: из неё строится замещение
        cid = "doc:" + hashlib.sha1(cid.encode()).hexdigest()[:16]
    return PlannedEvent(
        id=f"doc:{account_id}:{cid}:{ev.date}",
        account_id=account_id,
        component_id=cid,
        category=ev.category,
        amount_minor=ev.amount_minor,
        direction=ev.direction,
        due_date=ev.date,
        expected_date=ev.date,
        known_at=ev.known_at,
        source="documents",
        status="assumption",
        label=label[:240],
        counterparty_inn=inn,
        counterparty_name=(ev.counterparty_name or (None if inn else key) or None),
        document_ref=ev.document_ref[:160],
    )


# ---------------------------------------------------------------- прогноз


class StructuralComponents(dict):
    """components для service/models.predict: component_id -> источник; .events - PlannedEvent."""

    events: list
    meta: dict


@dataclass
class StructuralResult:
    values: np.ndarray  # остаток + тренд, (30, 3) копейки
    events: list  # PlannedEvent: механизмы, налоги, документы
    scale: float
    meta: dict


def history_scale(history: list[dict], as_of: date) -> float:
    start = date.fromisoformat(min(t["operation_date"] for t in history))
    since = max(start, as_of - timedelta(days=89))
    total = sum(t["amount_minor"] for t in history if t["operation_date"] >= since.isoformat())
    return max(100.0, total / ((as_of - since).days + 1))


def docs_available(account_id: str, as_of: date) -> bool:
    """Реестр есть, если в архиве счёта известны оплаты после as_of (ретро-реестр D6)."""
    return _UNIVERSE is not None and _ARCHIVE_END.get(account_id, date.min) > as_of


def compute_parts(
    history: list[dict],
    as_of: date,
    account_id: str,
    *,
    tax: bool,
    docs: bool,
    legal_form: str | None = None,
    cache: dict | None = None,
) -> dict:
    """Общие части для вариантов модели: механизмы, документы, остаток. Кэш на срез."""
    cache = cache if cache is not None else {}
    docs = bool(docs and docs_available(account_id, as_of))
    mech_key = ("mech", tax)
    if mech_key not in cache:
        cal = build_tax_calendar(history, as_of, legal_form) if tax else None
        model = detect_mechanisms(history, as_of, tax_calendar=cal)
        cache[mech_key] = (model, model.events(HORIZON), cal)
    model, mech_events, cal = cache[mech_key]
    if docs and "lags" not in cache:
        cache["lags"] = doc_lags(history, as_of, as_of - timedelta(days=WINDOW - 1))
    lags = cache.get("lags") if docs else None
    res_key = ("resid", tax, docs)
    if res_key not in cache:
        cache[res_key] = residual_profile(history, as_of, model, lags)
    registry = []
    if docs:
        if "registry" not in cache:
            cache["registry"] = _UNIVERSE.registry(account_id, as_of, HORIZON)
        registry = cache["registry"]
    return {
        "model": model,
        "mech_events": mech_events,
        "calendar": cal,
        "registry": registry,
        "residual": cache[res_key],
        "docs": docs,
    }


def structural_forecast(
    history: list[dict],
    as_of: date,
    account_id: str,
    *,
    tax: bool = True,
    docs: bool = True,
    alpha: float | None = None,
    legal_form: str | None = None,
    cache: dict | None = None,
    residual: str | None = None,
    models_root=None,
    blend: float | None = None,
    cold: bool = True,
    refuse: bool = False,
) -> StructuralResult:
    """Структурный прогноз по истории <= as_of.

    Маршрутизация по длине истории (X8): 7-89 дней - отраслевой prior без событий (cold=True);
    меньше 7 дней - при refuse=True ColdStartRefusal с объяснением, иначе обычная модель, а в
    meta['confidence'] стоит refuse=True и текст ограничения (продукт сам решает, показать отказ);
    дальше обычная модель. Во всех случаях meta['confidence'] -
    уровень доверия и текст ограничения для продукта.

    residual: "profile" (дни недели), рецепт GBM (ключ GBM_CONFIGS, с суффиксом ":cum" для E05)
    или None - решение X5 из decision.json. GBM нужна обученная gres-* в models_root и реестр
    документов на as_of (иначе остаток считается профилем, это пишется в meta)."""
    history = [t for t in history if t["operation_date"] <= as_of.isoformat()]
    if not history:
        raise ValueError("Нет операций на выбранную дату")
    confidence = cold_start.assess(cold_start.history_days(history, as_of))
    if refuse and confidence.refuse:
        raise cold_start.ColdStartRefusal(confidence)
    if cold and not confidence.refuse and confidence.history_days < cold_start.COLD_BELOW:
        industry = (_INFO.get(account_id) or {}).get("industry")
        cf = cold_start.cold_forecast(history, as_of, account_id, industry)
        meta = {
            "alpha": 0.0,
            "residual": "cold-prior",
            "residual_model": None,
            "events_by_source": {},
            "documents_registry": False,
            "tax_regime": None,
            "operations_by_owner": {},
            "cold_start": cf.meta(),
            "confidence": confidence.to_dict(),
        }
        return StructuralResult(cf.values, [], history_scale(history, as_of), meta)
    alpha = default_alpha() if alpha is None else alpha
    legal_form = legal_form or (_INFO.get(account_id) or {}).get("legal_form")
    parts = compute_parts(
        history, as_of, account_id, tax=tax, docs=docs, legal_form=legal_form, cache=cache
    )
    events = [
        mechanism_planned(e, account_id, as_of, event_source(e))
        for e in parts["mech_events"]
        if as_of < e.date <= as_of + timedelta(days=HORIZON) and e.amount_minor > 0
    ]
    events += [
        registry_planned(e, account_id)
        for e in parts["registry"]
        if as_of < e.date <= as_of + timedelta(days=HORIZON) and e.amount_minor > 0
    ]
    profile = parts["residual"]
    values = apply_trend(profile.values, profile.trend_slope, alpha)
    decision = residual_decision()
    kind = decision["residual"] if residual is None else residual
    weight = (decision["blend"] if residual is None else 1.0) if blend is None else blend
    used, gbm_version = "profile", None
    if kind != "profile" and models_root is not None and parts["docs"]:
        try:
            from cashflow_ml.models import ModelRegistry

            meta = ModelRegistry(models_root).select_residual(as_of, kind)
        except ValueError:
            meta = None
        if meta is not None:
            gbm = gbm_residual_values(history, as_of, account_id, meta, parts["registry"], cache)
            values = weight * gbm + (1 - weight) * profile.values
            used, gbm_version = kind, meta["model_version"]
    residual = profile
    by_source = defaultdict(int)
    for e in events:
        by_source[e.source] += 1
    meta = {
        "alpha": alpha,
        "residual": used,
        "residual_model": gbm_version,
        "events_by_source": dict(by_source),
        "documents_registry": parts["docs"],
        "tax_regime": parts["calendar"].regime.regime if parts["calendar"] else None,
        "operations_by_owner": residual.owned,
        "trend_slope_minor_per_day": residual.trend_slope,
        "confidence": confidence.to_dict(),
    }
    return StructuralResult(values, events, history_scale(history, as_of), meta)


def balances_of(result: StructuralResult, as_of: date, opening_minor: int) -> list[int]:
    """Остатки 30 дней: события + остаток через engine.assemble (как в продукте)."""
    points = assemble(as_of, opening_minor, np.maximum(0, result.values), result.events)
    return [p.closing_balance_minor for p in points]


def model_version(alpha: float | None = None, residual: str = "profile") -> str:
    base = f"{RECIPE_VERSION}-a{default_alpha() if alpha is None else alpha:g}"
    return base if residual == "profile" else f"{base}-{residual.replace(':', '-')}"


def predict_structural(
    transactions: list[dict],
    as_of: date,
    account_id: str,
    legal_form: str | None = None,
    tax: bool = True,
    docs: bool = True,
    models_root=None,
):
    """Вход для models.predict: (values, components, scale, version, trained_to)."""
    result = structural_forecast(
        transactions,
        as_of,
        account_id,
        tax=tax,
        docs=docs,
        legal_form=legal_form,
        models_root=models_root,
    )
    components = StructuralComponents({e.component_id: e.source for e in result.events})
    components.events, components.meta = list(result.events), result.meta
    return (
        np.maximum(0, result.values),
        components,
        result.scale,
        model_version(result.meta["alpha"], result.meta["residual"]),
        None,
    )


# ---------------------------------------------------------------- методы протокола X0


def protocol_method(
    *,
    tax: bool,
    docs: bool,
    alpha: float | None = None,
    residual: str | None = "profile",
    blend: float | None = None,
    cold: bool = True,
):
    """Forecaster для protocol_v3: forecaster(transactions, daily, as_of, ctx) -> Forecast."""
    from cashflow_ml.protocol_v3 import Forecast

    def forecaster(transactions, daily, as_of, ctx):
        cache = ctx.__dict__.setdefault("_structural_cache", {})
        result = structural_forecast(
            transactions,
            as_of,
            ctx.account_id,
            tax=tax,
            docs=docs,
            alpha=alpha,
            cache=cache,
            residual=residual,
            models_root=ctx.registry.root,
            blend=blend,
            cold=cold,
        )
        balances = balances_of(result, as_of, ctx.opening_minor)
        interval = result.meta.get("cold_start", {}).get("interval")
        if interval is None:
            return Forecast(balances, meta=result.meta)
        low, high = cold_start.interval_bounds(balances, interval["half_width_minor"])
        return Forecast(balances, p10_minor=low, p90_minor=high, meta=result.meta)

    return forecaster


# ---------------------------------------------------------------- HGB остатка (E03: C и D)


def residual_flows(
    txs: list[dict], start: date, end: date, model: MechanismModel | None
) -> np.ndarray:
    """Дневные потоки (revenue, other_in, out) без финансирования и без операций механизмов."""
    n = (end - start).days + 1
    values = np.zeros((n, STREAMS))
    for tx in txs:
        day = date.fromisoformat(tx["operation_date"])
        if not (start <= day <= end) or is_financing(tx["category"], tx["direction"]):
            continue
        if model is not None and model.owns(tx):
            continue
        values[(day - start).days, _stream(tx)] += tx["amount_minor"]
    return values


def _frame(values: np.ndarray, start: date):
    import pandas as pd

    from cashflow_ml.features import STREAMS as NAMES

    return pd.DataFrame(values, index=pd.date_range(start, periods=len(values)), columns=NAMES)


def hgb_inputs(history: list[dict], as_of: date, variant: str):
    """(flows, scale, mechanism model) для HGB остатка. variant: full | mech."""
    start = date.fromisoformat(min(t["operation_date"] for t in history))
    model = detect_mechanisms(history, as_of) if variant == "mech" else None
    flows = _frame(residual_flows(history, start, as_of, model), start)
    return flows, history_scale(history, as_of), model


def train_residual_hgb(
    account_data: dict, trained_to: date, variant: str, root, config: dict
) -> dict:
    """Обучить HGB остатка по D3-потокам. full: без вычитания механизмов (контроль 2x2),
    mech: из потока вычтены операции механизмов на каждом origin."""
    from cashflow_ml.models import training_origins

    features, targets, manifest = [], [], []
    for account_id, (txs, daily) in sorted(account_data.items()):
        if not daily:
            continue
        valid = {d["date"]: d["valid"] for d in daily}
        first = date.fromisoformat(daily[0]["date"])
        last = min(trained_to, date.fromisoformat(daily[-1]["date"]))
        for origin in training_origins(first, last):
            if not all(valid.get(str(origin + timedelta(days=h)), False) for h in range(-89, 31)):
                continue
            past = categories_at([t for t in txs if t["operation_date"] <= str(origin)], origin)
            flows, scale, model = hgb_inputs(past, origin, variant)
            future_txs = categories_at(
                [
                    t
                    for t in txs
                    if str(origin) < t["operation_date"] <= str(origin + timedelta(days=30))
                ],
                origin,
            )
            future = residual_flows(
                future_txs, origin + timedelta(days=1), origin + timedelta(days=30), model
            )
            features.append(feature_matrix(flows, origin, scale))
            targets.append(future / scale)
            manifest.append({"account_id": account_id, "origin": str(origin)})
    if not features:
        raise ValueError("Нет пригодных обучающих окон")
    x, y = np.concatenate(features), np.concatenate(targets)
    params = config["hgb"]
    estimator = MultiOutputRegressor(HistGradientBoostingRegressor(**params, random_state=42))
    with threadpool_limits(limits=1):
        estimator.fit(x, y)
    recipe = {
        "trained_to": str(trained_to),
        "variant": variant,
        "features": FEATURE_VERSION,
        "parameters": params,
        "seed": 42,
        "training_windows": len(manifest),
        "target": "D3 streams without financing; variant mech also without mechanism-owned ops",
    }
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:10]
    version = f"sres-{trained_to}-{variant}-{digest}"
    folder = Path(root) / version
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(estimator, folder / "model.joblib")
    meta = {**recipe, "model_version": version}
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2))
    return meta


def select_residual_hgb(root, as_of: date, variant: str) -> dict:
    choices = []
    for path in Path(root).glob("sres-*/metadata.json"):
        meta = json.loads(path.read_text())
        if meta["variant"] == variant and meta["trained_to"] <= str(as_of):
            choices.append((meta["trained_to"], path, meta))
    if not choices:
        raise ValueError(f"Нет обученной HGB остатка ({variant}) на {as_of}")
    _, path, meta = max(choices, key=lambda item: item[0])
    return {**meta, "path": str(path.parent / "model.joblib")}


@lru_cache(maxsize=4)
def _load(path):
    return joblib.load(path)  # локальные артефакты реестра, как в models.load_model


def hgb_method(variant: str):
    """Forecaster: HGB остатка (variant full|mech); для mech добавлены события механизмов X2."""
    from cashflow_ml.protocol_v3 import Forecast

    def forecaster(transactions, daily, as_of, ctx):
        root = ctx.registry.root
        meta = select_residual_hgb(root, as_of, variant)
        cache = ctx.__dict__.setdefault("_structural_cache", {})
        key = ("hgb_inputs", variant)
        if key not in cache:
            cache[key] = hgb_inputs(transactions, as_of, variant)
        flows, scale, model = cache[key]
        with threadpool_limits(limits=1):
            values = _load(meta["path"]).predict(feature_matrix(flows, as_of, scale)) * scale
        events = []
        if model is not None:
            events = [
                mechanism_planned(e, ctx.account_id, as_of, event_source(e))
                for e in model.events(HORIZON)
                if as_of < e.date <= as_of + timedelta(days=HORIZON) and e.amount_minor > 0
            ]
        points = assemble(as_of, ctx.opening_minor, np.maximum(0, values), events)
        return Forecast(
            [p.closing_balance_minor for p in points],
            meta={"model_version": meta["model_version"], "events": len(events)},
        )

    return forecaster


def simple_mech_method():
    """B матрицы 2x2: простой остаток (weekday) + механизмы X2, без документов, налогов и тренда."""
    return protocol_method(tax=False, docs=False, alpha=0.0)


# ---------------------------------------------------------------- GBM остатка (X5: E04, E05)


def industry_list(info: dict | None = None) -> list[str]:
    return sorted({v.get("industry") for v in (info or _INFO).values() if v.get("industry")})


def _salary_tax_dates(history: list[dict], as_of: date, model: MechanismModel):
    """Даты операций и ожидаемых событий зарплаты и ЕНП: прошлые 90 дней и горизонт 30."""
    since = (as_of - timedelta(days=89)).isoformat()
    salary, tax = set(), set()
    acq = total = 0
    for tx in history:
        if tx["operation_date"] < since:
            continue
        key = group_key(tx)
        day = date.fromisoformat(tx["operation_date"])
        if key == "salary|out" and key in model.groups:
            salary.add(day)
        elif key == "tax|out" and key in model.groups:
            tax.add(day)
        if tx["direction"] == "in" and not is_financing(tx["category"], "in"):
            total += tx["amount_minor"]
            if key in ("acquiring|in", "sbp|in"):
                acq += tx["amount_minor"]
    for e in model.events(HORIZON):
        if e.mechanism == "salary":
            salary.add(e.date)
        elif e.mechanism in ("enp", "tax_calendar"):
            tax.add(e.date)
    return salary, tax, (acq / total if total else 0.0)


def registry_daily(registry_events, as_of: date) -> np.ndarray:
    """События реестра -> массив (30, 2): поступления и списания по дням, копейки."""
    out = np.zeros((HORIZON, 2))
    for e in registry_events:
        k = (e.date - as_of).days
        if 1 <= k <= HORIZON:
            out[k - 1, 0 if e.direction == "in" else 1] += e.amount_minor
    return out


def gbm_context(history, as_of, account_id, industries, registry_events, cache=None):
    """(flows, scale, ExtraContext) для GBM остатка. Механизмы берутся без налогового календаря:
    принадлежность истории от него не зависит, а обучение и применение остаются согласованными."""
    cache = cache if cache is not None else {}
    if "mech_plain" not in cache:
        cache["mech_plain"] = detect_mechanisms(history, as_of)
    model = cache["mech_plain"]
    start = date.fromisoformat(min(t["operation_date"] for t in history))
    flows = _frame(residual_flows(history, start, as_of, model), start)
    salary, tax, acq = _salary_tax_dates(history, as_of, model)
    industry = (_INFO.get(account_id) or {}).get("industry")
    code = industries.index(industry) if industry in industries else -1
    ctx = ExtraContext(code, salary, tax, acq, registry_daily(registry_events, as_of))
    return flows, history_scale(history, as_of), ctx


def _wd_cum(as_of: date) -> np.ndarray:
    days = [is_working_day(as_of + timedelta(days=k)) for k in range(1, HORIZON + 1)]
    return np.cumsum(days) / 22.0


def window_matrix(x30: np.ndarray, as_of: date, formulation: str) -> np.ndarray:
    """Строки модели из признаков 30 дней: daily - все дни; cumulative - дни 7, 14, 30 плюс
    число рабочих дней с as_of."""
    if formulation == "daily":
        return x30
    idx = [h - 1 for h in RESIDUAL_HORIZONS]
    return np.hstack([x30[idx], _wd_cum(as_of)[idx, None]])


def window_targets(y30: np.ndarray, formulation: str) -> np.ndarray:
    if formulation == "daily":
        return y30
    return np.cumsum(y30, axis=0)[[h - 1 for h in RESIDUAL_HORIZONS]]


def cumulative_to_daily(cum: np.ndarray, as_of: date) -> np.ndarray:
    """Накопленный остаток на 7/14/30 -> дневной (30, 3): кусочно-линейная кумулята, приращение
    сегмента делится поровну между рабочими днями сегмента."""
    out = np.zeros((HORIZON, cum.shape[1]))
    prev, edge = np.zeros(cum.shape[1]), 0
    for h, c in zip(RESIDUAL_HORIZONS, cum, strict=True):
        days = [k for k in range(edge + 1, h + 1) if is_working_day(as_of + timedelta(days=k))]
        if not days:
            days = list(range(edge + 1, h + 1))
        for k in days:
            out[k - 1] = (c - prev) / len(days)
        prev, edge = c, h
    return out


def build_residual_dataset(account_data: dict, trained_to: date, progress=None) -> dict:
    """Окна обучения GBM остатка: признаки (N, 30, F) и нормированная цель (N, 30, 3).

    Цель - поток следующих 30 дней без финансирования, без операций механизмов и без документов,
    уже выставленных на origin (их ведёт реестр). Только origins с целями <= trained_to."""
    from cashflow_ml.models import training_origins

    if _UNIVERSE is None:
        raise ValueError("Нужен реестр документов: structural.configure(universe, info)")
    industries = industry_list()
    xs, ys, meta = [], [], []
    for account_id, (txs, daily) in sorted(account_data.items()):
        if not daily:
            continue
        valid = {d["date"]: d["valid"] for d in daily}
        first = date.fromisoformat(daily[0]["date"])
        last = min(trained_to, date.fromisoformat(daily[-1]["date"]))
        for origin in training_origins(first, last):
            if not all(valid.get(str(origin + timedelta(days=h)), False) for h in range(-89, 31)):
                continue
            if not docs_available(account_id, origin):
                continue
            end = origin + timedelta(days=HORIZON)
            upto = categories_at([t for t in txs if t["operation_date"] <= str(end)], origin)
            past = [t for t in upto if t["operation_date"] <= str(origin)]
            reg = _UNIVERSE.registry(account_id, origin, HORIZON)
            flows_in = gbm_context(past, origin, account_id, industries, reg)
            flows, scale, ctx = flows_in
            model = detect_mechanisms(past, origin)
            lags = doc_lags(upto, end, origin + timedelta(days=1))
            values = np.zeros((HORIZON, STREAMS))
            for tx in upto:
                day = date.fromisoformat(tx["operation_date"])
                if day <= origin:
                    continue
                h = (day - origin).days
                if owner_of(tx, model, None) != "residual":
                    continue
                if tx["operation_id"] in lags and lags[tx["operation_id"]] >= h:
                    continue
                values[h - 1, _stream(tx)] += tx["amount_minor"]
            xs.append(feature_matrix_ext(flows, origin, scale, ctx))
            ys.append(values / scale)
            meta.append({"account_id": account_id, "origin": str(origin)})
        if progress:
            progress(f"{account_id}: окон {len(meta)}")
    if not xs:
        raise ValueError("Нет пригодных обучающих окон")
    return {
        "x": np.stack(xs),
        "y": np.stack(ys),
        "meta": meta,
        "industries": industries,
        "trained_to": str(trained_to),
    }


def train_residual_gbm(
    dataset: dict,
    kind: str,
    root,
    formulation: str = "daily",
) -> dict:
    """Обучить рецепт kind (ключ models.GBM_CONFIGS) и сохранить gres-* в реестре артефактов."""
    from cashflow_ml.models import GBM_CONFIGS, GBMResidual

    library, params = GBM_CONFIGS[kind]
    origins = [date.fromisoformat(m["origin"]) for m in dataset["meta"]]
    x = np.concatenate(
        [window_matrix(w, o, formulation) for w, o in zip(dataset["x"], origins, strict=True)]
    )
    y = np.concatenate([window_targets(w, formulation) for w in dataset["y"]])
    estimator = GBMResidual(library, params, industry_column(), seed=42)
    with threadpool_limits(limits=1):
        estimator.fit(x, y)
    recipe = {
        "trained_to": dataset["trained_to"],
        "kind": kind if formulation == "daily" else f"{kind}:cum",
        "config": kind,
        "formulation": formulation,
        "library": library,
        "parameters": params,
        "features": FEATURE_VERSION,
        "industries": dataset["industries"],
        "seed": 42,
        "training_windows": len(dataset["meta"]),
        "training_rows": len(x),
        "target": "normalized residual flow: no financing, no mechanism-owned, no registry-owned",
    }
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:10]
    version = (
        f"gres-{recipe['trained_to']}-{kind}{'' if formulation == 'daily' else '-cum'}-{digest}"
    )
    folder = Path(root) / version
    folder.mkdir(parents=True, exist_ok=True)
    joblib.dump(estimator, folder / "model.joblib")
    meta = {**recipe, "model_version": version}
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    (folder / "training_origins.json").write_text(json.dumps(dataset["meta"]))
    return meta


def gbm_residual_values(
    history, as_of, account_id, meta: dict, registry_events, cache=None
) -> np.ndarray:
    """Остаток (30, 3) в копейках из обученной GBM по истории <= as_of."""
    flows, scale, ctx = gbm_context(
        history, as_of, account_id, meta["industries"], registry_events, cache
    )
    x30 = feature_matrix_ext(flows, as_of, scale, ctx)
    x = window_matrix(x30, as_of, meta["formulation"])
    with threadpool_limits(limits=1):
        pred = _load(meta["path"]).predict(x) * scale
    if meta["formulation"] == "cumulative":
        pred = cumulative_to_daily(pred, as_of)
    return np.maximum(0, pred)
