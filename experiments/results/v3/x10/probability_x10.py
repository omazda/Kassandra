"""X10: вероятность разрыва и полоса P10-P90 по банку ошибок, как их считает продукт.

Запуск из корня (после evaluate_x10.py и `cashflow build-ensemble`):
    uv run python experiments/results/v3/x10/probability_x10.py

Продукт (cashflow_ml.risk.add_uncertainty) берёт целые 30-дневные траектории ошибок из банка
(январь 2026, доступны только с available_to <= as_of), 1000 выборок с seed 42, и прибавляет их
к центральному пути. Здесь то же самое повторено на окнах выбора и ретро из protocol/:
- ens_bal — банк artifacts/ensemble/error_bank.json (цель D3, 15 январских срезов);
- hgb_cal / hgb_v2 — банк artifacts/error_bank.json (v2: наблюдаемый остаток, 5 срезов).
Метки: событие D2 в окне (как probability_metrics протокола), окна с текущим дефицитом исключены.
Покрытие P10-P90 считается по цели D3 (дни окна). Результат: probability.json.
"""

import csv
import gzip
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from cashflow_ml.calibration import dense_calibration_origins  # noqa: E402
from cashflow_ml.protocol_v3 import AccountTruth, probability_metrics  # noqa: E402
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).resolve().parent
BANKS = {
    "ens_bal": ("ensemble/error_bank.json", None),
    "hgb_cal": ("error_bank.json", "hgb|hgb-2025-12-31-residual-ca7d360845|calendar=True"),
    "hgb_v2": ("error_bank.json", "hgb|hgb-2025-12-31-full-255b703fbe|calendar=False"),
}


def load_bank(name, recipe):
    rows = json.loads((ROOT / "artifacts" / name).read_text())
    if recipe is None:
        recipes = {r["recipe"] for r in rows}
        assert len(recipes) == 1, recipes
        return rows, recipes.pop()
    return [r for r in rows if r["recipe"] == recipe], recipe


def main():
    os.chdir(ROOT)
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    svc = service()
    data = account_data(svc)
    truths = {a: AccountTruth(txs, daily, 0) for a, (txs, daily) in data.items()}
    windows = {}
    with (OUT / "protocol/windows.csv").open() as f:
        for r in csv.DictReader(f):
            if r["method"] in BANKS:
                windows[(r["split"], r["account_id"], r["origin"], r["method"])] = r
    paths = {}
    with gzip.open(OUT / "protocol/paths.csv.gz", "rt") as f:
        for r in csv.DictReader(f):
            if r["method"] in BANKS:
                key = (r["split"], r["account_id"], r["origin"], r["method"])
                paths[key] = np.asarray([int(r[f"d{h}"]) for h in range(1, 31)], float)
    # Базовая частота января (окна без текущего дефицита с событием D2) — ориентир для Brier.
    jan = []
    for origin in dense_calibration_origins(config):
        for a, (_, daily) in data.items():
            rows = {d["date"]: d for d in daily}
            if not all(
                rows.get(str(origin + timedelta(days=h)), {}).get("valid") for h in range(-89, 31)
            ):
                continue
            w = truths[a].window(origin)
            if not w["current_deficit"]:
                jan.append(bool(w["event_offsets"]))
    jan_rate = float(np.mean(jan))
    report = {"calibration_rate_jan_dense": jan_rate, "jan_windows": len(jan), "methods": {}}
    for method, (file, recipe) in BANKS.items():
        bank, recipe = load_bank(file, recipe)
        report["methods"][method] = {"bank": file, "recipe": recipe, "bank_paths": len(bank)}
        for split in ("selection", "retrospective"):
            rows, inside, total = [], 0, 0
            for key, w in windows.items():
                if key[0] != split or key[3] != method:
                    continue
                origin = date.fromisoformat(key[2])
                eligible = [b for b in bank if b["available_to"] <= key[2]]
                if len(eligible) < 20 or len({b["account_id"] for b in eligible}) < 5:
                    continue
                rng = np.random.default_rng(42)
                pick = rng.integers(0, len(eligible), size=1000)
                errors = np.asarray([eligible[i]["normalized_balance_error"] for i in pick])
                sims = paths[key][None, :] + errors * float(w["scale_minor"])
                p = float(np.mean(sims.min(1) < 0))
                lo, hi = np.quantile(sims, [0.1, 0.9], axis=0)
                target = truths[key[1]].window(origin)["target"]
                inside += int(np.sum((target >= lo) & (target <= hi)))
                total += 30
                rows.append(
                    {
                        "current_deficit": w["current_deficit"] == "True",
                        "event_offsets": json.loads(w["event_offsets"]),
                        "p_gap_30d": p,
                    }
                )
            metrics = probability_metrics(rows, jan_rate, config.get("reliability_bins", 10))
            metrics["p10_p90_coverage_d3"] = inside / total if total else None
            metrics["max_reliability_gap_bins_ge_30"] = max(
                (
                    abs(b["mean_p"] - b["observed"])
                    for b in metrics["reliability"]
                    if b["n"] >= 30 and b["mean_p"] is not None
                ),
                default=None,
            )
            report["methods"][method][split] = metrics
            print(
                method,
                split,
                f"n={metrics['n']} brier={metrics['brier']:.4f} "
                f"skill_vs_jan={metrics['vs_calibration_rate']['brier_skill']:+.3f} "
                f"coverage={metrics['p10_p90_coverage_d3']:.3f} "
                f"max_rel_gap={metrics['max_reliability_gap_bins_ge_30']}",
                flush=True,
            )
    (OUT / "probability.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
