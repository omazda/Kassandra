"""X8: короткая история и новые компании (E07).

Запуск из корня:  uv run python experiments/results/v3/x8/evaluate_x8.py [--skip-calibration]

1. Калибровка (только калибровочные срезы январь 2026, история обрезана до 14/30/60 дней):
   FULL_AT из {45, 90, 180} по нормированной MAE траектории, множитель интервала k из
   {0.75, 1, 1.25, 1.5, 2} по покрытию 80% (ближайшее к 0,8). Запись params.json.
2. Опыт A: срезы выбора, история обрезана до последних 14/30/60 дней (docs=False для всех
   вариантов structural: реестр документов построен по всему архиву, обрезать его честно нельзя).
3. Опыт B: естественные группы по длине истории: <7 / 7-29 / 30-89 (только реальные новые
   компании, открывшиеся после 2024-10-01) / 90-364 / >=365 дней; origin раз в 14 дней.
Результаты: report.md, results.json, windows.csv, registry_entry.json рядом с файлом.
"""

import argparse
import csv
import json
import multiprocessing
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from cashflow_domain.engine import assemble  # noqa: E402
from cashflow_ml import cold_start as C  # noqa: E402
from cashflow_ml import structural as S  # noqa: E402
from cashflow_ml.evaluation import eligible  # noqa: E402
from cashflow_ml.features import categories_at  # noqa: E402
from cashflow_ml.protocol_v3 import (  # noqa: E402
    AccountTruth,
    expand_origins,
    summarize,
    window_record,
)
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).resolve().parent
CUTS = (14, 30, 60)
FULL_AT_GRID = (45.0, 90.0, 180.0, 360.0, 720.0)
SHRINK_GRID = (0.25, 0.5, 0.75, 1.0)
K_GRID = (0.25, 0.35, 0.5, 0.75, 1.0, 1.5)
NATURAL_START = date(2024, 10, 6)
NATURAL_END = date(2026, 8, 31)
NATURAL_STEP = 14
ARCHIVE_START = "2024-10-01"
GROUPS = [
    ("<7", 0, 6),
    ("7-29", 7, 29),
    ("30-89", 30, 89),
    ("90-364", 90, 364),
    (">=365", 365, 10**6),
]
STATE: dict = {}


def say(text):
    print(text, flush=True)


def companies() -> dict:
    with open(ROOT / "dataset" / "companies.csv", encoding="utf-8-sig") as f:
        return {
            r["company_id"]: {"legal_form": r["legal_form"], "industry": r["industry"]}
            for r in csv.DictReader(f, delimiter=";")
        }


def group_of(n: int) -> str:
    for name, lo, hi in GROUPS:
        if lo <= n <= hi:
            return name
    raise ValueError(n)


def cold_balances(cf: C.ColdForecast, origin: date, opening: int) -> list[int]:
    points = assemble(origin, opening, np.maximum(0, cf.values), [])
    return [p.closing_balance_minor for p in points]


def forecast_of(result, origin, opening):
    from cashflow_ml.protocol_v3 import Forecast

    balances = S.balances_of(result, origin, opening)
    interval = result.meta.get("cold_start", {}).get("interval")
    if interval is None:
        return Forecast(balances, meta=result.meta)
    low, high = C.interval_bounds(balances, interval["half_width_minor"])
    return Forecast(balances, p10_minor=low, p90_minor=high, meta=result.meta)


def cold_forecast_obj(cf: C.ColdForecast, origin, opening):
    from cashflow_ml.protocol_v3 import Forecast

    balances = cold_balances(cf, origin, opening)
    low, high = C.interval_bounds(balances, cf.half_width_minor)
    return Forecast(balances, p10_minor=low, p90_minor=high, meta=cf.meta())


def constant_forecast(opening):
    from cashflow_ml.protocol_v3 import Forecast

    return Forecast([opening] * 30)


def make_record(split, account, origin, method, forecast, window, history, threshold, extra):
    rec = window_record(
        split,
        account,
        origin,
        method,
        forecast,
        window,
        S.history_scale(history, origin),
        threshold,
    )
    if forecast.p10_minor is not None:
        target = window["target"]
        inside = (target >= np.asarray(forecast.p10_minor)) & (
            target <= np.asarray(forecast.p90_minor)
        )
        rec["coverage"] = float(inside.mean())
        rec["half_width_rel"] = float(
            np.mean((np.asarray(forecast.p90_minor) - np.asarray(forecast.p10_minor)) / 2)
            / rec["scale_minor"]
        )
    else:
        rec["coverage"] = rec["half_width_rel"] = None
    rec.update(extra)
    return rec


