"""Собирает report.md из JSON, записанных evaluate_documents.py (числа не вводятся вручную)."""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent


def pct(x, d=1):
    return "н/д" if x is None else f"{100 * x:.{d}f}%"


def num(x, d=1):
    return "н/д" if x is None else f"{x:.{d}f}"


def rub(minor):
    return f"{minor / 100 / 1e6:,.1f} млн руб.".replace(",", " ")


def main():
    cov = json.loads((OUT / "coverage.json").read_text())
    cal = json.loads((OUT / "calibration.json").read_text())
    ev = json.loads((OUT / "evaluation.json").read_text())
    dist = json.loads((OUT / "delay_distribution.json").read_text())
    L = []
    w = L.append
    w("# X3. Реестр документов (D6): покрытие, модель задержки, оценка\n")
    w("Сгенерировано `make_report.py` из `coverage.json`, `calibration.json`, `evaluation.json`,")
    w(
        "`delay_distribution.json` (их пишет `evaluate_documents.py`). Все числа взяты из этих файлов.\n"
    )

    w("## 1. Покрытие парсером (весь архив, 150 компаний)\n")
    w(
        "Класс строки: dated_document = документ (счёт/УПД/акт/накладная) с датой; undated_document ="
    )
    w("ссылка без даты (в реестр не входит: нет known_at); contract_prepay_settle = предоплата или")
    w(
        "окончательный расчёт с номером договора; contract_only = договор без документа (кредит, лизинг,"
    )
    w("аренда, оферта и т.п.); return = возврат платежа; no_reference = ссылок нет.\n")
    w("| Направление | Класс | Строк | Доля строк | Доля суммы |")
    w("|---|---|---:|---:|---:|")
    for d, label in (("in", "поступления"), ("out", "списания")):
        for k, v in cov[d]["classes"].items():
            w(
                f"| {label} (всего {cov[d]['rows']}) | {k} | {v['rows']} | {pct(v['row_share'])} | {pct(v['amount_share'])} |"
            )
    w("")
    w("Состав классов с документами по категориям X1 (доля суммы направления):\n")
    for d in ("in", "out"):
        parts = [f"{k} {pct(v['amount_share'], 2)}" for k, v in cov[d]["by_category"].items()]
        w(f"- {d}: " + "; ".join(parts))
    u = cov["universe"]
    w("")
    w(
        f"Реестр после фильтра категорий (DOC_CATEGORIES): документов {u['documents']} "
        f"(поступления {u['documents_in']}, списания {u['documents_out']}), договорных циклов "
        f"предоплата->расчёт {u['contract_cycles']}, из них закрытых (есть расчёт) "
        f"{u['contract_cycles_closed']}; компаний с документами {u['companies_with_documents']} из "
        f"{u['companies_total']}. Регулярные счета (аренда, коммунальные, связь, ПО, охрана, "
        f"бухгалтерия) в реестр не входят: их ведёт X2.\n"
    )
    pd = dist["partial_documents"]
    w(
        f"Документов с несколькими оплатами (частичные, объединены): {pd['with_several_payments']} "
        f"из {pd['documents']} ({pct(pd['share'])}).\n"
    )

    w("## 2. Калибровка модели задержки (только калибровочные срезы)\n")
    w(
        f"Срезы: {', '.join(cal['origins'])}. Критерий: {cal['criterion']}. Подбор: охранный возраст "
        "документа в выборке, условность на «не оплачен к as_of», минимум наблюдений клиента.\n"
    )
    w("| Конфигурация | Событий | Средняя абс. ошибка, дн. | Медиана, дн. |")
    w("|---|---:|---:|---:|")
    for c in cal["grid"]:
        mark = " **(выбрана)**" if c["config"] == cal["chosen"] else ""
        w(
            f"| {c['config']}{mark} | {c['n']} | {num(c['abs_err_mean'], 2)} | {num(c['abs_err_median'], 1)} |"
        )
    w("")

    w("## 3. Оценка на срезах выбора и ретро\n")
    w(
        f"Конфигурация: `{ev['config']}`. Окно — 30 дней после as_of; окна те же, что в X0 (история 90 "
        "дней и 30 дней вперёд валидны). События реестра считаются отдельно на каждый as_of: один "
        "документ, открытый на нескольких недельных срезах, учитывается на каждом.\n"
    )
    for split, title in (
        ("selection", "Выбор (2026-03-01 … 05-31)"),
        ("retrospective", "Ретро (2026-07-01 … 08-31)"),
    ):
        s = ev["main"][split]
        w(f"### {title}\n")
        w(
            f"Окон: {s['windows_eligible']} годных из {s['windows_total']}, компаний {s['companies_eligible']}, "
            f"срезов {s['origins']}.\n"
        )
        w("| Показатель | Поступления | Списания |")
        w("|---|---:|---:|")
        i, o = s["in"], s["out"]
        rows = [
            ("Сумма потока за 30 дн. (сумма по окнам)", rub(i["flow_minor"]), rub(o["flow_minor"])),
            (
                "Потолок: доля, которую реестр может покрыть (оплаты открытых документов в окне)",
                pct(i["ceiling_share"]),
                pct(o["ceiling_share"]),
            ),
            (
                "Потолок, медиана по окнам",
                pct(i["ceiling_share_median_window"]),
                pct(o["ceiling_share_median_window"]),
            ),
            (
                "Сумма событий реестра с ожидаемой датой в окне / поток",
                pct(i["predicted_in_window_share"]),
                pct(o["predicted_in_window_share"]),
            ),
            (
                "Покрытие попаданием: ожидаемая и фактическая оплата в окне / поток",
                pct(i["hit_share"]),
                pct(o["hit_share"]),
            ),
            (
                "то же, медиана по окнам",
                pct(i["hit_share_median_window"]),
                pct(o["hit_share_median_window"]),
            ),
            ("то же, от потока без финансирования (D2)", pct(i["hit_share_of_ex_financing"]), "-"),
            (
                "Точность по сумме (попадание / предсказано)",
                pct(i["precision_amount"]),
                pct(o["precision_amount"]),
            ),
            (
                "Полнота к потолку (попадание / потолок)",
                pct(i["recall_vs_ceiling"]),
                pct(o["recall_vs_ceiling"]),
            ),
            ("Событий (пар документ x срез)", i["events"], o["events"]),
            (
                "Уникальных документов/договоров",
                i["events_unique_documents"],
                o["events_unique_documents"],
            ),
        ]
        for r in rows:
            w(f"| {r[0]} | {r[1]} | {r[2]} |")
        w("")
        w(
            "Ошибка ожидаемой даты (ожидаемая − фактическая, дни; фактическая = средневзвешенная дата "
            "оплат документа после as_of). Знак: плюс = ожидали позже.\n"
        )
        w(
            "| Направление / подмножество | n | медиана ошибки (смещение) | MAE | медиана |ошибки| | P90 |ошибки| | в ±3 дн. (шт.) | в ±7 дн. (шт.) | в ±7 дн. (по сумме) |"
        )
        w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for d in ("in", "out"):
            for key, lab in (
                ("date_error_all", "все события"),
                ("date_error_actual_in_window", "фактическая оплата в окне"),
                ("date_error_expected_in_window", "ожидание в окне"),
            ):
                st = s[d][key]
                if st["n"] == 0:
                    continue
                w(
                    f"| {d} / {lab} | {st['n']} | {num(st['bias_days_median'])} | {num(st['abs_err_mean'])} | {num(st['abs_err_median'])} | {num(st['abs_err_p90'])} | {pct(st['within_3d_count'])} | {pct(st['within_7d_count'])} | {pct(st['within_7d_amount'])} |"
                )
        w("")
        w("По уровню оценки задержки (все события):\n")
        w("| Направление | Уровень | n | MAE, дн. | в ±7 дн. |")
        w("|---|---|---:|---:|---:|")
        for d in ("in", "out"):
            for lv, st in s[d]["by_level"].items():
                if st["n"]:
                    w(
                        f"| {d} | {lv} | {st['n']} | {num(st['abs_err_mean'])} | {pct(st['within_7d_count'])} |"
                    )
        w("")
        w(
            f"Доля фактических дат внутри интервала [q10, q90] модели: поступления {pct(s['in']['share_actual_inside_q10_q90'])}, "
            f"списания {pct(s['out']['share_actual_inside_q10_q90'])} (номинально 80%).\n"
        )
        ca = s["contract_amount_error"]
        w(
            f"Сумма окончательного расчёта (отношение из истории), n={ca['n']}: медиана |отн. ошибки| "
            f"{pct(ca['median_abs_rel'])}, среднее {pct(ca['mean_abs_rel'])}, P90 {pct(ca['p90_abs_rel'])}, "
            f"смещение (медиана знаковой) {pct(ca['median_signed_rel'])}, в пределах 10%: {pct(ca['within_10pct'])}. "
            "Для счетов, УПД, актов сумма события равна сумме будущих оплат по D6, поэтому её "
            "ошибка нулевая по построению и в таблицах не оценивается.\n"
        )
        w("Поступления по отраслям (потолок и попадание, доля суммы потока отрасли):\n")
        w("| Отрасль | Поток | Потолок | Попадание |")
        w("|---|---:|---:|---:|")
        for k, v in s["by_industry_in"].items():
            w(
                f"| {k} | {rub(v['flow_minor'])} | {pct(v['ceiling_share'])} | {pct(v['hit_share'])} |"
            )
        w("")

    w("## 4. Иерархия против одной глобальной задержки (справочно)\n")
    w("| Срез | Модель | n событий (вх.) | MAE, дн. (вх.) | Попадание вх. | MAE, дн. (исх.) |")
    w("|---|---|---:|---:|---:|---:|")
    for split in ("selection", "retrospective"):
        for name, block in (
            ("клиент->отрасль->все", ev["main"]),
            ("только все", ev["global_only"]),
        ):
            s = block[split]
            w(
                f"| {split} | {name} | {s['in']['date_error_all']['n']} | {num(s['in']['date_error_all']['abs_err_mean'], 2)} | {pct(s['in']['hit_share'])} | {num(s['out']['date_error_all']['abs_err_mean'], 2)} |"
            )
    w("")

    w(
        "Попадание по сумме у «только все» не хуже, чем у иерархии, хотя MAE даты у неё выше; причину"
    )
    w("не исследовали, поэтому вывод о пользе иерархии сделан по ошибке даты, а не по попаданию.\n")
    w("## 5. Распределение задержек (полный архив, описательно)\n")
    w("Задержка = (средневзвешенная дата оплат документа) − дата документа, дни.\n")
    w("| Показатель | n | среднее | P05 | P25 | P50 | P75 | P95 |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|")
    for lab, st in (
        ("Поступления: счета/УПД/акты", dist["invoice"]["in"]),
        ("Списания: счета/УПД/акты", dist["invoice"]["out"]),
        ("Предоплата -> расчёт, лаг", dist["contract_lag_days"]),
        ("Расчёт / предоплата, отношение", dist["contract_ratio"]),
    ):
        w(
            f"| {lab} | {st['n']} | {num(st['mean'], 2)} | {num(st['p05'], 2)} | {num(st['p25'], 2)} | {num(st['p50'], 2)} | {num(st['p75'], 2)} | {num(st['p95'], 2)} |"
        )
    w("")
    w("По отраслям, поступления (медиана / P90 задержки, n):\n")
    for k, st in dist["invoice"]["in"]["by_industry"].items():
        w(f"- {k}: {num(st['p50'])} / {num(st['p90'])}, n={st['n']}")
    w("")
    head = (OUT / "report_head.md").read_text()
    body = "\n".join(L).split("\n", 4)[4]  # без повторного заголовка
    (OUT / "report.md").write_text(head + body)


if __name__ == "__main__":
    main()
