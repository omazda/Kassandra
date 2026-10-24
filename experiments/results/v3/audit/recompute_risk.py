"""Пересчёт событийных метрик с привязкой даты тревоги к дате события (находка аудита).

Читает сохранённые windows.csv (окна и истина не меняются) и заново считает summary и D1.
Запуск из корня: uv run python experiments/results/v3/audit/recompute_risk.py
"""

import csv
import json
import sys
from pathlib import Path

from cashflow_ml.protocol_v3 import d1_decision, summarize

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).parent
FLOAT = ["opening_minor", "scale_minor", "mae_traj_minor", "mae_d30_minor", "mae_min_minor",
         "norm_mae", "obs_mae_traj_minor", "obs_mae_d30_minor", "obs_mae_min_minor",
         "actual_min_minor", "pred_min_minor", "pred_deficit_minor", "actual_deficit_minor",
         "p_gap_30d"]  # fmt: skip


def load(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        for k in FLOAT:
            r[k] = float(r[k]) if r.get(k) not in (None, "") else None
        r["current_deficit"] = r["current_deficit"] == "True"
        r["alarm"] = r["alarm"] == "True"
        r["alarm_offset"] = int(float(r["alarm_offset"])) if r["alarm_offset"] else None
        r["event_offsets"] = json.loads(r["event_offsets"])
        r["first_event_offset"] = (
            int(float(r["first_event_offset"])) if r["first_event_offset"] else None
        )
        rows.append(r)
    return rows


def main():
    config = json.load(open(ROOT / "experiments/p3.json", encoding="utf-8"))
    sources = {
        "x0": ("protocol", None),
        "x4": ("x4/protocol", "weekday"),
        "x5": ("x5/protocol", "structural_profile"),
    }
    result = {}
    for task, (sub, default) in sources.items():
        records = load(ROOT / "experiments/results/v3" / sub / "windows.csv")
        methods = sorted({r["method"] for r in records})
        summary = summarize(records, config, methods)
        entry = {"summary": {}, "d1": {}}
        for split, by_method in summary.items():
            entry["summary"][split] = {
                m: {
                    "skill_trajectory": v["skill_trajectory"]["median"]
                    if "skill_trajectory" in v
                    else None,
                    "skill_minimum": v["skill_minimum"]["median"] if "skill_minimum" in v else None,
                    "risk": v["risk"],
                }
                for m, v in by_method.items()
            }
        if default:
            for m in methods:
                if m not in (default, "constant"):
                    entry["d1"][m] = d1_decision(summary["selection"], m, default, config)
        result[task] = entry
    (OUT / "risk_recomputed.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for task, e in result.items():
        print("==", task)
        for split in ("selection", "retrospective"):
            for m, v in e["summary"][split].items():
                r = v["risk"]
                print(
                    split,
                    m,
                    f"recall {r['recall_7d']:.3f} ({r['events_detected']}/{r['events_scorable']})"
                    if r["recall_7d"] is not None
                    else "-",
                    f"fa {r['false_alarms_per_account_month']:.3f}",
                    f"datetol {r['date_within_tolerance']}",
                )
        for m, d in e["d1"].items():
            print("D1", m, d["pass"], [k for k, v in d["checks"].items() if not v])


if __name__ == "__main__":
    sys.exit(main())
