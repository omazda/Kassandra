"""X4: структурная модель, матрица 2x2 (E03), польза источников (E09), решение по правилу D1.

Запуск из корня:  uv run python experiments/results/v3/x4/evaluate_x4.py [--skip-train]

Шаги:
1. HGB остатка по D3 (full - без вычитания механизмов, mech - с вычитанием), trained_to из p3.json.
2. Alpha затухающего тренда: сетка ALPHA_GRID, критерий - макро нормированная MAE траектории на
   КАЛИБРОВОЧНЫХ срезах (январь 2026); запись alpha.json. Срезы выбора и ретро не используются.
3. Прогон протокола X0 на всех срезах для всех методов; таблицы D1 с бутстрэпом по компаниям.
4. Польза источников: парные разности skill между вариантами structural, бутстрэп по компаниям.
5. Правило D1 для кандидатов против текущего default (weekday); решение пишется в registry.json.
Результаты: рядом с этим файлом (protocol/, results.json, report.md).
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from cashflow_ml import structural as S  # noqa: E402
from cashflow_ml.protocol_v3 import (  # noqa: E402
    METHODS,
    bootstrap_median,
    d1_decision,
    expand_origins,
    run_protocol,
)
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).resolve().parent
CURRENT_DEFAULT = "weekday"
METHOD_ORDER = [
    "constant",
    "shape_only",
    "weekday",
    "mech_weekday",
    "hgb_v2",
    "hgb_full",
    "hgb_mech",
    "structural_base",
    "structural_tax",
    "structural_docs",
    "structural",
]
TITLES = {
    "constant": "constant (ориентир)",
    "shape_only": "shape_only (ориентир)",
    "weekday": "A: простой, без календаря (weekday)",
    "mech_weekday": "B: простой остаток + механизмы",
    "hgb_v2": "hgb_v2 (артефакт v2, поток с финансированием)",
    "hgb_full": "C: HGB полный (D3, без механизмов)",
    "hgb_mech": "D: HGB остаточный + механизмы",
    "structural_base": "S1: structural без реестра и налогов",
    "structural_tax": "S1+налоги",
    "structural_docs": "S2: + реестр",
    "structural": "S3: + реестр + налоги",
}


def companies() -> dict:
    with open(ROOT / "dataset" / "companies.csv", encoding="utf-8-sig") as f:
        return {
            r["company_id"]: {"legal_form": r["legal_form"], "industry": r["industry"]}
            for r in csv.DictReader(f, delimiter=";")
        }


def say(text):
    print(text, flush=True)


def ensure_models(data, svc, config, p1):
    trained_to = date.fromisoformat(config["trained_to"])
    for variant in ("full", "mech"):
        try:
            meta = S.select_residual_hgb(svc.registry.root, trained_to, variant)
            say(f"HGB {variant}: уже есть {meta['model_version']}")
        except ValueError:
            t = time.time()
            meta = S.train_residual_hgb(data, trained_to, variant, svc.registry.root, p1)
            say(
                f"HGB {variant}: обучена {meta['model_version']}, окон {meta['training_windows']}, "
                f"{time.time() - t:.0f} с"
            )


def tune_alpha(data, svc, config, origins):
    """Выбор alpha по макро нормированной MAE траектории на калибровочных срезах."""
    methods = {"constant": METHODS["constant"]}
    for a in S.ALPHA_GRID:
        methods[f"a{a:g}"] = S.protocol_method(tax=True, docs=True, alpha=a)
    result = run_protocol(
        data,
        methods,
        {"calibration": origins["calibration"]},
        registry=svc.registry,
        config=config,
        n_jobs=2,
        progress=say,
    )
    table = {
        name: result["summary"]["calibration"][name]["normalized_mae"]
        for name in methods
        if name != "constant"
    }
    best = min(S.ALPHA_GRID, key=lambda a: (round(table[f"a{a:g}"], 6), a))
    payload = {
        "alpha": best,
        "phi": S.PHI,
        "grid_normalized_mae": {f"{a:g}": table[f"a{a:g}"] for a in S.ALPHA_GRID},
        "criterion": "macro normalized MAE of the 30-day trajectory, calibration slices only",
        "calibration_origins": [str(d) for d in origins["calibration"]],
        "windows": result["summary"]["calibration"]["a0"]["windows"],
        "companies": result["summary"]["calibration"]["a0"]["companies"],
    }
    S.ALPHA_FILE.parent.mkdir(parents=True, exist_ok=True)
    S.ALPHA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    say(f"alpha = {best}; сетка {payload['grid_normalized_mae']}")
    return payload


def paired(records, split, a, b, key, boot):
    """Медиана по компаниям разности skill(a) - skill(b) к constant, бутстрэп по компаниям."""
    rows = {m: [r for r in records if r["split"] == split and r["method"] == m] for m in (a, b)}
    const = {
        (r["account_id"], r["origin"]): r
        for r in records
        if r["split"] == split and r["method"] == "constant"
    }

    def skills(rs):
        per = {}
        m, c = defaultdict(float), defaultdict(float)
        for r in rs:
            m[r["account_id"]] += r[key]
            c[r["account_id"]] += const[(r["account_id"], r["origin"])][key]
        for acc in m:
            if c[acc] > 0:
                per[acc] = 1 - m[acc] / c[acc]
        return per

    sa, sb = skills(rows[a]), skills(rows[b])
    diffs = [sa[x] - sb[x] for x in sorted(sa) if x in sb]
    return bootstrap_median(diffs, boot["resamples"], boot["seed"])


def fmt_skill(entry, name):
    s = entry.get(name)
    if not s or s["median"] is None:
        return "—"
    return f"{s['median']:+.3f} [{s['ci_low']:+.3f}; {s['ci_high']:+.3f}]"


def f3(x):
    return "—" if x is None else f"{x:.3f}"


def split_table(summary, split):
    lines = [
        "| Метод | Окон | Компаний | Skill траект. | Skill миним. | Норм. MAE | Макро-MAE, руб | "
        "Событий | Найдено | Recall@7d | Ложных / счёт-мес | Дата ±3 дн. |",
        "|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in METHOD_ORDER:
        e = summary[split].get(m)
        if not e:
            continue
        r = e["risk"]
        lines.append(
            f"| {TITLES[m]} | {e['windows']} | {e['companies']} | {fmt_skill(e, 'skill_trajectory')} | "
            f"{fmt_skill(e, 'skill_minimum')} | {f3(e['normalized_mae'])} | "
            f"{e['macro_mae_rub']:,.0f} | {r['events_scorable']} | {r['events_detected']} | "
            f"{f3(r['recall_7d'])} | {f3(r['false_alarms_per_account_month'])} | "
            f"{f3(r['date_within_tolerance'])} (n={r['date_error_n']}) |"
        )
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-train", action="store_true")
    args = parser.parse_args()
    t0 = time.time()
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    p1 = json.loads((ROOT / "experiments/p1.json").read_text())
    os.chdir(ROOT)
    svc = service()
    data = account_data(svc)
    info = companies()
    universe = S.build_universe(data, {k: v["industry"] for k, v in info.items()})
    S.configure(universe, info)
    origins = expand_origins(config)
    say(
        f"данные: {len(data)} счетов; реестр: {len(universe.documents)} документов, "
        f"{len(universe.cycles)} договорных циклов ({time.time() - t0:.0f} с)"
    )
    if not args.skip_train:
        ensure_models(data, svc, config, p1)
    alpha = tune_alpha(data, svc, config, origins)
    a = alpha["alpha"]
    methods = {
        "constant": METHODS["constant"],
        "shape_only": METHODS["shape_only"],
        "weekday": METHODS["weekday"],
        "mech_weekday": S.simple_mech_method(),
        "hgb_v2": METHODS["hgb_v2"],
        "hgb_full": S.hgb_method("full"),
        "hgb_mech": S.hgb_method("mech"),
        "structural_base": S.protocol_method(tax=False, docs=False, alpha=a),
        "structural_tax": S.protocol_method(tax=True, docs=False, alpha=a),
        "structural_docs": S.protocol_method(tax=False, docs=True, alpha=a),
        "structural": S.protocol_method(tax=True, docs=True, alpha=a),
    }
    result = run_protocol(
        data,
        methods,
        origins,
        registry=svc.registry,
        config=config,
        out_dir=OUT / "protocol",
        n_jobs=2,
        progress=say,
    )
    summary, records = result["summary"], result["records"]
    boot = config["bootstrap"]

    decisions = {}
    for cand in METHOD_ORDER:
        if cand in ("constant", CURRENT_DEFAULT):
            continue
        decisions[cand] = d1_decision(summary["selection"], cand, CURRENT_DEFAULT, config)

    sources = {}
    pairs = [
        ("structural_docs", "structural_base", "+ реестр (без налогов)"),
        ("structural_tax", "structural_base", "+ налоги (без реестра)"),
        ("structural", "structural_docs", "+ налоги к реестру"),
        ("structural", "structural_tax", "+ реестр к налогам"),
        ("structural", "structural_base", "+ реестр + налоги"),
        ("mech_weekday", "weekday", "механизмы к weekday (B - A)"),
        ("hgb_mech", "hgb_full", "механизмы к HGB (D - C)"),
        ("structural_base", "mech_weekday", "тренд + события X2 без доп. (S1 - B)"),
    ]
    for split in ("selection", "retrospective"):
        for x, y, title in pairs:
            sources[f"{split}|{x}-{y}"] = {
                "title": title,
                "split": split,
                "skill_trajectory": paired(records, split, x, y, "mae_traj_minor", boot),
                "skill_minimum": paired(records, split, x, y, "mae_min_minor", boot),
            }

    # покрытие реестром: доля окон выбора, где реестр доступен
    docs_windows = sum(
        1 for r in records if r["split"] == "selection" and r["method"] == "structural"
    )
    out = {
        "alpha": alpha,
        "config": config,
        "summary": summary,
        "d1_vs_default": {"default": CURRENT_DEFAULT, "candidates": decisions},
        "source_gains": sources,
        "dropped_windows": len(result["dropped_windows"]),
        "manifest": result["manifest"]["parts"],
        "selection_windows_structural": docs_windows,
        "elapsed_sec": round(time.time() - t0),
    }
    (OUT / "results.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))

    lines = ["# X4: структурная модель, матрица 2x2, польза источников", ""]
    lines += [
        f"Цель D3. Срезы: калибровка {len(origins['calibration'])}, выбор "
        f"{len(origins['selection'])}, ретро {len(origins['retrospective'])}. "
        f"Alpha затухающего тренда = {a} (phi = {S.PHI}), выбрана на калибровке "
        f"({alpha['windows']} окон, {alpha['companies']} компаний). "
        "Skill к constant: медиана по компаниям, 95% бутстрэп по компаниям (1000, seed 42).",
        "",
    ]
    for split, title in (
        ("selection", "Выбор (мар-май 2026)"),
        ("retrospective", "Ретро (июль-август)"),
    ):
        lines += [f"## {title}", ""] + split_table(summary, split) + [""]
    lines += ["## Правило D1 против текущего default (weekday), срез выбора", ""]
    lines += [
        "| Кандидат | skill миним. > 0 | skill траект. >= -2% | ложные тревоги <= 1 | "
        "recall не хуже | Итог |",
        "|---|:-:|:-:|:-:|:-:|:-:|",
    ]
    for cand, d in decisions.items():
        c = d["checks"]
        lines.append(
            f"| {TITLES[cand]} | {'да' if c['skill_minimum_positive'] else 'нет'} | "
            f"{'да' if c['skill_trajectory_ge_minus_2pct'] else 'нет'} | "
            f"{'да' if c['false_alarm_budget'] else 'нет'} | "
            f"{'да' if c['recall_not_worse'] else 'нет'} | {'проходит' if d['pass'] else 'нет'} |"
        )
    lines += ["", "## Польза источников (парные разности skill, медиана по компаниям)", ""]
    lines += [
        "| Срез | Сравнение | Δ skill траект. | Δ skill миним. | Компаний |",
        "|---|---|---|---|---:|",
    ]
    for v in sources.values():
        t, m = v["skill_trajectory"], v["skill_minimum"]
        lines.append(
            f"| {v['split']} | {v['title']} | {t['median']:+.3f} [{t['ci_low']:+.3f}; {t['ci_high']:+.3f}] | "
            f"{m['median']:+.3f} [{m['ci_low']:+.3f}; {m['ci_high']:+.3f}] | {t['n_companies']} |"
        )
    lines += ["", f"Окон отброшено: {len(result['dropped_windows'])}.", ""]
    commentary = OUT / "commentary.md"
    if commentary.exists():
        lines += [commentary.read_text()]
    (OUT / "report.md").write_text("\n".join(lines) + "\n")
    say("\n".join(lines))
    say(f"готово за {time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
