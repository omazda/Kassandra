from datetime import date, timedelta

import numpy as np
import pandas as pd

from cashflow_domain.schemas import is_revenue
from cashflow_ml.calendar_ru import is_working_day
from cashflow_ml.regularity import component_key, detect_components

FEATURE_VERSION = "past-flow-v2"
STREAMS = ["revenue", "other_in", "out"]


def categories_at(transactions, as_of):
    result = []
    for tx in transactions:
        tx = {**tx, "category": tx.get("original_category", tx["category"])}
        for revision in tx.get("category_revisions", []):
            if revision["known_at"] <= str(as_of):
                tx["category"] = revision["category"]
        result.append(tx)
    return result


def flow_frame(transactions: list[dict], start: date, end: date, components: dict) -> pd.DataFrame:
    values = np.zeros(((end - start).days + 1, 3))
    for tx in transactions:
        day = date.fromisoformat(tx["operation_date"])
        if start <= day <= end and component_key(tx) not in components:
            stream = 2 if tx["direction"] == "out" else 0 if is_revenue(tx["category"], "in") else 1
            values[(day - start).days, stream] += tx["amount_minor"]
    return pd.DataFrame(values, index=pd.date_range(start, end), columns=STREAMS)


def prepare(transactions: list[dict], as_of: date, use_calendar: bool):
    past = categories_at(
        [t for t in transactions if t["operation_date"] <= as_of.isoformat()], as_of
    )
    if not past:
        raise ValueError("Нет операций на выбранную дату")
    start = date.fromisoformat(min(t["operation_date"] for t in past))
    components = detect_components(past, as_of) if use_calendar else {}
    flows = flow_frame(past, start, as_of, components)
    # Normalize with past TOTAL turnover so calendar/full recipes share the same scale.
    history_start = max(start, as_of - timedelta(days=89))
    scale = max(
        100.0,
        sum(t["amount_minor"] for t in past if t["operation_date"] >= history_start.isoformat())
        / ((as_of - history_start).days + 1),
    )
    return flows, components, scale


def feature_matrix(flows: pd.DataFrame, as_of: date, scale: float) -> np.ndarray:
    statistics = []
    for window in (7, 28, 90):
        values = flows.tail(window).to_numpy() / scale
        statistics.extend(np.mean(values, axis=0))
        statistics.extend(np.std(values, axis=0))
        statistics.extend(np.mean(values == 0, axis=0))
    statistics.append(min(len(flows), 730) / 730)
    result = []
    for h in range(1, 31):
        d = as_of + timedelta(days=h)
        # Future calendar is known; no future observations enter this matrix.
        result.append(
            [
                *statistics,
                h / 30,
                d.weekday() / 6,
                d.day / 31,
                np.sin(2 * np.pi * d.month / 12),
                np.cos(2 * np.pi * d.month / 12),
                float(d.weekday() >= 5),
            ]
        )
    return np.asarray(result)


# --------------------------------------------------------------- расширенные признаки (X5)

EXT_COLUMNS = [
    "log_turnover",
    "industry",
    "is_working",
    "is_holiday",
    "days_to_month_end",
    "working_days_left_in_month",
    "days_since_salary",
    "days_to_salary",
    "days_since_tax",
    "days_to_tax",
    "acquiring_share",
    "registry_in_day",
    "registry_out_day",
    "registry_in_cum",
    "registry_out_cum",
]
GAP_CAP = 30


class ExtraContext:
    """Контекст среза для расширенных признаков; всё известно на as_of.

    industry_code: код отрасли (категория), salary_dates/tax_dates: прошлые операции механизмов и
    ожидаемые события на горизонте, acquiring_share: доля эквайринга и СБП в поступлениях за 90
    дней, registry: массив (30, 2) ожидаемых поступлений и списаний реестра документов по дням
    (копейки; нули, если реестра нет).
    """

    def __init__(
        self, industry_code=-1, salary_dates=(), tax_dates=(), acquiring_share=0.0, registry=None
    ):
        self.industry_code = int(industry_code)
        self.salary_dates = sorted(salary_dates)
        self.tax_dates = sorted(tax_dates)
        self.acquiring_share = float(acquiring_share)
        self.registry = np.zeros((30, 2)) if registry is None else np.asarray(registry, dtype=float)


def _gaps(dates: list[date], d: date) -> tuple[float, float]:
    since, upto = GAP_CAP, GAP_CAP
    for x in dates:
        if x <= d:
            since = min(since, (d - x).days)
        else:
            upto = min(upto, (x - d).days)
            break
    return since / GAP_CAP, upto / GAP_CAP


def feature_matrix_ext(
    flows: pd.DataFrame, as_of: date, scale: float, context: ExtraContext
) -> np.ndarray:
    """Признаки v2 плюс отрасль, рабочий день, праздник, день месяца, позиция относительно
    зарплаты и ЕНП, доля эквайринга, оборот и ожидания реестра. Строка на каждый день 1..30."""
    base = feature_matrix(flows, as_of, scale)
    cum = np.cumsum(context.registry, axis=0) / scale
    rows = []
    for h in range(1, 31):
        d = as_of + timedelta(days=h)
        month_end = (pd.Timestamp(d) + pd.offsets.MonthEnd(0)).date()
        left = sum(is_working_day(d + timedelta(days=k)) for k in range((month_end - d).days + 1))
        s_since, s_to = _gaps(context.salary_dates, d)
        t_since, t_to = _gaps(context.tax_dates, d)
        working = is_working_day(d)
        rows.append(
            [
                np.log10(max(scale, 100.0) / 100.0),
                context.industry_code,
                float(working),
                float(d.weekday() < 5 and not working),
                (month_end - d).days / 31,
                left / 22,
                s_since,
                s_to,
                t_since,
                t_to,
                context.acquiring_share,
                context.registry[h - 1, 0] / scale,
                context.registry[h - 1, 1] / scale,
                cum[h - 1, 0],
                cum[h - 1, 1],
            ]
        )
    return np.hstack([base, np.asarray(rows)])


def industry_column(base_columns: int = 0) -> int:
    """Индекс колонки отрасли в матрице feature_matrix_ext."""
    return feature_matrix_width() + EXT_COLUMNS.index("industry") + base_columns


def feature_matrix_width() -> int:
    """Число колонок блока v2 (3 окна * 9 + возраст + 6 календарных)."""
    return 3 * 9 + 1 + 6
