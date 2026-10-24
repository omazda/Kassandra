"""Собирает report.md, reliability.csv, alarms.csv и slide.json из results.json (X6)."""

import csv
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
R = json.loads((OUT / "results.json").read_text())
SPLIT = {"selection": "выбор", "retrospective": "ретро", "calibration": "калибровка"}


def f(x, d=3):
    return "—" if x is None else f"{x:.{d}f}"


def pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def tab(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    lines = ["# X6: интервалы и вероятность разрыва (D7)", ""]
    v0 = R["variants"]["docs"]
    lines += [
        "Метод: structural S3 (default по X4; остаток профиль по X5). Вариант docs - с ретро-реестром "
        "документов (D6), nodocs - реестр недоступен (как в продукте на конце архива).",
        "",
        f"Калибровка: {v0['windows']['calibration']} окон (15 срезов с шагом 2 дня, 2026-01-01..01-29, "
        f"цели до 28 февраля), из них без текущего дефицита {v0['calibration_open_windows']}, "
        f"окон с событием D2 {v0['calibration_events_windows']} "
        f"(базовая частота {pct(v0['calibration_base_rate'])}). "
        f"Выбор: {v0['windows']['selection']} окон (14 недельных срезов 2026-03-01..05-31), "
        f"ретро: {v0['windows']['retrospective']} окон (9 срезов 07-01..08-26). "
        f"Окон отброшено: {len(R['dropped_windows'])}. Отдельных событий D2 с предупреждением >=7 дней: "
        "69 на выборе, 49 в ретро (как в X0).",
        "",
        "Плотные калибровочные срезы повышают число окон, но не число независимых событий: окна "
        "одной компании перекрываются. Ложные тревоги на калибровке считаются как для недельных окон.",
        "",
        "## 1. Конформные интервалы P10..P90 накопленной ошибки",
        "",
        "Split-conformal по горизонту: квантили знаковой ошибки (факт D3 минус прогноз), нормированной "
        "оборотом за 90 дней; уровни 10% и 90% с поправкой на конечную выборку; банк = калибровочные окна. "
        "Номинальное покрытие 80%.",
        "",
    ]
    rows = []
    for key in ("docs", "nodocs"):
        for split in ("selection", "retrospective"):
            c = R["variants"][key]["coverage"][split]
            rows.append(
                [
                    key,
                    SPLIT[split],
                    f"{c['windows']} / {c['companies']}",
                    pct(c["coverage_all_days"]),
                    pct(c["coverage_by_company_median"]),
                    pct(c["coverage_day7"]),
                    pct(c["coverage_day14"]),
                    pct(c["coverage_day30"]),
                    pct(c["below_p10_share"]),
                    pct(c["above_p90_share"]),
                ]
            )
    lines += [
        tab(
            [
                "вариант",
                "срез",
                "окон / компаний",
                "покрытие, все дни",
                "медиана по компаниям",
                "день 7",
                "день 14",
                "день 30",
                "ниже P10",
                "выше P90",
            ],
            rows,
        ),
        "",
    ]
    lines += [
        "## 2. Вероятность P(событие D2 за 30 дней): три способа",
        "",
        "1) порог на центральной траектории (тревога, если центральный остаток < 0); "
        "2) симуляция без калибровки (порог тревоги подбирается на калибровке при бюджете "
        f"<= {R['budget']} ложной тревоги на счёт-месяц); "
        "3) симуляция с калибровкой (изотония или Platt по кросс-валидации по счетам; порог тоже "
        "подбирается на калибровке, по out-of-fold вероятностям). "
        "Симуляция: целые траектории ошибок из банка (leave-one-company-out на калибровке); вариант "
        "bank - только банк, events - банк + разброс дат и сумм событий по источнику. "
        "Brier skill - к постоянному прогнозу базовой частоты калибровки.",
        "",
    ]
    for key in ("docs", "nodocs"):
        v = R["variants"][key]
        lines += [f"### Вариант {key} (выбрана симуляция: {v['chosen_simulation']})", ""]
        lines += [
            "Brier на калибровке (кросс-валидация по счетам): "
            + "; ".join(
                f"{n}: " + ", ".join(f"{k} {x:.4f}" for k, x in d.items())
                for n, d in v["cv_brier"].items()
            ),
            "",
        ]
        rows = []
        names = [
            ("central_threshold", "1. порог на центральной"),
            ("sim_raw_bank", "2. симуляция, bank"),
            ("sim_raw_events", "2. симуляция, bank+события"),
            ("sim_calibrated_bank", "3. + калибровка, bank"),
            ("sim_calibrated_events", "3. + калибровка, bank+события"),
        ]
        for split in ("selection", "retrospective"):
            for name, title in names:
                m = v["methods"][name][split]
                rk, pr = m["risk"], m["probability"]
                rows.append(
                    [
                        SPLIT[split],
                        title,
                        f(pr["brier"], 4) if pr else "—",
                        f(pr["vs_calibration_rate"]["brier_skill"]) if pr else "—",
                        f"{f(rk['recall_7d'])} ({rk['events_detected']}/{rk['events_scorable']})",
                        f(rk["false_alarms_per_account_month"]),
                        f"{pct(rk['date_within_tolerance'])} (n={rk['date_error_n']})",
                        f"{rk['deficit_mae_rub']:,.0f}".replace(",", " ")
                        if rk["deficit_mae_rub"] is not None
                        else "—",
                    ]
                )
        lines += [
            tab(
                [
                    "срез",
                    "способ",
                    "Brier",
                    "Brier skill",
                    "recall@7d (найдено/событий)",
                    "ложных тревог / счёт-месяц",
                    "дата ±3 дня",
                    "MAE суммы дефицита, руб",
                ],
                rows,
            ),
            "",
        ]
    lines += ["## 3. Recall@7d при разных бюджетах ложных тревог", ""]
    lines += [
        "Порог подбирается на калибровке под бюджет, затем применяется к выбору и ретро. "
        "Для сравнения: центральная траектория не имеет настраиваемого порога.",
        "",
    ]
    for key in ("docs", "nodocs"):
        v = R["variants"][key]
        best = v["chosen_simulation"]
        rows = []
        for split in ("selection", "retrospective"):
            c = v["methods"]["central_threshold"][split]["risk"]
            rows.append(
                [
                    SPLIT[split],
                    "центральная траектория",
                    "—",
                    f"{f(c['recall_7d'])} ({c['events_detected']}/{c['events_scorable']})",
                    f(c["false_alarms_per_account_month"]),
                ]
            )
            for label in ("raw", "calibrated"):
                m = v["methods"][f"sim_{label}_{best}"]
                for b, rk in m[split]["risk_by_budget"].items():
                    rows.append(
                        [
                            SPLIT[split],
                            f"симуляция {'с калибровкой' if label == 'calibrated' else 'без'} ({best})",
                            f"{b} (порог {m['thresholds_by_budget'][b]:.3f})",
                            f"{f(rk['recall_7d'])} ({rk['events_detected']}/{rk['events_scorable']})",
                            f(rk["false_alarms_per_account_month"]),
                        ]
                    )
        lines += [
            f"### {key}",
            "",
            tab(
                ["срез", "способ", "бюджет (порог)", "recall@7d", "ложных тревог / счёт-месяц"],
                rows,
            ),
            "",
        ]
    lines += ["## 4. Надёжность (reliability) калиброванной вероятности", ""]
    reliability_rows = []
    for key in ("docs", "nodocs"):
        v = R["variants"][key]
        m = v["methods"][f"sim_calibrated_{v['chosen_simulation']}"]
        for split in ("selection", "retrospective"):
            for b in m[split]["probability"]["reliability"]:
                reliability_rows.append(
                    [
                        key,
                        v["chosen_simulation"],
                        split,
                        b["bin"],
                        b["n"],
                        b["mean_p"],
                        b["observed"],
                    ]
                )
    with open(OUT / "reliability.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["variant", "simulation", "split", "bin", "n", "mean_p", "observed"])
        w.writerows(reliability_rows)
    for key in ("docs", "nodocs"):
        rows = [
            [r[2] and SPLIT[r[2]], r[3], r[4], f(r[5]), f(r[6])]
            for r in reliability_rows
            if r[0] == key and r[4]
        ]
        lines += [
            f"### {key}",
            "",
            tab(["срез", "корзина p", "окон", "средняя p", "наблюдаемая частота"], rows),
            "",
        ]
    lines += ["## 5. Статус калибровки в продукте", ""]
    rows = []
    for key in ("docs", "nodocs"):
        s = R["variants"][key]["reliability_status"]
        rows.append(
            [
                key,
                s["bins_checked"],
                f(s["max_gap"]),
                "calibrated_on_jan_feb" if s["ok"] else "experimental_uncalibrated",
            ]
        )
    lines += [
        "Правило: в каждой корзине выбора с >= 30 окон |наблюдаемая частота - средняя p| <= 0,10 "
        "и таких корзин не меньше двух; иначе experimental_uncalibrated.",
        "",
        tab(["вариант", "корзин проверено", "макс. отклонение", "статус"], rows),
        "",
        "## 6. Параметры разброса событий",
        "",
        "Полуокно даты (дни) подбирается по окнам P10..P90 механизмов и реестра на калибровке; "
        "сигма суммы задана заранее по отчётам X2/X3/X7.",
        "",
        tab(
            ["источник", "полуокно, дней", "сигма суммы", "событий для подбора"],
            [
                [k, v["half_days"], v["sigma"], v.get("fitted_n", "—")]
                for k, v in R["source_params"].items()
            ],
        ),
        "",
        f"Время полного прогона: {R['seconds']:.0f} с.",
        "",
    ]
    commentary = OUT / "commentary.md"
    if commentary.exists():
        lines += [commentary.read_text(), ""]
    (OUT / "report.md").write_text("\n".join(lines))

    slide = {"budget": R["budget"], "variants": {}}
    for key in ("docs", "nodocs"):
        v = R["variants"][key]
        m = v["methods"][f"sim_calibrated_{v['chosen_simulation']}"]
        slide["variants"][key] = {
            "status": "calibrated_on_jan_feb"
            if v["reliability_status"]["ok"]
            else "experimental_uncalibrated",
            "coverage": v["coverage"],
            "selection": {
                "brier": m["selection"]["probability"]["brier"],
                "brier_skill": m["selection"]["probability"]["vs_calibration_rate"]["brier_skill"],
                "risk": m["selection"]["risk"],
                "reliability": m["selection"]["probability"]["reliability"],
            },
            "retrospective": {
                "brier": m["retrospective"]["probability"]["brier"],
                "brier_skill": m["retrospective"]["probability"]["vs_calibration_rate"][
                    "brier_skill"
                ],
                "risk": m["retrospective"]["risk"],
                "reliability": m["retrospective"]["probability"]["reliability"],
            },
            "central_threshold": {
                s: v["methods"]["central_threshold"][s]["risk"]
                for s in ("selection", "retrospective")
            },
        }
    (OUT / "slide.json").write_text(json.dumps(slide, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
