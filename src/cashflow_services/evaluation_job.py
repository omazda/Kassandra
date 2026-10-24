"""Checking the forecast on an uploaded dataset: step log, forecasts, honest backtest, metrics.

For every company the current forecast is built on the last statement day, and the same model
is replayed at several earlier dates (every 14 days, the 30-day horizon fully known): only data
up to each date is used, the forecast is compared with the balances that followed. From these
windows come the balance metrics (MAE, MSE, RMSE, WAPE, skill to «nothing changes»), the gap
metrics (confusion matrix, accuracy, precision, recall, F1, date error, lead time) and the
probability metrics (Brier, ROC-AUC, calibration).
"""

import json
import math
import threading
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cashflow_domain.schemas import CATEGORY_LABELS, SOURCE_LABELS, ForecastRequest
from cashflow_ml.protocol_v3 import AccountTruth

HORIZON = 30
STEP_DAYS = 14
MAX_ORIGINS = 4
MIN_HISTORY_DAYS = 28
CLOSED_AFTER_DAYS = 7
DATE_TOLERANCE = 3
CALIBRATION_TEXT = {
    "unavailable": "недоступна",
    "experimental_uncalibrated": "сырая частота траекторий, без калибровки",
    "calibrated_on_jan_feb": "откалибровано на январе–феврале 2026",
    "calibrated_on_selection": "откалибровано X11 на марте–мае 2026",
}
MODEL_TEXT = {
    "ensemble": "ансамбль X10 (HGB по потокам + HGB с календарём + профиль месяца, веса по горизонту)",
    "hgb": "градиентный бустинг HGB по потокам",
    "weekday": "профиль по дням недели (счёт короче обучения моделей)",
}


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def rub(minor):
    if minor is None:
        return "—"
    sign = "−" if minor < 0 else ""
    return f"{sign}{abs(int(round(minor))) // 100:,} ₽".replace(",", " ")


def _ratio(a, b):
    return round(a / b, 4) if b else None


# ---------------------------------------------------------------------- one forecast, step by step


