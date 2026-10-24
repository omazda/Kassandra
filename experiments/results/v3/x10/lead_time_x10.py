"""X10: заблаговременность найденных разрывов (дни от первой верной тревоги до события).

Запуск из корня после evaluate_x10.py:  uv run python experiments/results/v3/x10/lead_time_x10.py

Событие D2 с заблаговременностью >= 7 дней найдено, если в каком-то окне без текущего дефицита
дата первой тревоги отличается от даты события не больше чем на 3 дня (как risk_metrics
протокола). Заблаговременность события — наибольшее число дней от среза до события среди
окон, которые его нашли (самое раннее верное предупреждение). Результат: lead_time.json.
"""

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent
LOW, TOLERANCE = 7, 3


def main():
    caught = defaultdict(dict)  # (split, method) -> event -> lead days
    with (OUT / "protocol/windows.csv").open() as f:
        for r in csv.DictReader(f):
            if r["current_deficit"] == "True" or r["alarm"] != "True" or not r["alarm_offset"]:
                continue
            alarm = int(r["alarm_offset"])
            origin = date.fromisoformat(r["origin"])
            for offset in json.loads(r["event_offsets"]):
                if offset >= LOW and abs(alarm - offset) <= TOLERANCE:
                    event = (r["account_id"], str(origin + timedelta(days=offset)))
                    key = (r["split"], r["method"])
                    caught[key][event] = max(caught[key].get(event, 0), offset)
    report = {}
    for (split, method), events in sorted(caught.items()):
        leads = np.asarray(list(events.values()))
        report.setdefault(split, {})[method] = {
            "events_detected": len(leads),
            "lead_days_median": float(np.median(leads)),
            "lead_days_mean": float(leads.mean()),
            "lead_days_min": int(leads.min()),
            "lead_days_max": int(leads.max()),
            "share_lead_ge_14": float(np.mean(leads >= 14)),
        }
        print(split, method, report[split][method])
    (OUT / "lead_time.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
