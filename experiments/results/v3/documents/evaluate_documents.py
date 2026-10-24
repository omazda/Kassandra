"""X3: покрытие ссылок на документы, калибровка модели задержки, оценка реестра на срезах.

Запуск из корня: LOKY_MAX_CPU_COUNT=2 uv run python experiments/results/v3/documents/evaluate_documents.py
Читает data/cashflow.sqlite через cashflow_services.cli и dataset/companies.csv (отрасль).
Пишет в experiments/results/v3/documents/: coverage.json, calibration.json, evaluation.json,
events_sample.csv, delay_distribution.json, report.md.
"""

import csv
import json
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from cashflow_domain.schemas import is_financing
from cashflow_ml.documents import (
    DelayConfig,
    DocumentUniverse,
    extract_payments,
    owned_categories,
    parse_purpose,
)
from cashflow_ml.evaluation import eligible
from cashflow_ml.protocol_v3 import expand_origins
from cashflow_services.cli import account_data, service

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
HORIZON = 30
GRID = [
    DelayConfig(guard_days=g, condition=c, min_client=m)
    for g in (0, 45, 90)
    for c in (False, True)
    for m in (5, 10)
]
BIG = 10**9


def cfg_name(c: DelayConfig, global_only: bool = False) -> str:
    base = f"guard={c.guard_days},cond={int(c.condition)},min_client={c.min_client}"
    return base + (",global_only" if global_only else "")


def load_industries() -> dict:
    with (ROOT / "dataset" / "companies.csv").open(encoding="utf-8-sig") as f:
        return {r["company_id"]: r["industry"] for r in csv.DictReader(f, delimiter=";")}


# ---------------------------------------------------------------- покрытие парсером


def coverage(data: dict) -> dict:
    classes = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # direction -> class -> [rows, sum]
    totals = defaultdict(lambda: [0, 0])
    by_cat = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for txs, _ in data.values():
        for tx in txs:
            d, a = tx["direction"], tx["amount_minor"]
            totals[d][0] += 1
            totals[d][1] += a
            parsed = parse_purpose(tx["payment_purpose"])
            if parsed is None:
                cls = "no_reference"
            elif parsed.stage == "return":
                cls = "return"
            elif parsed.document is not None:
                cls = "dated_document"
            elif parsed.has_undated_document:
                cls = "undated_document"
            elif parsed.stage in ("prepayment", "settlement") and parsed.contract_number:
                cls = "contract_prepay_settle"
            else:
                cls = "contract_only"
            classes[d][cls][0] += 1
            classes[d][cls][1] += a
            if cls in ("dated_document", "contract_prepay_settle"):
                by_cat[d][tx["category"] + ":" + cls][0] += 1
                by_cat[d][tx["category"] + ":" + cls][1] += a
    result = {}
    for d in ("in", "out"):
        rows, amount = totals[d]
        result[d] = {
            "rows": rows,
            "amount_minor": amount,
            "classes": {
                k: {
                    "rows": v[0],
                    "amount_minor": v[1],
                    "row_share": v[0] / rows,
                    "amount_share": v[1] / amount,
                }
                for k, v in sorted(classes[d].items())
            },
            "by_category": {
                k: {"rows": v[0], "amount_minor": v[1], "amount_share": v[1] / amount}
                for k, v in sorted(by_cat[d].items(), key=lambda kv: -kv[1][1])
            },
        }
    return result


# ---------------------------------------------------------------- оценка


def wmedian(values, weights):
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    if values.size == 0:
        return None
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, cum[-1] / 2)])


def date_stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    err = np.asarray([r["err_days"] for r in rows], float)
    w = np.asarray([r["amount_minor"] for r in rows], float)
    ab = np.abs(err)
    return {
        "n": len(rows),
        "amount_minor": int(w.sum()),
        "bias_days_median": float(np.median(err)),
        "abs_err_median": float(np.median(ab)),
        "abs_err_mean": float(ab.mean()),
        "abs_err_p90": float(np.quantile(ab, 0.9)),
        "within_3d_count": float((ab <= 3).mean()),
        "within_7d_count": float((ab <= 7).mean()),
        "within_3d_amount": float(w[ab <= 3].sum() / w.sum()),
        "within_7d_amount": float(w[ab <= 7].sum() / w.sum()),
        "abs_err_median_amount_weighted": wmedian(ab, w),
    }


