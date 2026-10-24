import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from cashflow_domain.engine import assemble
from cashflow_ml.models import predict
from cashflow_ml.regularity import predicted_events


def eligible(daily, origin):
    mapping = {d["date"]: d for d in daily}
    return all(
        mapping.get(str(origin + timedelta(days=h)), {}).get("valid", False) for h in range(-89, 31)
    )


def episode_manifest(account_data):
    episodes = []
    for account, (_, daily) in account_data.items():
        start, previous = None, None
        for d in daily:
            if d["valid"] and d["balance_minor"] < 0:
                if start is None:
                    start = d["date"]
                previous = d["date"]
            elif start is not None:
                episodes.append({"account_id": account, "start": start, "end": previous})
                start = None
        if start:
            episodes.append({"account_id": account, "start": start, "end": previous})
    return episodes


def evaluate(account_data, registry, root, config, progress=print):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    bank, metrics, predictions, split_windows = [], [], [], []
    groups = {
        "calibration": config["calibration_origins"],
        "selection": config["selection_origins"],
        "retrospective": config["retrospective_origins"],
    }
    for split, origins in groups.items():
        for text in origins:
            origin = date.fromisoformat(text)
            accepted = 0
            for account, (txs, daily) in sorted(account_data.items()):
                if not eligible(daily, origin):
                    continue
                accepted += 1
                by_date = {d["date"]: d for d in daily}
                opening = by_date[text]["balance_minor"]
                target = [by_date[str(origin + timedelta(days=h))] for h in range(1, 31)]
                actual_balance = np.asarray([d["balance_minor"] for d in target])
                actual_in = np.asarray([d["inflow_minor"] for d in target])
                actual_out = np.asarray([d["outflow_minor"] for d in target])
                split_windows.append(
                    {
                        "split": split,
                        "account_id": account,
                        "origin": text,
                        "target_end": str(origin + timedelta(days=30)),
                        "new_gap_window": bool(opening >= 0 and np.any(actual_balance < 0)),
                    }
                )
                modes = [False, True] if split == "calibration" else [False]
                for calendar in modes:
                    for model in ("constant", "mean", "weekday", "hgb"):
                        values, comps, scale, version, _ = predict(
                            txs, origin, model, calendar, registry
                        )
                        events = predicted_events(comps, account, origin)
                        points = assemble(origin, opening, values, events)
                        pred_balance = np.asarray([p.closing_balance_minor for p in points])
                        pred_in = np.asarray(
                            [p.event_in_minor + p.residual_in_minor for p in points]
                        )
                        pred_out = np.asarray(
                            [p.event_out_minor + p.residual_out_minor for p in points]
                        )
                        error = actual_balance - pred_balance
                        if split == "calibration":
                            bank.append(
                                {
                                    "account_id": account,
                                    "origin": text,
                                    "available_to": str(origin + timedelta(days=30)),
                                    "recipe": f"{model}|{version}|calendar={calendar}",
                                    "normalized_balance_error": (error / scale).tolist(),
                                }
                            )
                        else:
                            record = {
                                "split": split,
                                "account_id": account,
                                "origin": text,
                                "model": model,
                                "calendar": calendar,
                                "trajectory_mae_rub": float(np.mean(np.abs(error)) / 100),
                                "day30_mae_rub": float(abs(error[-1]) / 100),
                                "inflow_mae_rub": float(np.mean(np.abs(actual_in - pred_in)) / 100),
                                "outflow_mae_rub": float(
                                    np.mean(np.abs(actual_out - pred_out)) / 100
                                ),
                                "minimum_mae_rub": float(
                                    abs(np.min(actual_balance) - np.min(pred_balance)) / 100
                                ),
                                "normalized_mae": float(np.mean(np.abs(error)) / scale),
                                "actual_new_gap": bool(opening >= 0 and np.any(actual_balance < 0)),
                                "predicted_new_gap": bool(
                                    opening >= 0 and np.any(pred_balance < 0)
                                ),
                            }
                            metrics.append(record)
                            for i, p in enumerate(points):
                                predictions.append(
                                    {
                                        "split": split,
                                        "account_id": account,
                                        "origin": text,
                                        "model": model,
                                        "date": str(p.date),
                                        "actual_balance_minor": int(actual_balance[i]),
                                        "predicted_balance_minor": p.closing_balance_minor,
                                        "actual_in_minor": int(actual_in[i]),
                                        "predicted_in_minor": int(pred_in[i]),
                                        "actual_out_minor": int(actual_out[i]),
                                        "predicted_out_minor": int(pred_out[i]),
                                    }
                                )
            progress(f"{split} {text}: {accepted} eligible accounts")
    summary = {}
    metric_names = [
        "trajectory_mae_rub",
        "day30_mae_rub",
        "inflow_mae_rub",
        "outflow_mae_rub",
        "minimum_mae_rub",
        "normalized_mae",
    ]
    for split in ("selection", "retrospective"):
        summary[split] = {}
        for model in ("constant", "mean", "weekday", "hgb"):
            rows = [r for r in metrics if r["split"] == split and r["model"] == model]
            companies = sorted({r["account_id"] for r in rows})
            macro = (
                {
                    key: float(
                        np.mean(
                            [
                                np.mean([r[key] for r in rows if r["account_id"] == a])
                                for a in companies
                            ]
                        )
                    )
                    for key in metric_names
                }
                if rows
                else {}
            )
            macro.update(
                windows=len(rows),
                companies=len(companies),
                new_gap_windows=sum(r["actual_new_gap"] for r in rows),
                detected_gap_windows=sum(
                    r["actual_new_gap"] and r["predicted_new_gap"] for r in rows
                ),
                false_alarm_windows=sum(
                    not r["actual_new_gap"] and r["predicted_new_gap"] for r in rows
                ),
            )
            summary[split][model] = macro
    results = {
        "protocol": config,
        "summary": summary,
        "limitations": [
            "Ретроспективная проверка на доступной синтетике; не скрытый тест",
            "Оконные рисковые метрики зависимы и не являются событийным recall",
            "Календарь в основном сравнении выключен; матрица 2x2 остаётся E03",
            "Категории не имеют ручного эталона; CatBoost не обучался",
        ],
    }
    episodes = episode_manifest(account_data)
    support = {}
    for split in groups:
        windows = [w for w in split_windows if w["split"] == split]
        negative = [
            e
            for e in episodes
            if any(
                e["account_id"] == w["account_id"]
                and w["origin"] < e["start"] <= w["target_end"]
                and w["new_gap_window"]
                for w in windows
            )
        ]
        support[split] = {
            "eligible_windows": len(windows),
            "eligible_companies": len({w["account_id"] for w in windows}),
            "new_gap_windows": sum(w["new_gap_window"] for w in windows),
            "distinct_new_segments": len(negative),
        }
    support_report = {
        "splits": support,
        "negative_segments_total": len(episodes),
        "companies_with_negative_segments": len({e["account_id"] for e in episodes}),
        "limitation": "Invalid coverage breaks a segment. Segment count is not a calibrated count of independent risk episodes.",
    }
    for filename, data in [
        ("metrics.json", results),
        ("split_manifest.json", split_windows),
        ("window_metrics.json", metrics),
        ("episodes.json", episodes),
        ("split_support.json", support_report),
    ]:
        (root / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    for filename, rows in [("predictions.csv", predictions)]:
        if rows:
            with (root / filename).open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return results, bank
