"""X5: GBM остатка (E04: HGB / LightGBM / CatBoost, E05: прямой накопленный остаток 7/14/30).

Запуск из корня:  uv run python experiments/results/v3/x5/evaluate_x5.py [--skip-train]

Порядок (всё, что подбирается, - только на КАЛИБРОВОЧНЫХ срезах 2026-01; срезы выбора и ретро
используются один раз, для итоговой таблицы и решения по D1):
1. Датасет окон остатка (origins с целями <= trained_to) и обучение 6 конфигураций E04.
2. Калибровка: лучшая конфигурация E04 по макро нормированной MAE траектории.
3. E05: та же конфигурация, цель - накопленный остаток на 7/14/30 с интерполяцией.
4. Калибровка веса смеси w (GBM / профиль дней недели) из {0.5, 1.0} для E04 и E05.
5. Итоговая таблица на всех срезах: constant, weekday, structural (профиль), все 6 конфигураций,
   выбранные E04 и E05; правило D1 против structural с профилем; запись decision.json.
Правило замены default, заданное заранее: кандидат проходит D1 И парная разность skill
траектории (кандидат - профиль) на выборе имеет медиану > 0.
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")
os.environ.setdefault("OMP_NUM_THREADS", "1")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from cashflow_ml import structural as S  # noqa: E402
from cashflow_ml.models import GBM_CONFIGS  # noqa: E402
from cashflow_ml.protocol_v3 import METHODS, d1_decision, expand_origins, run_protocol  # noqa: E402
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).resolve().parent
PROFILE = "structural_profile"
BLENDS = (0.5, 1.0)

_spec = importlib.util.spec_from_file_location(
    "evaluate_x4", ROOT / "experiments/results/v3/x4/evaluate_x4.py"
)
X4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X4)


def say(text):
    print(text, flush=True)


def protocol(data, svc, config, methods, origins, out_dir=None):
    return run_protocol(
        data, methods, origins, registry=svc.registry, config=config,
        out_dir=out_dir, n_jobs=2, progress=say,
    )  # fmt: skip


def gbm(kind, blend=1.0):
    return S.protocol_method(tax=True, docs=True, residual=kind, blend=blend)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-train", action="store_true")
    args = parser.parse_args()
    t0 = time.time()
    os.chdir(ROOT)
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    svc = service()
    data = account_data(svc)
    info = S.load_company_info(ROOT / "dataset/companies.csv")
    S.configure(S.build_universe(data, {k: v["industry"] for k, v in info.items()}), info)
    origins = expand_origins(config)
    trained_to = date.fromisoformat(config["trained_to"])
    root = svc.registry.root
    timings, metas = {}, {}

    def ensure(kind, formulation, dataset):
        name = kind if formulation == "daily" else f"{kind}:cum"
        try:
            meta = svc.registry.select_residual(trained_to, name)
            say(f"{name}: уже есть {meta['model_version']}")
        except ValueError:
            t = time.time()
            meta = S.train_residual_gbm(dataset, kind, root, formulation)
            timings[name] = round(time.time() - t, 1)
            say(f"{name}: обучена {meta['model_version']}, {timings[name]} с")
        metas[name] = meta

    dataset = None
    if not args.skip_train:
        t = time.time()
        dataset = S.build_residual_dataset(data, trained_to)
        say(
            f"датасет: окон {dataset['x'].shape[0]}, признаков {dataset['x'].shape[2]}, "
            f"{time.time() - t:.0f} с"
        )
        timings["dataset"] = round(time.time() - t, 1)
        for kind in GBM_CONFIGS:
            ensure(kind, "daily", dataset)
    else:
        for kind in GBM_CONFIGS:
            metas[kind] = svc.registry.select_residual(trained_to, kind)

    # --- калибровка: выбор конфигурации E04
    cal = {"calibration": origins["calibration"]}
    methods = {"constant": METHODS["constant"], PROFILE: S.protocol_method(tax=True, docs=True)}
    methods |= {f"gbm_{k}": gbm(k) for k in GBM_CONFIGS}
    res = protocol(data, svc, config, methods, cal)
    table = {
        n: res["summary"]["calibration"][n]["normalized_mae"] for n in methods if n != "constant"
    }
    best = min(GBM_CONFIGS, key=lambda k: (round(table[f"gbm_{k}"], 6), k))
    say(f"E04 калибровка: {table}; лучшая {best}")

    # --- E05: накопленная цель на 7/14/30
    if dataset is not None:
        ensure(best, "cumulative", dataset)
    else:
        metas[f"{best}:cum"] = svc.registry.select_residual(trained_to, f"{best}:cum")
    methods2 = {"constant": METHODS["constant"]}
    for w in BLENDS:
        methods2[f"e04_w{w:g}"] = gbm(best, w)
        methods2[f"e05_w{w:g}"] = gbm(f"{best}:cum", w)
    res2 = protocol(data, svc, config, methods2, cal)
    table2 = {
        n: res2["summary"]["calibration"][n]["normalized_mae"] for n in methods2 if n != "constant"
    }
    say(f"калибровка смесей: {table2}")
    best_e04 = min(BLENDS, key=lambda w: (round(table2[f"e04_w{w:g}"], 6), -w))
    best_e05 = min(BLENDS, key=lambda w: (round(table2[f"e05_w{w:g}"], 6), -w))
    calibration = {
        "normalized_mae_profile": table[PROFILE],
        "normalized_mae_by_config": {k: table[f"gbm_{k}"] for k in GBM_CONFIGS},
        "best_config": best,
        "blend_table": table2,
        "blend_e04": best_e04,
        "blend_e05": best_e05,
        "windows": res["summary"]["calibration"][PROFILE]["windows"],
        "companies": res["summary"]["calibration"][PROFILE]["companies"],
    }

    # --- итоговый прогон
    methods3 = {
        "constant": METHODS["constant"],
        "weekday": METHODS["weekday"],
        PROFILE: S.protocol_method(tax=True, docs=True),
    }
    methods3 |= {f"gbm_{k}": gbm(k) for k in GBM_CONFIGS}
    methods3["e04_best"] = gbm(best, best_e04)
    methods3["e05_best"] = gbm(f"{best}:cum", best_e05)
    result = protocol(data, svc, config, methods3, origins, out_dir=OUT / "protocol")
    summary, records = result["summary"], result["records"]
    boot = config["bootstrap"]
    decisions, gains = {}, {}
    for cand in [m for m in methods3 if m not in ("constant", "weekday", PROFILE)]:
        d = d1_decision(summary["selection"], cand, PROFILE, config)
        traj = X4.paired(records, "selection", cand, PROFILE, "mae_traj_minor", boot)
        mini = X4.paired(records, "selection", cand, PROFILE, "mae_min_minor", boot)
        d["paired_trajectory"], d["paired_minimum"] = traj, mini
        d["better_than_profile"] = (traj["median"] or 0) > 0
        d["becomes_default"] = bool(d["pass"] and d["better_than_profile"])
        decisions[cand] = d
    for split in ("selection", "retrospective"):
        for cand in ("e04_best", "e05_best"):
            gains[f"{split}|{cand}-{PROFILE}"] = {
                "skill_trajectory": X4.paired(
                    records, split, cand, PROFILE, "mae_traj_minor", boot
                ),
                "skill_minimum": X4.paired(records, split, cand, PROFILE, "mae_min_minor", boot),
            }
    chosen = None
    for cand in ("e04_best", "e05_best"):
        if decisions[cand]["becomes_default"]:
            better = chosen is None or (
                summary["selection"][cand]["skill_trajectory"]["median"]
                > summary["selection"][chosen]["skill_trajectory"]["median"]
            )
            chosen = cand if better else chosen
    if chosen == "e04_best":
        decision = {"residual": best, "blend": best_e04}
    elif chosen == "e05_best":
        decision = {"residual": f"{best}:cum", "blend": best_e05}
    else:
        decision = {"residual": "profile", "blend": 1.0}
    decision |= {
        "chosen": chosen,
        "rule": "D1 against structural with weekday profile AND paired selection skill delta > 0",
        "calibration": calibration,
    }
    (OUT / "decision.json").write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    out = {
        "calibration": calibration,
        "summary": summary,
        "d1": decisions,
        "paired": gains,
        "models": {k: m["model_version"] for k, m in metas.items()},
        "timings_sec": timings,
        "dropped_windows": len(result["dropped_windows"]),
        "decision": decision,
        "elapsed_sec": round(time.time() - t0),
    }
    (OUT / "results.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    write_report(out, origins, decisions, gains, best)
    say(f"готово за {time.time() - t0:.0f} с; решение {decision['residual']} w={decision['blend']}")


TITLES = {
    "constant": "constant (ориентир)",
    "weekday": "weekday (ориентир)",
    PROFILE: "structural S3, остаток по дням недели (default X4)",
    "e04_best": "E04 лучшая GBM (дневная цель)",
    "e05_best": "E05 лучшая GBM (накопленная цель 7/14/30)",
}


def write_report(out, origins, decisions, gains, best):
    summary = out["summary"]
    titles = dict(TITLES) | {f"gbm_{k}": f"E04 {k} ({GBM_CONFIGS[k][0]})" for k in GBM_CONFIGS}
    X4.TITLES.update(titles)
    X4.METHOD_ORDER[:] = list(titles)
    cal = out["calibration"]
    lines = ["# X5: GBM остатка (E04, E05)", ""]
    lines += [
        f"Цель D3. Срезы: калибровка {len(origins['calibration'])}, выбор {len(origins['selection'])}, "
        f"ретро {len(origins['retrospective'])}. Обучение на origins с целями до "
        "2025-12-31. Skill к constant: медиана по компаниям, 95% бутстрэп по компаниям "
        "(1000, seed 42). Окон отброшено: " + str(out["dropped_windows"]) + ".",
        "",
        "## Калибровка (январь 2026, "
        f"{cal['windows']} окон, {cal['companies']} компаний): нормированная MAE траектории",
        "",
        "| Вариант | Норм. MAE |",
        "|---|---:|",
        f"| профиль по дням недели | {cal['normalized_mae_profile']:.4f} |",
    ]
    for k, v in cal["normalized_mae_by_config"].items():
        lines.append(f"| {k} ({GBM_CONFIGS[k][0]}) | {v:.4f} |{' лучшая' if k == best else ''}")
    lines += ["", f"Смесь GBM и профиля (конфигурация {best}): "]
    lines += [f"- {k}: {v:.4f}" for k, v in cal["blend_table"].items()]
    lines += ["", f"Выбрано: вес GBM E04 = {cal['blend_e04']}, E05 = {cal['blend_e05']}.", ""]
    for split, title in (
        ("selection", "Выбор (мар-май 2026)"),
        ("retrospective", "Ретро (июль-август)"),
    ):
        lines += [f"## {title}", ""] + X4.split_table(summary, split) + [""]
    lines += ["## Правило D1 против structural с профилем (срез выбора)", ""]
    lines += [
        "| Кандидат | skill миним. > 0 | skill траект. >= -2% | ложные <= 1 | recall не хуже | "
        "D1 | Δ skill траект. к профилю | Δ skill миним. к профилю | Default |",
        "|---|:-:|:-:|:-:|:-:|:-:|---|---|:-:|",
    ]
    yn = lambda b: "да" if b else "нет"  # noqa: E731
    for cand, d in decisions.items():
        c, t, m = d["checks"], d["paired_trajectory"], d["paired_minimum"]
        lines.append(
            f"| {titles[cand]} | {yn(c['skill_minimum_positive'])} | "
            f"{yn(c['skill_trajectory_ge_minus_2pct'])} | {yn(c['false_alarm_budget'])} | "
            f"{yn(c['recall_not_worse'])} | {yn(d['pass'])} | "
            f"{t['median']:+.3f} [{t['ci_low']:+.3f}; {t['ci_high']:+.3f}] | "
            f"{m['median']:+.3f} [{m['ci_low']:+.3f}; {m['ci_high']:+.3f}] | "
            f"{yn(d['becomes_default'])} |"
        )
    lines += ["", "## Парные разности на выборе и ретро (кандидат - профиль)", ""]
    lines += ["| Срез | Сравнение | Δ траект. | Δ миним. | Компаний |", "|---|---|---|---|---:|"]
    for key, v in gains.items():
        t, m = v["skill_trajectory"], v["skill_minimum"]
        lines.append(
            f"| {key.split('|')[0]} | {key.split('|')[1]} | {t['median']:+.3f} "
            f"[{t['ci_low']:+.3f}; {t['ci_high']:+.3f}] | {m['median']:+.3f} "
            f"[{m['ci_low']:+.3f}; {m['ci_high']:+.3f}] | {t['n_companies']} |"
        )
    lines += [
        "",
        f"Решение: `{json.dumps(out['decision']['residual'])}`, вес {out['decision']['blend']}.",
        "",
    ]
    lines += ["Время обучения, с: " + json.dumps(out["timings_sec"]), ""]
    commentary = OUT / "commentary.md"
    if commentary.exists():
        lines.append(commentary.read_text())
    (OUT / "report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
