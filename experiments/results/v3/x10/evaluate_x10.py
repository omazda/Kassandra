"""X10: ансамбль с весами по горизонту (src/cashflow_ml/ensemble.py) на протоколе v3.

Запуск из корня:  uv run python experiments/results/v3/x10/evaluate_x10.py

Шаги:
1. Набор подбора (до срезов выбора): июль-декабрь 2025 на HGB, обученных до 2025-06-30,
   и плотный январь 2026 на боевых моделях. Тот же код, что в `cashflow build-ensemble`.
2. Кросс-проверка формы на наборе подбора (июль-дек -> январь, январь -> июль-дек, по
   компаниям): fitset_cv.json. Срезы выбора и ретро здесь не используются.
3. Веса вариантов рецепта (traj: lam 0, bal: lam 0,5) по всему набору: weights_<variant>.json.
4. Один прогон протокола на срезах выбора и ретро: constant, weekday, shape_only, hgb_v2,
   hgb_cal (путь продукта по умолчанию), ens_traj, ens_bal -> protocol/.
5. Парные разности, D1 против hgb_v2, предрегистрированное правило -> results.json, report.md.
"""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from cashflow_ml import ensemble as E  # noqa: E402
from cashflow_ml.protocol_v3 import (  # noqa: E402
    METHODS,
    bootstrap_median,
    d1_decision,
    expand_origins,
    run_protocol,
)
from cashflow_services.cli import account_data, dataset_digest, service  # noqa: E402

OUT = Path(__file__).resolve().parent
DEFAULT = "hgb_v2"
ORDER = ["constant", "weekday", "shape_only", "hgb_v2", "hgb_cal", "ens_traj", "ens_bal"]
TITLES = {
    "constant": "constant (ориентир)",
    "weekday": "weekday",
    "shape_only": "shape_only",
    "hgb_v2": "hgb_v2 (default по D1)",
    "hgb_cal": "hgb с календарём (путь продукта)",
    "ens_traj": "ансамбль traj (lam 0)",
    "ens_bal": "ансамбль bal (lam 0,5)",
}


def say(text):
    print(text, flush=True)


# ---------------------------------------------------------------- кросс-проверка на наборе подбора


def fit_skills(fs, pred, mask):
    """Медиана skill траектории и минимума к constant по компаниям (окна mask)."""
    y, o, acc = fs["target"], fs["opening"], fs["accounts"]
    err, base = np.abs(y - pred).mean(1), np.abs(y - o[:, None]).mean(1)
    emin, bmin = np.abs(y.min(1) - pred.min(1)), np.abs(y.min(1) - o)
    t, m = [], []
    for a in np.unique(acc[mask]):
        k = mask & (acc == a)
        if base[k].sum() > 0:
            t.append(1 - err[k].sum() / base[k].sum())
        if bmin[k].sum() > 0:
            m.append(1 - emin[k].sum() / bmin[k].sum())
    return {"trajectory": float(np.median(t)), "minimum": float(np.median(m))}