def run_windows(universe: DocumentUniverse, data: dict, origins: dict[str, list[date]]):
    windows, events = [], []
    for account, (txs, daily) in sorted(data.items()):
        tx_rows = [
            (
                date.fromisoformat(t["operation_date"]),
                t["amount_minor"],
                t["direction"],
                t["category"],
            )
            for t in txs
        ]
        for split, split_origins in origins.items():
            for origin in split_origins:
                if not eligible(daily, origin):
                    windows.append(
                        {
                            "split": split,
                            "account_id": account,
                            "origin": str(origin),
                            "eligible": False,
                        }
                    )
                    continue
                end = origin + timedelta(days=HORIZON)
                total = defaultdict(int)
                for d, a, direction, category in tx_rows:
                    if origin < d <= end:
                        total[direction] += a
                        if direction == "in" and not is_financing(category, direction):
                            total["in_ops"] += a
                ceil = defaultdict(int)
                for d, a, direction, _ in universe.owned_payments(account, origin):
                    if d <= end:
                        ceil[direction] += a
                pred = defaultdict(int)
                tp = defaultdict(int)
                for ev in universe.registry(account, origin):
                    future = [(d, a) for d, a in universe.actual(ev) if d > origin]
                    if not future:
                        continue
                    act_date = None
                    total_future = sum(a for _, a in future)
                    base = min(d for d, _ in future)
                    act_date = base + timedelta(
                        days=int(round(sum((d - base).days * a for d, a in future) / total_future))
                    )
                    in_win = sum(a for d, a in future if d <= end)
                    exp_in = origin < ev.date <= end
                    if exp_in:
                        pred[ev.direction] += ev.amount_minor
                        tp[ev.direction] += min(in_win, ev.amount_minor)
                    events.append(
                        {
                            "split": split,
                            "account_id": account,
                            "origin": str(origin),
                            "document_ref": ev.document_ref,
                            "component_id": ev.component_id,
                            "direction": ev.direction,
                            "category": ev.category,
                            "mechanism": ev.mechanism,
                            "level": ev.delay_level,
                            "delay_n": ev.delay_n,
                            "known_at": str(ev.known_at),
                            "expected": str(ev.date),
                            "actual": str(act_date),
                            "err_days": (ev.date - act_date).days,
                            "amount_minor": ev.amount_minor,
                            "actual_amount_minor": total_future,
                            "actual_in_window_minor": in_win,
                            "exp_in_window": exp_in,
                            "act_in_window": origin < act_date <= end,
                            "q10": ev.delay_quantiles["q10"],
                            "q50": ev.delay_quantiles["q50"],
                            "q90": ev.delay_quantiles["q90"],
                            "in_q10_q90": ev.window[0] <= act_date <= ev.window[1],
                        }
                    )
                windows.append(
                    {
                        "split": split,
                        "account_id": account,
                        "origin": str(origin),
                        "eligible": True,
                        "in_total": total["in"],
                        "in_ops_total": total["in_ops"],
                        "out_total": total["out"],
                        "ceil_in": ceil["in"],
                        "ceil_out": ceil["out"],
                        "pred_in": pred["in"],
                        "pred_out": pred["out"],
                        "tp_in": tp["in"],
                        "tp_out": tp["out"],
                    }
                )
    return windows, events


