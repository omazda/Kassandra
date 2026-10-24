"""X11: gap alarm rule and probability calibration for the ensemble, fitted on selection only.

Windows: every company of the main dataset, origins every 14 days from 2026-03-01 (selection
slice of protocol v3, before July), 30-day horizon fully known, start balance not negative.
Label — protocol D2: a negative end-of-day balance or financing that rescued the day
(AccountTruth), so a gap the owner closed with own money is still a gap.
Fitted: isotonic calibration of the gap probability and the alarm threshold that maximises F1.
Then reported once on the retro slice (2026-07-01 … 2026-08-26) without refitting.
Writes artifacts/gap_alarm.json and results.json next to this script.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression

from cashflow_domain.schemas import ForecastRequest
from cashflow_ml.protocol_v3 import AccountTruth
from cashflow_services.service import Service

OUT = Path(__file__).parent
ROOT = Path(__file__).resolve().parents[4]


def windows(svc, origins):
    rows = []
    accounts = [
        a for a in svc.accounts({"id": "local", "accounts": ["*"]}) if a["owner"] != "debug"
    ]
    for account in accounts:
        rows_daily = svc.store.daily(account["id"])
        daily = {d["date"]: d for d in rows_daily}
        truth = AccountTruth(svc.store.transactions(account["id"]), rows_daily)
        for origin in origins:
            start = daily.get(str(origin))
            end = origin + timedelta(days=30)
            if start is None or str(end) not in daily or start["balance_minor"] < 0:
                continue
            try:
                f = svc.forecast(
                    ForecastRequest(account_id=account["id"], as_of=origin, model="ensemble")
                )
            except ValueError:
                continue
            if f.model != "ensemble" or f.risk.probability_30d is None:
                continue
            actual = bool(truth.window(origin)["event_offsets"])
            rows.append(
                {
                    "account": account["id"],
                    "origin": str(origin),
                    "p": f.risk.probability_30d,
                    "trajectory": f.risk.first_gap_date is not None,
                    "y": actual,
                }
            )
    return rows


def scores(pred, y):
    tp = int(np.sum(pred & y))
    fp = int(np.sum(pred & ~y))
    fn = int(np.sum(~pred & y))
    tn = int(np.sum(~pred & ~y))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round((tp + tn) / len(y), 4),
    }


def main():
    svc = Service(
        sys.argv[1] if len(sys.argv) > 1 else ROOT / "data/cashflow.sqlite", ROOT / "artifacts"
    )
    selection = [date(2026, 3, 1) + timedelta(days=14 * k) for k in range(7)]
    retro = [date(2026, 7, 1) + timedelta(days=14 * k) for k in range(5)]
    fit = windows(svc, selection)
    p = np.array([r["p"] for r in fit])
    y = np.array([r["y"] for r in fit])
    traj = np.array([r["trajectory"] for r in fit])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p, y.astype(float))
    cal = iso.predict(p)
    grid = [round(t, 3) for t in np.arange(0.02, 0.62, 0.02)]
    best = max(grid, key=lambda t: (scores(cal >= t, y)["f1"], scores(cal >= t, y)["precision"]))
    artifact = {
        "version": "x11-d2-2026-05-31",
        "fitted_on": "selection 2026-03-01…2026-05-24, ensemble, label D2, windows with non-negative start",
        "available_to": str(selection[-1] + timedelta(days=30)),
        "windows": len(fit),
        "positives": int(y.sum()),
        "threshold": best,
        "iso_x": [float(v) for v in iso.X_thresholds_],
        "iso_y": [float(v) for v in iso.y_thresholds_],
    }
    (ROOT / "artifacts/gap_alarm.json").write_text(json.dumps(artifact, indent=1))
    test = windows(svc, retro)
    pt = np.array([r["p"] for r in test])
    yt = np.array([r["y"] for r in test])
    tt = np.array([r["trajectory"] for r in test])
    ct = iso.predict(pt)

    def brier(q, labels):
        return round(float(np.mean((q - labels) ** 2)), 4)

    result = {
        "artifact": {
            k: artifact[k]
            for k in ("version", "fitted_on", "available_to", "windows", "positives", "threshold")
        },
        "selection": {
            "trajectory_rule": scores(traj, y),
            "calibrated_alarm": scores(cal >= best, y),
            "brier_raw": brier(p, y),
            "brier_calibrated": brier(cal, y),
            "base_rate": round(float(y.mean()), 4),
        },
        "retro": {
            "windows": len(test),
            "positives": int(yt.sum()),
            "trajectory_rule": scores(tt, yt),
            "calibrated_alarm": scores(ct >= best, yt),
            "brier_raw": brier(pt, yt),
            "brier_calibrated": brier(ct, yt),
            "base_rate": round(float(yt.mean()), 4),
        },
    }
    (OUT / "results.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