def forecast_trace(svc, forecast) -> list[dict]:
    """How this forecast was built: data, categories, regular payments, known events, the model
    and its weights, the trajectory, uncertainty and the gap."""
    account = forecast.account_id
    as_of = forecast.as_of
    quality = svc.store.quality(account)
    txs = svc.store.transactions(account, as_of)
    recent = [t for t in txs if t["operation_date"] > str(as_of - timedelta(days=90))]
    steps = []

    def step(title, detail, items=None):
        steps.append({"n": len(steps) + 1, "title": title, "detail": detail, "items": items or []})

    first = txs[0]["operation_date"] if txs else None
    step(
        "История операций",
        f"{len(txs)} операций с {first} по {as_of} — прогноз видит только данные до даты расчёта "
        f"(будущие операции и остатки в расчёт не попадают).",
        [
            f"несверок остатка между днями: {len(quality.get('inconsistent_transitions', []))}",
            f"дней под маской качества (не используются в оценке): {quality.get('invalid_days', 0)}",
        ],
    )
    by_cat = Counter()
    for t in recent:
        by_cat[(t["direction"], t["category"])] += t["amount_minor"]
    top = sorted(by_cat.items(), key=lambda kv: -kv[1])[:6]
    step(
        "Категоризация",
        "59 правил «контрагент + назначение» относят каждую операцию к одной из 30 категорий "
        "(точность 0,985 на 400 размеченных). Обороты за 90 дней:",
        [
            f"{'поступления' if d == 'in' else 'списания'} · {CATEGORY_LABELS.get(c, c)}: {rub(v)}"
            for (d, c), v in top
        ],
    )
    regular = [e for e in forecast.events if e.source == "regular" and e.status != "cancelled"]
    step(
        "Регулярные платежи",
        f"Найдено повторов по истории в горизонте: {len(regular)} (зарплата, аренда, кредиты, "
        "подписки — ритм и сумма по прошлым платежам, перенос на рабочий день по календарю РФ).",
        [
            f"{e.expected_date} · {'+' if e.direction == 'in' else '−'}{rub(e.remaining_minor)} · {e.label[:60]}"
            for e in sorted(regular, key=lambda e: e.expected_date)[:6]
        ],
    )
    known = defaultdict(lambda: [0, 0])
    for e in forecast.events:
        if e.source != "regular" and e.status != "cancelled":
            known[e.source][0] += 1
            known[e.source][1] += e.remaining_minor
    step(
        "Известные события",
        "Договоры, счета, банк, налоги и ручной ввод: точные даты и суммы заменяют свою долю "
        "среднего потока модели (правило «один владелец потока»).",
        [f"{SOURCE_LABELS.get(s, s)}: {n} шт. на {rub(v)}" for s, (n, v) in known.items()]
        or ["внешних событий нет — прогноз опирается на историю"],
    )
    model_items = [
        f"модель: {forecast.model} ({forecast.model_version}), обучение до {forecast.trained_to or '—'}",
        "признаки: 34 — потоки за 7/28/90 дней, день недели и месяца, рабочий день РФ, длина истории",
    ]
    if forecast.model == "ensemble":
        try:
            weights = json.loads(
                (Path(svc.artifacts) / "ensemble" / "weights.json").read_text(encoding="utf-8")
            )
            names = {
                "hgb_v2": "HGB по потокам",
                "hgb_cal": "HGB с календарём",
                "shape": "профиль месяца",
            }
            theta = weights["theta"]
            for i, (lo, hi) in enumerate(weights["buckets"]):
                parts = [
                    f"{names.get(spec[0], spec[0])} {theta[j * len(weights['buckets']) + i]:.2f}"
                    for j, spec in enumerate(weights["spec"])
                ]
                model_items.append(
                    f"дни {lo}–{hi}: " + ", ".join(parts) + "; остальное — «не изменится»"
                )
        except (OSError, KeyError, ValueError, IndexError):
            pass
    step(
        "ML-модель",
        f"{MODEL_TEXT.get(forecast.model, forecast.model)}. Модель прогнозирует дневные потоки "
        "(выручка, прочие поступления, списания); ансамбль смешивает изменения остатка трёх "
        "методов с весами по горизонту, подобранными до 28.02.2026.",
        model_items,
    )
    points = forecast.points
    inflow = sum(p.event_in_minor + p.residual_in_minor for p in points)
    outflow = sum(p.event_out_minor + p.residual_out_minor for p in points)
    low = min(points, key=lambda p: p.closing_balance_minor)
    step(
        "Сборка траектории",
        "Остаток[d] = остаток[d−1] + события + прогноз модели по дням.",
        [
            f"остаток на {as_of}: {rub(forecast.opening_balance_minor)}",
            f"поступления за 30 дней: {rub(inflow)}, списания: {rub(outflow)}",
            f"через 7 / 14 / 30 дней: {rub(points[6].closing_balance_minor)} / "
            f"{rub(points[13].closing_balance_minor)} / {rub(points[-1].closing_balance_minor)}",
            f"минимум: {rub(low.closing_balance_minor)} на {low.date}",
        ],
    )
    risk = forecast.risk
    p10, p90 = points[-1].p10_minor, points[-1].p90_minor
    step(
        "Неопределённость и вероятность",
        f"Банк ошибок прошлых прогнозов, {risk.trajectory_count or risk.simulations or 1000} "
        "траекторий: доля траекторий, где остаток уходит ниже нуля, — вероятность разрыва.",
        [
            f"вероятность разрыва за 30 дней: {'—' if risk.probability_30d is None else f'{risk.probability_30d:.0%}'} "
            f"({CALIBRATION_TEXT.get(risk.calibration_status, risk.calibration_status)})",
            *(
                [f"тревога X11 (порог калиброванной вероятности): {'да' if risk.alarm else 'нет'}"]
                if risk.alarm is not None
                else []
            ),
            f"коридор P10–P90 на 30-й день: {rub(p10)} … {rub(p90)}",
        ],
    )
    explanation = svc.explain(run_id=forecast.run_id)
    step(
        "Кассовый разрыв",
        f"{explanation.when} {explanation.shortfall}".strip()
        if explanation.has_gap
        else "Остаток не уходит ниже нуля в горизонте 30 дней.",
        list(explanation.why[:4]) + [f"действие: {a.text}" for a in explanation.actions[:3]],
    )
    return steps