# ---------------------------------------------------------------- калибровка


def _calibration_job(account):
    data, truth = STATE["data"][account], STATE["truth"][account]
    txs, daily = data
    industry = STATE["info"].get(account, {}).get("industry")
    rows = []
    for origin in STATE["origins"]["calibration"]:
        if not eligible(daily, origin):
            continue
        window = truth.window(origin)
        full = categories_at([t for t in txs if t["operation_date"] <= origin.isoformat()], origin)
        for cut in CUTS:
            lo = (origin - timedelta(days=cut - 1)).isoformat()
            hist = [t for t in full if t["operation_date"] >= lo]
            if C.history_days(hist, origin) < C.REFUSE_BELOW:
                continue
            scale = S.history_scale(hist, origin)
            for full_at in FULL_AT_GRID:
                for shrink in SHRINK_GRID:
                    for k in K_GRID:
                        cf = C.cold_forecast(
                            hist, origin, account, industry, full_at=full_at, k=k, shrink=shrink
                        )
                        f = cold_forecast_obj(cf, origin, window["opening_minor"])
                        err = np.abs(window["target"] - np.asarray(f.balances_minor))
                        inside = (window["target"] >= np.asarray(f.p10_minor)) & (
                            window["target"] <= np.asarray(f.p90_minor)
                        )
                        rows.append(
                            (full_at, shrink, k, float(err.mean() / scale), float(inside.mean()))
                        )
    return rows


def calibrate(data, truths, info, origins, config):
    STATE.update(data=data, truth=truths, info=info, origins=origins)
    rows = []
    with multiprocessing.get_context("fork").Pool(2) as pool:
        for r in pool.imap(_calibration_job, sorted(data), chunksize=1):
            rows += r
    table = defaultdict(list)
    for full_at, shrink, k, nmae, cov in rows:
        table[(full_at, shrink, k)].append((nmae, cov))
    grid = {
        f"{fa:g}|{sh:g}|{k:g}": {
            "normalized_mae": float(np.mean([x[0] for x in v])),
            "coverage": float(np.mean([x[1] for x in v])),
            "windows": len(v),
        }
        for (fa, sh, k), v in table.items()
    }
    # FULL_AT и shrink: по норм. MAE (k на точечный прогноз не влияет); k: покрытие ближе к 0,8
    best_full, best_shrink = min(
        ((fa, sh) for fa in FULL_AT_GRID for sh in SHRINK_GRID),
        key=lambda p: (round(grid[f"{p[0]:g}|{p[1]:g}|1"]["normalized_mae"], 6), p),
    )
    best_k = min(
        K_GRID,
        key=lambda k: (abs(grid[f"{best_full:g}|{best_shrink:g}|{k:g}"]["coverage"] - 0.8), k),
    )
    payload = {
        "full_at": best_full,
        "shrink": best_shrink,
        "k": best_k,
        "criterion": "full_at, shrink: macro normalized MAE; k: pointwise P10-P90 coverage closest "
        "to 0.8; calibration slices only, history cut to 14/30/60 days",
        "calibration_origins": [str(d) for d in origins["calibration"]],
        "windows": grid[f"{best_full:g}|{best_shrink:g}|{best_k:g}"]["windows"],
        "grid": grid,
    }
    (OUT / "params.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    say(
        f"full_at={best_full}, shrink={best_shrink}, k={best_k}, окон калибровки {payload['windows']}"
    )
    return payload


# ---------------------------------------------------------------- опыт A и B