def summarize(windows: list[dict], events: list[dict], industries: dict) -> dict:
    out = {}
    for split in sorted({w["split"] for w in windows}):
        ws = [w for w in windows if w["split"] == split]
        ok = [w for w in ws if w["eligible"]]
        evs = [e for e in events if e["split"] == split]
        res = {
            "windows_total": len(ws),
            "windows_eligible": len(ok),
            "companies_eligible": len({w["account_id"] for w in ok}),
            "origins": len({w["origin"] for w in ok}),
        }
        for direction, tkey in (("in", "in_total"), ("out", "out_total")):
            T = sum(w[tkey] for w in ok)
            ceil, pred, tp = (
                sum(w[f"{k}_{direction}"] for w in ok) for k in ("ceil", "pred", "tp")
            )
            per_win = [w[f"ceil_{direction}"] / w[tkey] for w in ok if w[tkey] > 0]
            per_win_tp = [w[f"tp_{direction}"] / w[tkey] for w in ok if w[tkey] > 0]
            res[direction] = {
                "flow_minor": T,
                "flow_ex_financing_minor": sum(w["in_ops_total"] for w in ok)
                if direction == "in"
                else None,
                "ceiling_share": ceil / T if T else None,
                "ceiling_share_median_window": float(np.median(per_win)) if per_win else None,
                "predicted_in_window_share": pred / T if T else None,
                "hit_share": tp / T if T else None,
                "hit_share_median_window": float(np.median(per_win_tp)) if per_win_tp else None,
                "hit_share_of_ex_financing": (
                    tp / sum(w["in_ops_total"] for w in ok)
                    if direction == "in" and sum(w["in_ops_total"] for w in ok)
                    else None
                ),
                "precision_amount": tp / pred if pred else None,
                "recall_vs_ceiling": tp / ceil if ceil else None,
                "windows_with_flow": len(per_win),
                "n_windows_ceiling_positive": sum(1 for w in ok if w[f"ceil_{direction}"] > 0),
            }
            dir_events = [e for e in evs if e["direction"] == direction]
            res[direction]["events"] = len(dir_events)
            res[direction]["events_unique_documents"] = len({e["component_id"] for e in dir_events})
            res[direction]["date_error_all"] = date_stats(dir_events)
            res[direction]["date_error_actual_in_window"] = date_stats(
                [e for e in dir_events if e["act_in_window"]]
            )
            res[direction]["date_error_expected_in_window"] = date_stats(
                [e for e in dir_events if e["exp_in_window"]]
            )
            res[direction]["share_actual_inside_q10_q90"] = (
                float(np.mean([e["in_q10_q90"] for e in dir_events])) if dir_events else None
            )
            res[direction]["by_level"] = {
                lv: date_stats([e for e in dir_events if e["level"] == lv])
                for lv in ("client", "industry", "global", "fallback")
            }
            res[direction]["by_mechanism"] = {
                m: date_stats([e for e in dir_events if e["mechanism"] == m])
                for m in ("invoice", "contract_settlement")
            }
        contract = [e for e in evs if e["mechanism"] == "contract_settlement"]
        rel = [
            (e["amount_minor"] - e["actual_amount_minor"]) / e["actual_amount_minor"]
            for e in contract
            if e["actual_amount_minor"] > 0
        ]
        res["contract_amount_error"] = {
            "n": len(rel),
            "median_abs_rel": float(np.median(np.abs(rel))) if rel else None,
            "mean_abs_rel": float(np.mean(np.abs(rel))) if rel else None,
            "median_signed_rel": float(np.median(rel)) if rel else None,
            "within_10pct": float(np.mean(np.abs(rel) <= 0.10)) if rel else None,
            "p90_abs_rel": float(np.quantile(np.abs(rel), 0.9)) if rel else None,
        }
        inc = defaultdict(lambda: [0, 0, 0])
        for w in ok:
            ind = industries.get(w["account_id"], "?")
            inc[ind][0] += w["in_total"]
            inc[ind][1] += w["ceil_in"]
            inc[ind][2] += w["tp_in"]
        res["by_industry_in"] = {
            k: {
                "flow_minor": v[0],
                "ceiling_share": v[1] / v[0] if v[0] else None,
                "hit_share": v[2] / v[0] if v[0] else None,
            }
            for k, v in sorted(inc.items())
        }
        out[split] = res
    return out


def delay_distribution(universe: DocumentUniverse, industries: dict) -> dict:
    """Описательное распределение задержек по полному архиву (не для подбора параметров)."""
    result = {"invoice": {}, "contract_lag_days": {}, "contract_ratio": {}}
    for direction in ("in", "out"):
        delays = [
            max(0, (d.weighted_date() - d.doc_date).days)
            for d in universe.documents
            if d.direction == direction
        ]
        result["invoice"][direction] = _dist(delays)
        by_ind = defaultdict(list)
        for d in universe.documents:
            if d.direction == direction:
                by_ind[industries.get(d.account_id, "?")].append(
                    max(0, (d.weighted_date() - d.doc_date).days)
                )
        result["invoice"][direction]["by_industry"] = {
            k: _dist(v) for k, v in sorted(by_ind.items())
        }
    done = [c for c in universe.cycles if c.settle]
    result["contract_lag_days"] = _dist([(c.settle[0] - c.prepay[0]).days for c in done])
    result["contract_ratio"] = _dist([c.settle[1] / c.prepay[1] for c in done if c.prepay[1]])
    partial = [d for d in universe.documents if len(d.payments) > 1]
    result["partial_documents"] = {
        "documents": len(universe.documents),
        "with_several_payments": len(partial),
        "share": len(partial) / len(universe.documents),
    }
    return result


def _dist(values) -> dict:
    v = np.asarray(values, float)
    if v.size == 0:
        return {"n": 0}
    qs = {
        f"p{int(q * 100):02d}": float(np.quantile(v, q))
        for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
    }
    return {"n": int(v.size), "mean": float(v.mean()), **qs}