def fitset_cv(fitset):
    core = {k: v for k, v in fitset.items() if k not in ("meta", "part")}
    spec = [tuple(s) for s in E.RECIPE["spec"]]
    fold, jan = fitset["part"] == "fold", fitset["part"] == "jan"
    companies = np.unique(fitset["accounts"])
    out = {"base": {}, "variants": {}}
    for name in ("shape", "hgb_v2", "hgb_cal", "weekday"):
        pred = core["opening"][:, None] + core["deltas"][name]
        out["base"][name] = {
            "fold": fit_skills(core, pred, fold),
            "jan": fit_skills(core, pred, jan),
        }
    for variant, lam in E.RECIPE["variants"].items():
        row = {}
        for title, tr, te in (("fold->jan", fold, jan), ("jan->fold", jan, fold)):
            theta = E.fit_on(core, spec, lam, mask=tr)
            row[title] = fit_skills(core, E.predict_on(core, spec, theta), te)
        cv = []
        for seed in range(3):
            half = set(np.random.default_rng(seed).choice(companies, len(companies) // 2, False))
            a = np.asarray([x in half for x in fitset["accounts"]])
            for tr in (a, ~a):
                theta = E.fit_on(core, spec, lam, mask=tr)
                cv.append(fit_skills(core, E.predict_on(core, spec, theta), ~tr))
        row["companies_2fold_x3"] = {
            k: float(np.mean([c[k] for c in cv])) for k in ("trajectory", "minimum")
        }
        out["variants"][variant] = row
    return out


# ---------------------------------------------------------------- парные разности


def paired(records, split, a, b, key, boot):
    """Медиана по компаниям разности skill(a) - skill(b) к constant, бутстрэп по компаниям."""
    const = {
        (r["account_id"], r["origin"]): r
        for r in records
        if r["split"] == split and r["method"] == "constant"
    }

    def skills(method):
        m, c = defaultdict(float), defaultdict(float)
        for r in records:
            if r["split"] == split and r["method"] == method:
                m[r["account_id"]] += r[key]
                c[r["account_id"]] += const[(r["account_id"], r["origin"])][key]
        return {x: 1 - m[x] / c[x] for x in m if c[x] > 0}

    sa, sb = skills(a), skills(b)
    diffs = [sa[x] - sb[x] for x in sorted(sa) if x in sb]
    return bootstrap_median(diffs, boot["resamples"], boot["seed"])


def fmt(s):
    if not s or s.get("median") is None:
        return "—"
    return f"{s['median']:+.3f} [{s['ci_low']:+.3f}; {s['ci_high']:+.3f}]"


def f3(x):
    return "—" if x is None else f"{x:.3f}"


def split_table(summary, split):
    lines = [
        "| Метод | Окон | Компаний | Skill траект. | Skill миним. | Skill дня 30 | Норм. MAE | "
        "Найдено | Recall@7d | Ложных / счёт-мес | Дата ±3 дн. |",
        "|---|---:|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for m in ORDER:
        e = summary[split].get(m)
        if not e:
            continue
        r = e["risk"]
        lines.append(
            f"| {TITLES[m]} | {e['windows']} | {e['companies']} | {fmt(e['skill_trajectory'])} | "
            f"{fmt(e['skill_minimum'])} | {fmt(e['skill_day30'])} | {f3(e['normalized_mae'])} | "
            f"{r['events_detected']}/{r['events_scorable']} | {f3(r['recall_7d'])} | "
            f"{f3(r['false_alarms_per_account_month'])} | {f3(r['date_within_tolerance'])} |"
        )
    return lines


def decide(summary, pairs, config):
    """Предрегистрированное правило X10 (experiments/registry.json)."""
    out = {}
    for cand in ("ens_traj", "ens_bal"):
        d1 = d1_decision(summary["selection"], cand, DEFAULT, config)
        sel = pairs[f"selection|{cand}-{DEFAULT}"]["skill_trajectory"]
        ret = pairs[f"retrospective|{cand}-{DEFAULT}"]["skill_trajectory"]
        checks = {
            **d1["checks"],
            "paired_trajectory_vs_default_positive": (sel["median"] or 0) > 0,
            "retro_not_worse_beyond_ci": (ret["ci_high"] or -1) >= 0,
        }
        out[cand] = {"checks": checks, "pass": all(checks.values())}
    passed = [c for c in out if out[c]["pass"]]
    traj = {c: summary["selection"][c]["skill_trajectory"]["median"] for c in out}
    mins = {c: summary["selection"][c]["skill_minimum"]["median"] for c in out}
    winner = None
    if passed:
        winner = max(passed, key=lambda c: traj[c])
        others = [c for c in passed if c != winner and traj[winner] - traj[c] < 0.01]
        if others:
            winner = max([winner, *others], key=lambda c: mins[c])
    return {"candidates": out, "winner": winner, "default_after": winner or "hgb"}


def main():
    t0 = time.time()
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    p1 = json.loads((ROOT / "experiments/p1.json").read_text())
    os.chdir(ROOT)
    svc = service()
    data = account_data(svc)
    fitset = E.build_fitset(
        data, svc.registry, svc.artifacts, config, p1, dataset_digest(data), progress=say
    )
    cv = fitset_cv(fitset)
    (OUT / "fitset_cv.json").write_text(
        json.dumps({"fitset": fitset["meta"], **cv}, ensure_ascii=False, indent=2)
    )
    models = {}
    for variant in E.RECIPE["variants"]:
        models[variant] = E.fit_variant(fitset, variant)
        (OUT / f"weights_{variant}.json").write_text(
            json.dumps(models[variant], ensure_ascii=False, indent=2)
        )
        say(f"{variant}: {models[variant]['version']} {models[variant]['theta']}")
    say(f"набор подбора и веса: {time.time() - t0:.0f} с")

    origins = expand_origins(config)
    origins = {s: origins[s] for s in ("selection", "retrospective")}
    methods = {m: METHODS[m] for m in ("constant", "weekday", "shape_only", "hgb_v2", "hgb_cal")}
    methods["ens_traj"] = E.protocol_method(models["traj"])
    methods["ens_bal"] = E.protocol_method(models["bal"])
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
    pairs = {}
    for split in ("selection", "retrospective"):
        for a, b in (
            ("ens_traj", DEFAULT),
            ("ens_bal", DEFAULT),
            ("ens_traj", "shape_only"),
            ("ens_bal", "shape_only"),
            ("ens_traj", "hgb_cal"),
            ("ens_bal", "hgb_cal"),
            ("hgb_cal", DEFAULT),
            ("ens_bal", "ens_traj"),
        ):
            pairs[f"{split}|{a}-{b}"] = {
                "skill_trajectory": paired(records, split, a, b, "mae_traj_minor", boot),
                "skill_minimum": paired(records, split, a, b, "mae_min_minor", boot),
                "skill_day30": paired(records, split, a, b, "mae_d30_minor", boot),
            }
    decision = decide(summary, pairs, config)
    payload = {
        "recipe": E.RECIPE,
        "models": {v: {"version": m["version"], "theta": m["theta"]} for v, m in models.items()},
        "fitset": fitset["meta"],
        "fitset_cv": cv,
        "summary": summary,
        "pairs": pairs,
        "decision": decision,
        "dropped_windows": len(result["dropped_windows"]),
    }
    (OUT / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    lines = ["# X10: ансамбль с весами по горизонту — таблицы", ""]
    for split in ("selection", "retrospective"):
        lines += [f"## {split}", "", *split_table(summary, split), ""]
    lines += [
        "## Парные разности (медиана по компаниям, 95% бутстрэп)",
        "",
        "| Срез | Сравнение | Траектория | Минимум | День 30 |",
        "|---|---|---|---|---|",
    ]
    for key, v in pairs.items():
        split, title = key.split("|")
        lines.append(
            f"| {split} | {title} | {fmt(v['skill_trajectory'])} | {fmt(v['skill_minimum'])} | "
            f"{fmt(v['skill_day30'])} |"
        )
    lines += [
        "",
        "## Решение",
        "",
        "```",
        json.dumps(decision, ensure_ascii=False, indent=2),
        "```",
    ]
    lines += [
        "",
        f"Окон отброшено: {len(result['dropped_windows'])}. Время: {time.time() - t0:.0f} с.",
    ]
    (OUT / "tables.md").write_text("\n".join(lines) + "\n")
    say("\n".join(lines))


if __name__ == "__main__":
    main()