def _evaluate_job(account):
    data, truth = STATE["data"][account], STATE["truth"][account]
    txs, daily = data
    info = STATE["info"].get(account, {})
    legal = info.get("legal_form")
    threshold = STATE["config"].get("threshold_minor", 0)
    first_op = min(t["operation_date"] for t in txs)
    new_company = first_op > ARCHIVE_START
    valid = {d["date"]: d["valid"] for d in daily}
    records, refused = [], []

    def base_methods(hist, origin, opening, with_docs):
        out = {"constant": constant_forecast(opening)}
        out["structural_base"] = forecast_of(
            S.structural_forecast(
                hist, origin, account, tax=True, docs=with_docs, legal_form=legal, cold=False
            ),
            origin,
            opening,
        )
        return out

    def cold_methods(hist, origin, opening):
        out = {}
        industry = info.get("industry")
        out["own_window"] = cold_forecast_obj(
            C.cold_forecast(hist, origin, account, industry, use_prior=False), origin, opening
        )
        out["prior_only"] = cold_forecast_obj(
            C.cold_forecast(hist, origin, account, industry, full_at=1e12), origin, opening
        )
        out["structural"] = forecast_of(
            S.structural_forecast(hist, origin, account, tax=True, docs=False, legal_form=legal),
            origin,
            opening,
        )
        return out

    # опыт A: обрезанная история на срезах выбора
    for origin in STATE["origins"]["selection"]:
        if not eligible(daily, origin):
            continue
        window = truth.window(origin)
        full = categories_at([t for t in txs if t["operation_date"] <= origin.isoformat()], origin)
        for cut in CUTS:
            lo = (origin - timedelta(days=cut - 1)).isoformat()
            hist = [t for t in full if t["operation_date"] >= lo]
            n = C.history_days(hist, origin)
            if n < C.REFUSE_BELOW:
                refused.append({"exp": "A", "group": f"cut{cut}", "account_id": account})
                continue
            methods = base_methods(hist, origin, window["opening_minor"], False)
            methods.update(cold_methods(hist, origin, window["opening_minor"]))
            for name, f in methods.items():
                records.append(
                    make_record(
                        "selection",
                        account,
                        origin,
                        name,
                        f,
                        window,
                        hist,
                        threshold,
                        {"exp": "A", "group": f"cut{cut}", "hist_days": n},
                    )
                )
    # опыт B: естественные группы
    first = date.fromisoformat(first_op)
    origin = NATURAL_START
    while origin <= NATURAL_END:
        step_origin, origin = origin, origin + timedelta(days=NATURAL_STEP)
        o = step_origin
        days = [str(o + timedelta(days=h)) for h in range(0, 31)]
        if not all(valid.get(d, False) for d in days) or o < first:
            continue
        n = (o - first).days + 1
        if n < C.COLD_BELOW and not new_company:
            continue  # левое усечение архива: «короткая история» старой компании не настоящая
        window = truth.window(o)
        full = categories_at([t for t in txs if t["operation_date"] <= o.isoformat()], o)
        grp = group_of(n)
        base = base_methods(full, o, window["opening_minor"], True)
        if n < C.REFUSE_BELOW:
            methods = base
            refused.append({"exp": "B", "group": grp, "account_id": account})
        elif n < C.COLD_BELOW:
            methods = dict(base)
            methods.update(cold_methods(full, o, window["opening_minor"]))
            # structural для естественной группы: с реестром документов как в продукте
            methods["structural"] = forecast_of(
                S.structural_forecast(full, o, account, tax=True, docs=True, legal_form=legal),
                o,
                window["opening_minor"],
            )
        else:
            methods = dict(base)
            methods["structural"] = base["structural_base"]
        for name, f in methods.items():
            records.append(
                make_record(
                    "selection",
                    account,
                    o,
                    name,
                    f,
                    window,
                    full,
                    threshold,
                    {"exp": "B", "group": grp, "hist_days": n, "new_company": new_company},
                )
            )
    return records, refused


def evaluate(data, truths, info, origins, config):
    STATE.update(data=data, truth=truths, info=info, origins=origins, config=config)
    records, refused = [], []
    with multiprocessing.get_context("fork").Pool(2) as pool:
        for i, (r, f) in enumerate(pool.imap(_evaluate_job, sorted(data), chunksize=1), 1):
            records += r
            refused += f
            if i % 25 == 0:
                say(f"{i}/{len(data)} счетов, записей {len(records)}")
    return records, refused


# ---------------------------------------------------------------- сводка


def summarise_groups(records, config, methods):
    out = {}
    cfg = {**config, "splits": {**config["splits"], "selection": {"step_days": 14}}}
    for exp in ("A", "B"):
        for grp in sorted({r["group"] for r in records if r["exp"] == exp}):
            rows = [r for r in records if r["exp"] == exp and r["group"] == grp]
            step_cfg = config if exp == "A" else cfg
            try:
                s = summarize(
                    rows, step_cfg, [m for m in methods if any(r["method"] == m for r in rows)]
                )
            except Exception as error:  # слишком мало данных в группе
                out[f"{exp}|{grp}"] = {"error": f"{type(error).__name__}: {error}"}
                continue
            entry = s.get("selection", {})
            for m, e in entry.items():
                cov = [
                    r["coverage"] for r in rows if r["method"] == m and r["coverage"] is not None
                ]
                e["coverage_mean"] = float(np.mean(cov)) if cov else None
                e["coverage_n"] = len(cov)
                hw = [r["half_width_rel"] for r in rows if r["method"] == m and r["half_width_rel"]]
                e["half_width_rel_mean"] = float(np.mean(hw)) if hw else None
            hist = [r["hist_days"] for r in rows if r["method"] == "constant"]
            out[f"{exp}|{grp}"] = {
                "methods": entry,
                "hist_days_median": float(np.median(hist)) if hist else None,
                "windows": len(hist),
                "companies": len({r["account_id"] for r in rows}),
            }
    return out