# ---------------------------------------------------------------------- metrics


def _auc(scores, labels):
    pos = [s for s, y in zip(scores, labels, strict=True) if y]
    neg = [s for s, y in zip(scores, labels, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def compute_metrics(windows: list[dict]) -> dict:
    errors, const_errors, actuals = [], [], []
    by_h = defaultdict(lambda: [0, 0, 0])
    min_errors, min_const = [], []
    per_company = defaultdict(lambda: [0, 0, 0])
    for w in windows:
        for h, (f, a) in enumerate(zip(w["forecast"], w["actual"], strict=True), 1):
            if a is None:
                continue
            e, c = f - a, w["start_balance"] - a
            errors.append(e)
            const_errors.append(c)
            actuals.append(a)
            by_h[h][0] += abs(e)
            by_h[h][1] += abs(c)
            by_h[h][2] += 1
            per_company[w["account_id"]][0] += abs(e)
            per_company[w["account_id"]][1] += abs(c)
            per_company[w["account_id"]][2] += 1
        facts = [a for a in w["actual"] if a is not None]
        if facts:
            min_errors.append(abs(min(w["forecast"]) - min(facts)))
            min_const.append(abs(w["start_balance"] - min(facts)))
    n = len(errors)
    mae = sum(abs(e) for e in errors) / n if n else None
    mse = sum(e * e for e in errors) / n if n else None
    mae_c = sum(abs(c) for c in const_errors) / n if n else None
    mse_c = sum(c * c for c in const_errors) / n if n else None
    better = sum(1 for m, c, k in per_company.values() if k and m < c)
    balance = {
        "points": n,
        "windows": len(windows),
        "companies": len(per_company),
        "mae_minor": round(mae) if mae is not None else None,
        "mse": mse,
        "rmse_minor": round(math.sqrt(mse)) if mse is not None else None,
        "wape": _ratio(sum(abs(e) for e in errors), sum(abs(a) for a in actuals)),
        "bias_minor": round(sum(errors) / n) if n else None,
        "constant_mae_minor": round(mae_c) if mae_c is not None else None,
        "constant_mse": mse_c,
        "constant_rmse_minor": round(math.sqrt(mse_c)) if mse_c is not None else None,
        "skill_mae": round(1 - mae / mae_c, 4) if mae_c else None,
        "skill_mse": round(1 - mse / mse_c, 4) if mse_c else None,
        "companies_better": better,
        "minimum_mae_minor": round(sum(min_errors) / len(min_errors)) if min_errors else None,
        "minimum_constant_mae_minor": round(sum(min_const) / len(min_const)) if min_const else None,
        "by_horizon": [
            {
                "day": h,
                "mae_minor": round(v[0] / v[2]),
                "constant_mae_minor": round(v[1] / v[2]),
            }
            for h, v in sorted(by_h.items())
            if v[2]
        ],
    }
    gaps = _gap_metrics(windows, "predicted_gap_day")
    trajectory = _gap_metrics(windows, "trajectory_gap_day")
    probs = [
        w["probability"]
        for w in windows
        if w["start_balance"] >= 0 and w["probability"] is not None
    ]
    labels = [
        w["actual_gap_day"] is not None
        for w in windows
        if w["start_balance"] >= 0 and w["probability"] is not None
    ]
    base = sum(labels) / len(labels) if labels else None
    brier = (
        sum((p - y) ** 2 for p, y in zip(probs, labels, strict=True)) / len(probs)
        if probs
        else None
    )
    brier_base = base * (1 - base) if base is not None else None
    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        hi = lo + 0.2
        inside = [
            (p, y)
            for p, y in zip(probs, labels, strict=True)
            if lo <= p < hi or (hi >= 1 and p == 1)
        ]
        if inside:
            bins.append(
                {
                    "range": f"{lo:.0%}–{hi:.0%}",
                    "windows": len(inside),
                    "predicted": round(sum(p for p, _ in inside) / len(inside), 4),
                    "observed": round(sum(y for _, y in inside) / len(inside), 4),
                }
            )
    probability = {
        "windows": len(probs),
        "calibrated_windows": sum(
            1 for w in windows if w["start_balance"] >= 0 and w.get("alarm") is not None
        ),
        "base_rate": round(base, 4) if base is not None else None,
        "mean_probability": round(sum(probs) / len(probs), 4) if probs else None,
        "brier": round(brier, 4) if brier is not None else None,
        "brier_skill": round(1 - brier / brier_base, 4)
        if brier is not None and brier_base
        else None,
        "roc_auc": _auc(probs, labels),
        "calibration": bins,
    }
    return {
        "balance": balance,
        "gaps": gaps,
        "gaps_trajectory": trajectory,
        "probability": probability,
    }


def _gap_metrics(windows: list[dict], key: str) -> dict:
    """Confusion matrix of one alarm rule; a window counts if it did not start in a deficit."""
    tp = fp = fn = tn = 0
    date_hits, date_errors, leads, skipped = 0, [], [], 0
    for w in windows:
        if w["start_balance"] < 0:
            skipped += 1
            continue
        actual = w["actual_gap_day"] is not None
        predicted = w[key] is not None
        tp += actual and predicted
        fp += predicted and not actual
        fn += actual and not predicted
        tn += not actual and not predicted
        if actual and predicted:
            delta = abs(w[key] - w["actual_gap_day"])
            date_errors.append(delta)
            date_hits += delta <= DATE_TOLERANCE
            leads.append(w["actual_gap_day"])
    total = tp + fp + fn + tn
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision and recall
        else (0.0 if precision is not None and recall is not None else None)
    )
    return {
        "windows": total,
        "skipped_negative_start": skipped,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "accuracy": _ratio(tp + tn, total),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": _ratio(tn, tn + fp),
        "date_within_3_days": _ratio(date_hits, len(date_errors)),
        "date_mae_days": round(sum(date_errors) / len(date_errors), 2) if date_errors else None,
        "lead_days_median": sorted(leads)[len(leads) // 2] if leads else None,
    }


# ---------------------------------------------------------------------- the job


class EvaluationMixin:
    @property
    def _evaluation_jobs(self) -> dict:
        # Per service instance: a test service never sees another one's jobs.
        return self.__dict__.setdefault("_eval_jobs", {})

    @property
    def _evaluation_lock(self):
        return self.__dict__.setdefault("_eval_lock", threading.Lock())

    def start_evaluation(self, dataset_id, principal):
        self.debug_dataset(dataset_id)
        with self._evaluation_lock:
            job = self._evaluation_jobs.get(dataset_id)
            if job and job["status"] == "running":
                return self.evaluation(dataset_id)
            job = {
                "status": "running",
                "started": time.monotonic(),
                "started_at": _now(),
                "stage": "Подготовка",
                "done": 0,
                "total": 0,
                "log": [],
            }
            self._evaluation_jobs[dataset_id] = job
        threading.Thread(
            target=self._run_evaluation,
            args=(dataset_id, job),
            daemon=True,
            name=f"eval-{dataset_id}",
        ).start()
        return self.evaluation(dataset_id)

    def evaluation(self, dataset_id):
        self.debug_dataset(dataset_id)
        with self._evaluation_lock:
            job = self._evaluation_jobs.get(dataset_id)
            if job is not None:
                elapsed = (job.get("finished") or time.monotonic()) - job["started"]
                state = {
                    "dataset_id": dataset_id,
                    "status": job["status"],
                    "stage": job["stage"],
                    "done": job["done"],
                    "total": job["total"],
                    "elapsed": round(elapsed, 1),
                    "log": list(job["log"]),
                    "result": job.get("result"),
                    "error": job.get("error"),
                }
                return state
        with self.store.connect() as db:
            row = db.execute(
                "SELECT state FROM debug_evaluations WHERE dataset_id=?", (dataset_id,)
            ).fetchone()
        if row is None:
            return {
                "dataset_id": dataset_id,
                "status": "none",
                "stage": "",
                "done": 0,
                "total": 0,
                "elapsed": 0,
                "log": [],
                "result": None,
                "error": None,
            }
        state = json.loads(row[0])
        if state["status"] == "running":
            state["status"], state["error"] = "error", "Расчёт прервался (сервер перезапускался)"
        return state

    def _log(self, job, text):
        elapsed = time.monotonic() - job["started"]
        with self._evaluation_lock:
            job["log"].append(f"[{int(elapsed // 60):02d}:{elapsed % 60:04.1f}] {text}")

    def _run_evaluation(self, dataset_id, job):
        try:
            self._evaluate(dataset_id, job)
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001 — the page must see the failure, not spin forever.
            job["status"], job["error"] = "error", str(exc)[:300]
            self._log(job, f"Ошибка: {str(exc)[:200]}")
        job["finished"] = time.monotonic()
        state = self.evaluation(dataset_id)
        with self.lock, self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO debug_evaluations(dataset_id, finished_at, state) VALUES(?,?,?)",
                (dataset_id, _now(), json.dumps(state, ensure_ascii=False, default=str)),
            )

    def _evaluate(self, dataset_id, job):
        report = self.debug_dataset(dataset_id)
        accounts = report["accounts"]
        q = report["quality"]
        self._log(
            job,
            f"Шаг 1. Данные: «{report['filename']}» — {report['companies']} компаний, "
            f"{report['accepted']} операций из {report['rows']} строк (отклонено {report['rejected']}), "
            f"период {report['first_date']} — {report['last_date']}.",
        )
        self._log(
            job,
            f"Шаг 2. Качество: несверок остатка {q['inconsistent_transitions']} "
            f"(больше копейки — {q['material_transitions']}), подозрительных дублей "
            f"{q['suspicious_duplicate_groups']}, дней под маской {q['invalid_days']} — такие дни "
            "не участвуют в метриках.",
        )
        cats = report["categories"]
        top = ", ".join(f"{c['label']} {rub(c['amount_minor'])}" for c in cats["top"][:4])
        self._log(
            job,
            f"Шаг 3. Категоризация: без категории {cats['unknown_operations']} операций "
            f"({cats['unknown_share']:.2%} оборота); крупнейшие: {top}.",
        )
        self._log(
            job,
            "Шаг 4. Модель: ансамбль X10 (модели обучены до 31.12.2025, веса до 28.02.2026); "
            "если дата раньше — HGB, если счёт короче обучения — профиль по дням недели. "
            "Вероятность разрыва — 1000 траекторий ошибок, после 23.06.2026 калибровка X11. "
            f"Проверка на истории: до {MAX_ORIGINS} дат расчёта на компанию с шагом {STEP_DAYS} дней, "
            f"горизонт {HORIZON} дней, только данные до даты расчёта.",
        )
        plan = []
        for account in accounts:
            last = date.fromisoformat(str(account["last_date"]))
            first = date.fromisoformat(str(account.get("data_from") or account["first_date"]))
            history_from = date.fromisoformat(str(account["first_date"]))
            origins = []
            for k in range(MAX_ORIGINS):
                origin = last - timedelta(days=HORIZON + k * STEP_DAYS)
                if origin < first or (origin - history_from).days < MIN_HISTORY_DAYS:
                    break
                origins.append(origin)
            plan.append((account, last, origins))
        job["total"] = sum(1 + len(o) for _, _, o in plan)
        windows, current, models = [], [], Counter()
        job["stage"] = "Прогнозы и проверка на истории"
        # An account that stopped at zero well before the end of the set was closed: its
        # forecast is built for the record, but it is not a gap ahead.
        set_end = max((last for _, last, _ in plan), default=None)
        for account, last, origins in plan:
            closed = (
                account["last_balance_minor"] == 0
                and set_end is not None
                and (set_end - last).days > CLOSED_AFTER_DAYS
            )
            account_id = account["account_id"]
            daily = self.store.daily(account_id)
            by_day = {d["date"]: d for d in daily}
            truth = AccountTruth(self.store.transactions(account_id), daily) if origins else None
            try:
                f = self.forecast(
                    ForecastRequest(account_id=account_id, as_of=last, model="ensemble")
                )
                models[f.model] += 1
                current.append(
                    {
                        "account_id": account_id,
                        "run_id": f.run_id,
                        "model": f.model,
                        "as_of": str(last),
                        "gap_date": str(f.risk.first_gap_date) if f.risk.first_gap_date else None,
                        "need_minor": f.risk.liquidity_need_minor,
                        "probability": f.risk.probability_30d,
                        "minimum_minor": f.risk.minimum_balance_minor,
                        "closed": closed,
                    }
                )
            except ValueError as exc:
                self._log(job, f"  {account_id}: прогноз не построен — {str(exc)[:120]}")
            job["done"] += 1
            for origin in origins:
                try:
                    past = self.forecast(
                        ForecastRequest(account_id=account_id, as_of=origin, model="ensemble")
                    )
                except ValueError:
                    job["done"] += 1
                    continue
                start = by_day.get(str(origin), {}).get("balance_minor", past.opening_balance_minor)
                # Truth by protocol v3: D3 — the balance without financing after the origin,
                # D2 — a negative day or a day the owner's money rescued.
                window = truth.window(origin)
                actual, observed, forecast_path = [], [], []
                for p, target, seen in zip(
                    past.points, window["target"], window["observed"], strict=True
                ):
                    valid = by_day.get(str(p.date), {}).get("valid", False)
                    actual.append(int(target) if valid else None)
                    observed.append(int(seen) if valid else None)
                    forecast_path.append(p.closing_balance_minor)
                gap_actual = min(window["event_offsets"], default=None)
                gap_line = next((i for i, v in enumerate(forecast_path, 1) if v < 0), None)
                # X11 alarm by the calibrated probability; without it — the trajectory below zero.
                alarm = past.risk.alarm
                if alarm is None:
                    gap_pred = gap_line
                elif alarm:
                    low = forecast_path.index(min(forecast_path)) + 1
                    gap_pred = gap_line or low
                else:
                    gap_pred = None
                windows.append(
                    {
                        "account_id": account_id,
                        "origin": str(origin),
                        "model": past.model,
                        "start_balance": start,
                        "forecast": forecast_path,
                        "p10": [p.p10_minor for p in past.points],
                        "p90": [p.p90_minor for p in past.points],
                        "actual": actual,
                        "observed": observed,
                        "financing_minor": window["financing_total_minor"],
                        "actual_gap_day": gap_actual,
                        "predicted_gap_day": gap_pred,
                        "trajectory_gap_day": gap_line,
                        "alarm": alarm,
                        "probability": past.risk.probability_30d,
                    }
                )
                job["done"] += 1
            if len(current) % 25 == 0:
                self._log(job, f"  построено прогнозов: {job['done']} из {job['total']}")
        gaps_now = [c for c in current if c["gap_date"] and not c["closed"]]
        closed_now = sum(c["closed"] for c in current)
        self._log(
            job,
            f"Шаг 5. Прогноз на последнюю дату: {len(current)} компаний, модель — "
            + ", ".join(f"{m} {n}" for m, n in models.most_common())
            + f"; разрыв в ближайшие 30 дней у {len(gaps_now)}, суммарная нехватка "
            f"{rub(sum(c['need_minor'] for c in gaps_now))}"
            + (
                f"; закрытых счетов (остаток 0, операции кончились раньше набора) — {closed_now}, "
                "их прогноз в разрывы не входит."
                if closed_now
                else "."
            ),
        )
        self._log(
            job,
            f"Шаг 6. Проверка на истории: {len(windows)} окон по {HORIZON} дней. Факт — остаток без "
            "новых пополнений владельца и кредитов после даты расчёта (протокол D3); разрыв — "
            "отрицательный остаток или день, который спасло такое пополнение (метка D2). Тревога — "
            "калиброванная вероятность разрыва от порога X11, без калибровки — траектория ниже нуля.",
        )
        metrics = compute_metrics(windows)
        b, g, p = metrics["balance"], metrics["gaps"], metrics["probability"]
        self._log(
            job,
            f"Шаг 7. Остаток: MAE {rub(b['mae_minor'])} против {rub(b['constant_mae_minor'])} у "
            f"«не изменится» (skill {b['skill_mae']}), RMSE {rub(b['rmse_minor'])}, WAPE {b['wape']}; "
            f"минимум месяца: MAE {rub(b['minimum_mae_minor'])} против "
            f"{rub(b['minimum_constant_mae_minor'])}.",
        )
        c = g["confusion"]
        self._log(
            job,
            f"Шаг 8. Разрывы: TP {c['tp']}, FP {c['fp']}, FN {c['fn']}, TN {c['tn']} — "
            f"accuracy {g['accuracy']}, precision {g['precision']}, recall {g['recall']}, F1 {g['f1']} "
            f"(только траектория: F1 {metrics['gaps_trajectory']['f1']}); "
            f"вероятность: Brier {p['brier']} (skill {p['brier_skill']}), ROC-AUC {p['roc_auc']}.",
        )
        series = defaultdict(list)
        for w in windows:
            series[w["account_id"]].append(w)
        with self.lock, self.store.connect() as db:
            db.execute("DELETE FROM debug_eval_companies WHERE dataset_id=?", (dataset_id,))
            db.executemany(
                "INSERT INTO debug_eval_companies(dataset_id, account_id, payload) VALUES(?,?,?)",
                [
                    (
                        dataset_id,
                        c["account_id"],
                        json.dumps({"current": c, "windows": series.get(c["account_id"], [])}),
                    )
                    for c in current
                ],
            )
        per_company = []
        for c in current:
            own = series.get(c["account_id"], [])
            m = compute_metrics(own)["balance"] if own else None
            per_company.append(
                {
                    **c,
                    "windows": len(own),
                    "mae_minor": m["mae_minor"] if m else None,
                    "constant_mae_minor": m["constant_mae_minor"] if m else None,
                    "skill": m["skill_mae"] if m else None,
                }
            )
        job["result"] = {
            "computed_at": _now(),
            "metrics": metrics,
            "models": dict(models),
            "current": {
                "companies": len(current),
                "closed": closed_now,
                "with_gap": len(gaps_now),
                "need_minor": sum(c["need_minor"] for c in gaps_now),
            },
            "companies": per_company,
            "categorization": {
                "labelled": 400,
                "accuracy": 0.985,
                "macro_f1": 0.963,
                "note": "размеченная вручную выборка из обучающего датасета (X1)",
            },
        }
        job["stage"] = "Готово"
        self._log(job, "Готово.")

    def evaluation_company(self, dataset_id, account_id):
        self.debug_dataset(dataset_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM debug_eval_companies WHERE dataset_id=? AND account_id=?",
                (dataset_id, account_id),
            ).fetchone()
        if row is None:
            raise ValueError("Для этой компании ещё нет расчёта")
        data = json.loads(row[0])
        forecast = self.get_forecast(data["current"]["run_id"])
        daily = [
            d
            for d in self.store.daily(account_id)
            if date.fromisoformat(d["date"]) > forecast.as_of - timedelta(days=60)
        ]
        return {
            "account_id": account_id,
            "current": data["current"],
            "history": [{"date": d["date"], "balance_minor": d["balance_minor"]} for d in daily],
            "forecast": [
                {
                    "date": str(p.date),
                    "balance_minor": p.closing_balance_minor,
                    "p10_minor": p.p10_minor,
                    "p90_minor": p.p90_minor,
                }
                for p in forecast.points
            ],
            "windows": data["windows"],
            "trace": forecast_trace(self, forecast),
        }

    def evaluation_report(self, dataset_id) -> str:
        report = self.debug_dataset(dataset_id)
        state = self.evaluation(dataset_id)
        result = state.get("result")
        if not result:
            raise ValueError("Сначала запустите расчёт")
        m = result["metrics"]
        b, g, p = m["balance"], m["gaps"], m["probability"]
        t = m.get("gaps_trajectory", g)
        c = g["confusion"]
        lines = [
            f"# Kassandra — проверка на датасете «{report['filename']}»",
            "",
            f"Рассчитано {result['computed_at']}: {report['companies']} компаний, "
            f"{report['accepted']} операций, период {report['first_date']} — {report['last_date']}.",
            "",
            "## Журнал расчёта",
            "",
            *[f"    {line}" for line in state["log"]],
            "",
            "## Прогноз на последнюю дату",
            "",
            f"Компаний с прогнозом: {result['current']['companies']}; кассовый разрыв в ближайшие 30 дней — "
            f"у {result['current']['with_gap']}, суммарная нехватка {rub(result['current']['need_minor'])}.",
            "",
            "## Точность прогноза остатка (проверка на истории)",
            "",
            f"Окон: {b['windows']}, дневных точек: {b['points']}, компаний: {b['companies']}.",
            "",
            "| Метрика | Модель | «Остаток не изменится» |",
            "|---|---:|---:|",
            f"| MAE, ₽ | {rub(b['mae_minor'])} | {rub(b['constant_mae_minor'])} |",
            f"| RMSE, ₽ | {rub(b['rmse_minor'])} | {rub(b['constant_rmse_minor'])} |",
            f"| MSE, ₽² | {b['mse']:.3e} | {b['constant_mse']:.3e} |"
            if b["mse"] is not None
            else "| MSE | — | — |",
            f"| WAPE | {b['wape']} | — |",
            f"| MAE минимума месяца, ₽ | {rub(b['minimum_mae_minor'])} | {rub(b['minimum_constant_mae_minor'])} |",
            "",
            f"Skill по MAE: {b['skill_mae']}, по MSE: {b['skill_mse']}; модель точнее у "
            f"{b['companies_better']} из {b['companies']} компаний.",
            "",
            "## Кассовые разрывы: матрица ошибок",
            "",
            "Факт — остаток без новых пополнений после даты расчёта (D3); разрыв — отрицательный "
            "остаток или день, который спасло пополнение владельца или кредит (D2). Тревога — "
            "калиброванная вероятность от порога X11 (без калибровки — траектория ниже нуля).",
            "",
            "| | Разрыв был | Разрыва не было |",
            "|---|---:|---:|",
            f"| Модель предсказала разрыв | TP = {c['tp']} | FP = {c['fp']} |",
            f"| Модель не предсказала | FN = {c['fn']} | TN = {c['tn']} |",
            "",
            f"Accuracy {g['accuracy']}, Precision {g['precision']}, Recall {g['recall']}, F1 {g['f1']}, "
            f"Specificity {g['specificity']}. Дата разрыва угадана ±3 дня в {g['date_within_3_days']} "
            f"найденных (средняя ошибка {g['date_mae_days']} дн.), медиана заблаговременности "
            f"{g['lead_days_median']} дн. Окон с отрицательным остатком на старте (исключены): "
            f"{g['skipped_negative_start']}.",
            "",
            f"Только траектория ниже нуля: TP {t['confusion']['tp']}, FP {t['confusion']['fp']}, "
            f"FN {t['confusion']['fn']}, TN {t['confusion']['tn']}; precision {t['precision']}, "
            f"recall {t['recall']}, F1 {t['f1']}.",
            "",
            "## Вероятность разрыва",
            "",
            f"Окон с вероятностью: {p['windows']}, базовая частота {p['base_rate']}, средняя вероятность "
            f"{p['mean_probability']}, Brier {p['brier']} (skill {p['brier_skill']}), ROC-AUC {p['roc_auc']}.",
            "",
            "| Прогноз вероятности | Окон | Средняя вероятность | Фактическая частота |",
            "|---|---:|---:|---:|",
            *[
                f"| {x['range']} | {x['windows']} | {x['predicted']} | {x['observed']} |"
                for x in p["calibration"]
            ],
            "",
            "## Категоризация",
            "",
            "Accuracy 0,985 и macro-F1 0,963 на 400 операциях, размеченных вручную (обучающий датасет).",
        ]
        return "\n".join(lines) + "\n"