def main():
    started = time.time()
    p3 = json.loads((ROOT / "experiments" / "p3.json").read_text())
    origins = expand_origins(p3)
    svc = service()
    data = account_data(svc)
    industries = load_industries()
    print(f"счетов {len(data)}, операций {sum(len(t) for t, _ in data.values())}")

    cov = coverage(data)
    payments = []
    for account, (txs, _) in data.items():
        payments += extract_payments(txs, account)
    base = DocumentUniverse(payments, industries)
    cov["universe"] = {
        "documents": len(base.documents),
        "documents_in": sum(d.direction == "in" for d in base.documents),
        "documents_out": sum(d.direction == "out" for d in base.documents),
        "contract_cycles": len(base.cycles),
        "contract_cycles_closed": sum(c.settle is not None for c in base.cycles),
        "companies_with_documents": len({d.account_id for d in base.documents}),
        "companies_total": len(data),
        "owned_categories": owned_categories(),
    }
    (OUT / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1))
    print("покрытие готово", round(time.time() - started), "с")

    # калибровка параметров модели задержки: только калибровочные срезы
    calib = []
    for global_only in (False, True):
        for cfg in GRID:
            if global_only and cfg.min_client != 5:
                continue
            c = DelayConfig(
                cfg.guard_days,
                cfg.condition,
                BIG if global_only else cfg.min_client,
                BIG if global_only else cfg.min_industry,
                cfg.min_global,
                cfg.fallback_days,
            )
            uni = DocumentUniverse(payments, industries, c)
            _, events = run_windows(uni, data, {"calibration": origins["calibration"]})
            sel = [
                e
                for e in events
                if (date.fromisoformat(e["actual"]) - date.fromisoformat(e["origin"])).days <= 60
            ]
            stats = date_stats(sel)
            calib.append(
                {
                    "config": cfg_name(cfg, global_only),
                    "global_only": global_only,
                    "guard_days": cfg.guard_days,
                    "condition": cfg.condition,
                    "min_client": cfg.min_client,
                    **stats,
                }
            )
            print("калибровка", calib[-1]["config"], stats.get("abs_err_mean"), flush=True)
    hier = [c for c in calib if not c["global_only"]]
    best = min(hier, key=lambda c: (c["abs_err_mean"], c["abs_err_median"]))
    chosen = DelayConfig(best["guard_days"], best["condition"], best["min_client"])
    (OUT / "calibration.json").write_text(
        json.dumps(
            {
                "origins": [str(o) for o in origins["calibration"]],
                "criterion": "средняя абс. ошибка даты (дни) по событиям с фактической оплатой <= origin+60",
                "chosen": best["config"],
                "grid": calib,
            },
            ensure_ascii=False,
            indent=1,
        )
    )

    evaluation = {"config": cfg_name(chosen), "horizon_days": HORIZON}
    eval_origins = {k: origins[k] for k in ("selection", "retrospective")}
    uni = DocumentUniverse(payments, industries, chosen)
    windows, events = run_windows(uni, data, {**eval_origins})
    evaluation["main"] = summarize(windows, events, industries)
    # контроль: потолок из owned_payments совпадает с суммой фактических оплат событий
    check = defaultdict(int)
    for e in events:
        if e["direction"] == "in":
            check[(e["split"])] += e["actual_in_window_minor"]
    evaluation["ceiling_check_in"] = dict(check)
    # справочно: только глобальная задержка (без клиента и отрасли)
    go = DelayConfig(chosen.guard_days, chosen.condition, BIG, BIG, chosen.min_global)
    w2, e2 = run_windows(DocumentUniverse(payments, industries, go), data, {**eval_origins})
    evaluation["global_only"] = summarize(w2, e2, industries)
    # калибровочные срезы (справочно, параметры подбирались на них)
    w3, e3 = run_windows(uni, data, {"calibration": origins["calibration"]})
    evaluation["calibration_split"] = summarize(w3, e3, industries)
    (OUT / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=1))
    (OUT / "delay_distribution.json").write_text(
        json.dumps(delay_distribution(uni, industries), ensure_ascii=False, indent=1)
    )
    keys = list(events[0])
    sample = [e for i, e in enumerate(events) if i % 50 == 0]
    with (OUT / "events_sample.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(sample)
    with (OUT / "windows.csv").open("w", newline="", encoding="utf-8") as f:
        keys = sorted({k for w in windows for k in w})
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(windows)
    print("готово", round(time.time() - started), "с")


if __name__ == "__main__":
    main()