def f3(x):
    return "—" if x is None else f"{x:.3f}"


def skill(e, key):
    s = e.get(key)
    if not s or s.get("median") is None:
        return "—"
    return f"{s['median']:+.3f} [{s['ci_low']:+.3f}; {s['ci_high']:+.3f}]"


TITLES = {
    "constant": "constant",
    "structural_base": "structural без prior (как в продукте)",
    "own_window": "своя история, окно n дней, без prior",
    "prior_only": "только отраслевой prior",
    "structural": "structural с prior (X8)",
}
ORDER = ["constant", "structural_base", "own_window", "prior_only", "structural"]


def table(summary, exp, groups):
    lines = [
        "| Группа | Метод | Окон | Компаний | Норм. MAE | Skill траект. к constant | "
        "Skill миним. | Recall@7d (найдено/событий) | Ложных / счёт-мес | Покрытие P10-P90 |",
        "|---|---|---:|---:|---:|---|---|---|---:|---:|",
    ]
    for g in groups:
        item = summary.get(f"{exp}|{g}")
        if not item or "methods" not in item:
            continue
        for m in ORDER:
            e = item["methods"].get(m)
            if not e:
                continue
            r = e["risk"]
            lines.append(
                f"| {g} (медиана n={item['hist_days_median']:.0f}) | {TITLES[m]} | {e['windows']} | "
                f"{e['companies']} | {f3(e['normalized_mae'])} | {skill(e, 'skill_trajectory')} | "
                f"{skill(e, 'skill_minimum')} | {f3(r['recall_7d'])} "
                f"({r['events_detected']}/{r['events_scorable']}) | "
                f"{f3(r['false_alarms_per_account_month'])} | {f3(e['coverage_mean'])} |"
            )
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-calibration", action="store_true")
    args = parser.parse_args()
    t0 = time.time()
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    os.chdir(ROOT)
    svc = service()
    data = account_data(svc)
    info = companies()
    industries = {k: v["industry"] for k, v in info.items()}
    universe = S.build_universe(data, industries)
    S.configure(universe, info)
    C.configure(data, industries)
    origins = expand_origins(config)
    truths = {a: AccountTruth(t, d, config.get("threshold_minor", 0)) for a, (t, d) in data.items()}
    say(f"данные: {len(data)} счетов, доноры {len(C._ARR)} ({time.time() - t0:.0f} с)")
    if not args.skip_calibration:
        calibrate(data, truths, info, origins, config)
    C._STATS.clear()
    par = C.params()
    say(f"параметры: {par}")
    records, refused = evaluate(data, truths, info, origins, config)
    summary = summarise_groups(records, config, ORDER)
    # реальные новые компании отдельно (опыт B, группы <90)
    new_ids = sorted({r["account_id"] for r in records if r["exp"] == "B" and r["new_company"]})
    result = {
        "params": par,
        "summary": summary,
        "refused": {
            f"{e}|{g}": sum(1 for x in refused if x["exp"] == e and x["group"] == g)
            for e, g in sorted({(x["exp"], x["group"]) for x in refused})
        },
        "new_companies_with_windows": len(
            {
                r["account_id"]
                for r in records
                if r["exp"] == "B" and r["new_company"] and r["hist_days"] < 90
            }
        ),
        "runtime_s": time.time() - t0,
    }
    (OUT / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    keys = [k for k in records[0] if k not in ("path", "p10", "p90", "event_offsets")]
    with open(OUT / "windows.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    lines = ["# X8: короткая история и новые компании", ""]
    lines += ["## Опыт A: история обрезана на срезах выбора", ""]
    lines += table(summary, "A", ["cut14", "cut30", "cut60"]) + [""]
    lines += ["## Опыт B: естественные группы по длине истории", ""]
    lines += table(summary, "B", [g[0] for g in GROUPS]) + [""]
    lines += [
        "## Отказы (история < 7 дней)",
        "",
        f"`{json.dumps(result['refused'], ensure_ascii=False)}`",
        "",
    ]
    (OUT / "tables.md").write_text("\n".join(lines))
    say(f"готово за {time.time() - t0:.0f} с, записей {len(records)}, new={len(new_ids)}")


if __name__ == "__main__":
    main()
