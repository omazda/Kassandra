"""X2: evaluation of the mechanism detectors on weekly slices 2026-03-01 .. 2026-05-31.

Run from the project root:  uv run python experiments/results/v3/mechanisms/evaluate_mechanisms.py

Future operations (as_of+1 .. as_of+30) are used only here, to score the expected events.
Detectors see history <= as_of only. Outputs next to this file:
calendar_check.json, rules.json, results.json, per_slice.csv, report.md
(report.md = generated tables + commentary.md if present).
"""

import bisect
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from cashflow_ml import calendar_ru as cal  # noqa: E402
from cashflow_ml.mechanisms import (  # noqa: E402
    FIXED_CATEGORIES,
    detect_mechanisms,
    fit_nominal_days,
    group_key,
)
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).resolve().parent
FIRST_ASOF, LAST_ASOF = date(2026, 3, 1), date(2026, 5, 31)
HORIZON = 30
RULE_CUTOFF = date(2025, 12, 31)  # transfer rules are derived from history before the slices
NON_OPERATING = {
    "unknown",
    "owner_funding",
    "loan_in",
    "own_transfer",
    "owner_withdrawal",
    "refund",
}

WORKING = [
    d
    for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(365 * 4 + 400))
    if cal.is_working_day(d)
]


def wd_index(d: date) -> int:
    return bisect.bisect_right(WORKING, d)


def wd_dist(a: date, b: date) -> int:
    return wd_index(b) - wd_index(a)


def label_of(key: str) -> str:
    head = key.split("|")[0]
    if head == "salary":
        return "salary"
    if head == "tax":
        return "enp"
    if head == "debt_service":
        return "loan"
    if head in FIXED_CATEGORIES:
        return f"fixed:{head}"
    return head


MECH_LABELS = [
    "salary",
    "enp",
    "loan",
    "fixed:rent",
    "fixed:utilities",
    "fixed:telecom",
    "fixed:software",
    "fixed:accounting",
    "fixed:facility_services",
    "fixed:bank_fee",
    "acquiring",
    "sbp",
    "cash_in",
]


def greedy_match(events, actuals, tol):
    """One-to-one matching of event dates to actual dates within `tol` working days."""
    pairs = sorted(
        (abs(wd_dist(e, a)), abs((e - a).days), i, j)
        for i, e in enumerate(events)
        for j, a in enumerate(actuals)
        if abs(wd_dist(e, a)) <= tol
    )
    used_e, used_a, out = set(), set(), []
    for _dist, _, i, j in pairs:
        if i in used_e or j in used_a:
            continue
        used_e.add(i)
        used_a.add(j)
        out.append((i, j, wd_dist(events[i], actuals[j])))
    return out


def new_acc():
    return dict(
        windows=0,
        accounts_with_component=set(),
        n_pred=0,
        pred_matched=0,
        n_act=0,
        act_matched=0,
        n_act_detected=0,
        act_detected_matched=0,
        exact=0,
        signed_err=[],
        near_err=[],
        ape=[],
        sum_pred_matched=0,
        sum_abs_err=0,
        win_hit=0,
        win_total=0,
        sum_act_matched=0,
        sum_pred=0,
        sum_act_detected=0,
        sum_act_all=0,
    )


def evaluate(data, use_season=True):
    as_ofs = []
    d = FIRST_ASOF
    while d <= LAST_ASOF:
        as_ofs.append(d)
        d += timedelta(days=7)
    acc = defaultdict(new_acc)
    cover = defaultdict(lambda: defaultdict(int))
    per_slice = defaultdict(lambda: defaultdict(int))
    window_cover = []  # (account, as_of, direction, scope, owned, matched, total)
    mech_counts = Counter()
    for account, txs in data.items():
        dated = [(t["operation_date"], t) for t in txs]
        for as_of in as_ofs:
            lim, end = as_of.isoformat(), (as_of + timedelta(days=HORIZON)).isoformat()
            history = [t for ds, t in dated if ds <= lim]
            future = [t for ds, t in dated if lim < ds <= end]
            model = detect_mechanisms(history, as_of, use_season=use_season)
            events = model.events(HORIZON)
            for c in model.components:
                mech_counts[label_of(c.group_key)] += 1
            owned = model.groups
            # actual per (group, day)
            actual = defaultdict(lambda: defaultdict(int))
            for t in future:
                k = group_key(t)
                if k:
                    actual[k][date.fromisoformat(t["operation_date"])] += t["amount_minor"]
            pred = defaultdict(list)
            for e in events:
                pred[e.group_key].append(e)
            matched_amount = {"in": 0, "out": 0}
            pred_amount = {"in": 0, "out": 0}
            owned_amount = {"in": 0, "out": 0}
            for key in set(actual) | set(pred):
                lab = label_of(key)
                a = acc[lab]
                a["windows"] += 1
                if key in owned:
                    a["accounts_with_component"].add(account)
                evs = sorted(pred.get(key, []), key=lambda e: e.date)
                act_days = sorted(actual.get(key, {}))
                m1 = greedy_match([e.date for e in evs], act_days, 1)
                m10 = greedy_match([e.date for e in evs], act_days, 10)
                a["n_pred"] += len(evs)
                a["pred_matched"] += len(m1)
                a["n_act"] += len(act_days)
                a["act_matched"] += len(m1)
                if key in owned:
                    a["n_act_detected"] += len(act_days)
                    a["act_detected_matched"] += len(m1)
                for i, j, dist in m1:
                    a["exact"] += dist == 0
                    act_amt = actual[key][act_days[j]]
                    a["ape"].append(abs(evs[i].amount_minor - act_amt) / act_amt)
                    a["sum_pred_matched"] += evs[i].amount_minor
                    a["sum_abs_err"] += abs(evs[i].amount_minor - act_amt)
                    a["sum_act_matched"] += act_amt
                    direction = evs[i].direction
                    matched_amount[direction] += act_amt
                for e in evs:
                    lo, hi = e.window if e.window else (e.date, e.date)
                    a["win_total"] += 1
                    a["win_hit"] += any(
                        wd_dist(lo, x) >= -1 and wd_dist(x, hi) >= -1 for x in act_days
                    )
                for _, _, dist in m10:
                    a["signed_err"].append(dist)
                a["sum_pred"] += sum(e.amount_minor for e in evs)
                direction = "in" if key.split("|")[1] == "in" else "out"
                tot_act = sum(actual.get(key, {}).values())
                a["sum_act_all"] += tot_act
                if key in owned:
                    a["sum_act_detected"] += tot_act
                    owned_amount[direction] += tot_act
                pred_amount[direction] += sum(e.amount_minor for e in evs)
                ps = per_slice[(as_of.isoformat(), lab)]
                ps["n_pred"] += len(evs)
                ps["pred_matched"] += len(m1)
                ps["n_act"] += len(act_days)
                ps["act_matched"] += len(m1)
            for scope, keep in (
                ("all", lambda t: True),
                ("operating", lambda t: t["category"] not in NON_OPERATING),
            ):
                for direction in ("in", "out"):
                    total = sum(
                        t["amount_minor"] for t in future if t["direction"] == direction and keep(t)
                    )
                    owned_scope = sum(
                        t["amount_minor"]
                        for t in future
                        if t["direction"] == direction and keep(t) and group_key(t) in owned
                    )
                    cover[(scope, direction)]["total"] += total
                    cover[(scope, direction)]["owned"] += owned_scope
                    if scope == "all":
                        cover[(scope, direction)]["matched"] += matched_amount[direction]
                        cover[(scope, direction)]["pred"] += pred_amount[direction]
                        cover[(scope, direction)]["owned_actual"] += owned_amount[direction]
                    window_cover.append((scope, direction, owned_scope, total))
            # operating scope matched: matched actual amounts are all operating categories
            for direction in ("in", "out"):
                cover[("operating", direction)]["matched"] += matched_amount[direction]
                cover[("operating", direction)]["pred"] += pred_amount[direction]
                cover[("operating", direction)]["owned_actual"] += owned_amount[direction]
    return as_ofs, acc, cover, per_slice, window_cover, mech_counts


def summarize(acc, label):
    a = acc[label]
    ape = sorted(a["ape"])
    err = a["signed_err"]
    n_err = len(err)
    return dict(
        label=label,
        account_windows_with_activity=a["windows"],
        accounts_with_component=len(a["accounts_with_component"]),
        n_pred=a["n_pred"],
        n_actual_days=a["n_act"],
        precision_pm1wd=round(a["pred_matched"] / a["n_pred"], 4) if a["n_pred"] else None,
        recall_pm1wd=round(a["act_matched"] / a["n_act"], 4) if a["n_act"] else None,
        recall_detected_groups=round(a["act_detected_matched"] / a["n_act_detected"], 4)
        if a["n_act_detected"]
        else None,
        share_actual_days_in_detected_groups=round(a["n_act_detected"] / a["n_act"], 4)
        if a["n_act"]
        else None,
        exact_date_share_of_matched=round(a["exact"] / a["pred_matched"], 4)
        if a["pred_matched"]
        else None,
        date_err_wd_nearest10_n=n_err,
        date_err_wd_nearest10_mae=round(sum(abs(e) for e in err) / n_err, 3) if n_err else None,
        date_err_wd_nearest10_median_abs=sorted(abs(e) for e in err)[n_err // 2] if n_err else None,
        date_err_wd_nearest10_share_exact=round(sum(e == 0 for e in err) / n_err, 4)
        if n_err
        else None,
        date_err_wd_nearest10_share_within1=round(sum(abs(e) <= 1 for e in err) / n_err, 4)
        if n_err
        else None,
        mape_amount=round(sum(ape) / len(ape), 4) if ape else None,
        median_ape_amount=round(ape[len(ape) // 2], 4) if ape else None,
        share_ape_le_10=round(sum(x <= 0.10 for x in ape) / len(ape), 4) if ape else None,
        precision_in_date_window=round(a["win_hit"] / a["win_total"], 4)
        if a["win_total"]
        else None,
        wape_amount=round(a["sum_abs_err"] / a["sum_act_matched"], 4)
        if a["sum_act_matched"]
        else None,
        n_matched_pairs=len(ape),
        sum_pred_rub=round(a["sum_pred"] / 100, 2),
        sum_actual_detected_rub=round(a["sum_act_detected"] / 100, 2),
        sum_pred_over_actual_detected=round(a["sum_pred"] / a["sum_act_detected"], 4)
        if a["sum_act_detected"]
        else None,
        sum_actual_all_rub=round(a["sum_act_all"] / 100, 2),
    )


def calendar_check(data):
    ops = Counter()
    for txs in data.values():
        for t in txs:
            ops[date.fromisoformat(t["operation_date"])] += 1
    lo, hi = min(ops), max(ops)
    days = [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]
    nonworking_weekdays = [d for d in days if d.weekday() < 5 and not cal.is_working_day(d)]
    result = dict(
        archive_range=[lo.isoformat(), hi.isoformat()],
        operations_total=sum(ops.values()),
        table_nonworking_weekdays_in_range=len(nonworking_weekdays),
        ops_on_table_nonworking_days=sum(ops[d] for d in days if not cal.is_working_day(d)),
        nonworking_days_with_ops=[
            d.isoformat() for d in days if not cal.is_working_day(d) and ops[d]
        ],
        working_saturdays_in_range={
            d.isoformat(): ops[d] for d in sorted(cal.WORKING_SATURDAYS) if lo <= d <= hi
        },
        working_days_in_range=sum(cal.is_working_day(d) for d in days),
        working_days_without_ops_not_in_table=[
            d.isoformat() for d in days if cal.is_working_day(d) and not ops[d]
        ],
        weekend_days_with_ops_not_working_saturday=[
            d.isoformat()
            for d in days
            if d.weekday() >= 5 and ops[d] and d not in cal.WORKING_SATURDAYS
        ],
        provisional_years=sorted(cal.PROVISIONAL_YEARS),
        not_covered_by_archive="2024-01-01..2024-09-30 and 2027 (table taken from decrees as known)",
    )
    # settlement lag of acquiring / SBP: credit day = next working day after the registry date
    import re

    pat = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
    for category in ("acquiring", "sbp"):
        n = ok = 0
        for txs in data.values():
            for t in txs:
                if t["category"] != category:
                    continue
                m = pat.search(t["payment_purpose"])
                if not m:
                    continue
                reg = date(int(m[3]), int(m[2]), int(m[1]))
                credit = date.fromisoformat(t["operation_date"])
                n += 1
                ok += credit == cal.next_working_day(reg + timedelta(days=1))
        result[f"{category}_credit_is_next_working_day_after_registry"] = dict(
            checked=n, matched=ok, share=round(ok / n, 6) if n else None
        )
    return result


def rules_check(data):
    """Transfer rule per mechanism from history <= RULE_CUTOFF (before the evaluation slices)."""
    cutoff = RULE_CUTOFF.isoformat()
    shares = defaultdict(lambda: defaultdict(int))
    for txs in data.values():
        groups = defaultdict(set)
        for t in txs:
            if t["operation_date"] <= cutoff and (k := group_key(t)) and not k.endswith("|in"):
                groups[k].add(date.fromisoformat(t["operation_date"]))
        for key, days in groups.items():
            if len(days) < 6:
                continue
            lab = "fixed_monthly" if label_of(key).startswith("fixed") else label_of(key)
            clusters = 3 if lab == "salary" else 1
            for rule in ("next", "prev", "none"):
                fit = fit_nominal_days(days, rule, clusters, 3)
                shares[lab][rule] += sum(len(h) for _, h in fit)
            shares[lab]["observed_days"] += len(days)
    out = {}
    for lab, v in shares.items():
        total = v["observed_days"]
        out[lab] = dict(
            observed_days=total,
            **{f"share_explained_{r}": round(v[r] / total, 4) for r in ("next", "prev", "none")},
            chosen=max(("next", "prev", "none"), key=lambda r: v[r]),
        )
    return dict(history_until=cutoff, per_mechanism=out)


def fmt(x, pct=False):
    if x is None:
        return "-"
    return f"{x * 100:.1f}%" if pct else f"{x}"


def write_report(meta, summaries, cover, season_cmp, rules, calendar):
    L = []
    w = L.append
    w("# X2: календарь РФ и детекторы механизмов — отчёт\n")
    w(
        f"Срезы `as_of`: {meta['n_slices']} недельных, {meta['first']} .. {meta['last']}; "
        f"{meta['n_accounts']} компаний; {meta['n_windows']} окон «компания × срез». "
        f"Горизонт — {HORIZON} дней. Детекторы видят только операции с датой <= as_of; будущие "
        "операции используются только для оценки. Рецепт: mechanisms-v1.\n"
    )
    w("## 1. Календарь\n")
    c = calendar
    w(
        f"- Диапазон архива {c['archive_range'][0]} .. {c['archive_range'][1]}, операций "
        f"{c['operations_total']}.\n"
        f"- Нерабочих будних дней по таблице в диапазоне: {c['table_nonworking_weekdays_in_range']}; "
        f"операций в нерабочие дни по таблице: **{c['ops_on_table_nonworking_days']}**.\n"
        f"- Рабочие субботы в диапазоне (операций): {c['working_saturdays_in_range']}.\n"
        f"- Рабочие дни таблицы без операций: {len(c['working_days_without_ops_not_in_table'])}; "
        f"выходные с операциями, не являющиеся рабочими субботами: "
        f"{len(c['weekend_days_with_ops_not_working_saturday'])}.\n"
        f"- Зачисление эквайринга: дата зачисления = следующий рабочий день после даты реестра "
        f"в {c['acquiring_credit_is_next_working_day_after_registry']['matched']} из "
        f"{c['acquiring_credit_is_next_working_day_after_registry']['checked']} операций; СБП: "
        f"{c['sbp_credit_is_next_working_day_after_registry']['matched']} из "
        f"{c['sbp_credit_is_next_working_day_after_registry']['checked']}.\n"
        f"- Не покрыты архивом: {c['not_covered_by_archive']}. 2027 предварительный.\n"
    )
    w("## 2. Правила переноса (по истории до " + rules["history_until"] + ")\n")
    w(
        "| Механизм | наблюдаемых дат | объяснено «следующий р.д.» | «предыдущий р.д.» | «без переноса» | выбрано |"
    )
    w("|---|---:|---:|---:|---:|---|")
    for lab, v in rules["per_mechanism"].items():
        w(
            f"| {lab} | {v['observed_days']} | {fmt(v['share_explained_next'], True)} | "
            f"{fmt(v['share_explained_prev'], True)} | {fmt(v['share_explained_none'], True)} | "
            f"{v['chosen']} |"
        )
    w("")
    w("## 3. Точность по механизмам (±1 рабочий день)\n")
    w(
        "Precision = доля ожидаемых событий, совпавших с фактической операцией той же группы "
        "(компонента) в окне ±1 р.д. Recall = доля фактических дней операций механизма, "
        "совпавших с событием (все операции категорий механизма, включая группы, "
        "которые детектор не нашёл). «Recall по найденным» — только группы с компонентом. "
        "Ошибка даты — до ближайшего факта в пределах ±10 р.д. (среди найденных пар), "
        "MAPE суммы — по парам ±1 р.д.\n"
    )
    w(
        "| Механизм | компаний с компонентом | событий | факт. дней | precision | recall | "
        "recall по найденным | дата: точно | дата MAE, р.д. | дата ≤1 р.д. | MAPE суммы | "
        "медиана APE | APE ≤10% | WAPE | precision в окне дат | пар |"
    )
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in summaries:
        w(
            f"| {s['label']} | {s['accounts_with_component']} | {s['n_pred']} | "
            f"{s['n_actual_days']} | {fmt(s['precision_pm1wd'], True)} | "
            f"{fmt(s['recall_pm1wd'], True)} | {fmt(s['recall_detected_groups'], True)} | "
            f"{fmt(s['date_err_wd_nearest10_share_exact'], True)} | "
            f"{fmt(s['date_err_wd_nearest10_mae'])} | "
            f"{fmt(s['date_err_wd_nearest10_share_within1'], True)} | "
            f"{fmt(s['mape_amount'], True)} | {fmt(s['median_ape_amount'], True)} | "
            f"{fmt(s['share_ape_le_10'], True)} | {fmt(s['wape_amount'], True)} | "
            f"{fmt(s['precision_in_date_window'], True)} | {s['n_matched_pairs']} |"
        )
    w("")
    w("## 4. Доля суммы будущих 30-дневных потоков под механизмами\n")
    w(
        "Знаменатель — сумма всех фактических операций окна as_of+1..+30 данного направления "
        "по всем окнам. «Владение» — операции групп, для которых найден компонент. "
        "«Совпало» — суммы фактических дней, сопоставленных с событием ±1 р.д. "
        "«Прогноз/владение» — сумма ожидаемых событий к сумме фактических операций найденных групп. "
        "Сфера «operating» исключает unknown, owner_funding, loan_in, own_transfer, "
        "owner_withdrawal, refund.\n"
    )
    w("| Сфера | направление | знаменатель, руб. | владение | совпало | прогноз/владение |")
    w("|---|---|---:|---:|---:|---:|")
    for (scope, direction), v in cover.items():
        t = v["total"]
        w(
            f"| {scope} | {direction} | {t / 100:,.0f} | {fmt(v['owned'] / t, True)} | "
            f"{fmt(v['matched'] / t, True)} | "
            f"{(fmt(round(v['pred'] / v['owned_actual'], 3)) if v['owned_actual'] else '-')} |"
        )
    w("")
    w("## 5. Сезонность в модели эквайринга и СБП (диагностика)\n")
    w(
        "Дефолт «день недели × сезон» зафиксирован заранее по плану D5 (сезонный индекс включается "
        "только при истории >= 13 месяцев). Вариант без сезона посчитан на тех же срезах только "
        "для сравнения и не использовался для выбора.\n"
    )
    w("| Канал | вариант | precision | recall | MAPE суммы | прогноз/факт найденных групп |")
    w("|---|---|---:|---:|---:|---:|")
    for lab, variants in season_cmp.items():
        for name, s in variants.items():
            w(
                f"| {lab} | {name} | {fmt(s['precision_pm1wd'], True)} | "
                f"{fmt(s['recall_pm1wd'], True)} | {fmt(s['mape_amount'], True)} | "
                f"{fmt(s['sum_pred_over_actual_detected'])} |"
            )
    w("")
    commentary = OUT / "commentary.md"
    if commentary.exists():
        w(commentary.read_text(encoding="utf-8"))
    (OUT / "report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    svc = service()
    data = {a: txs for a, (txs, _) in account_data(svc).items()}
    calendar = calendar_check(data)
    (OUT / "calendar_check.json").write_text(
        json.dumps(calendar, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    rules = rules_check(data)
    (OUT / "rules.json").write_text(
        json.dumps(rules, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    as_ofs, acc, cover, per_slice, _, mech_counts = evaluate(data, use_season=True)
    summaries = [summarize(acc, lab) for lab in MECH_LABELS if lab in acc]
    # aggregate "fixed" over categories
    fixed = new_acc()
    for lab in MECH_LABELS:
        if lab.startswith("fixed:") and lab in acc:
            a = acc[lab]
            for k, v in a.items():
                if isinstance(v, set):
                    fixed[k] |= v
                elif isinstance(v, list):
                    fixed[k].extend(v)
                else:
                    fixed[k] += v
    acc["fixed_all"] = fixed
    summaries.insert(3, summarize(acc, "fixed_all"))
    _, acc2, _, _, _, _ = evaluate(data, use_season=False)
    season_cmp = {
        lab: {
            "dow x season (default)": summarize(acc, lab),
            "dow only": summarize(acc2, lab),
        }
        for lab in ("acquiring", "sbp")
        if lab in acc
    }
    meta = dict(
        first=as_ofs[0].isoformat(),
        last=as_ofs[-1].isoformat(),
        n_slices=len(as_ofs),
        n_accounts=len(data),
        n_windows=len(as_ofs) * len(data),
        horizon_days=HORIZON,
        components_detected_total=dict(mech_counts),
    )
    cover_json = {
        f"{s}/{d}": dict(
            total_rub=round(v["total"] / 100, 2),
            owned_share=round(v["owned"] / v["total"], 4) if v["total"] else None,
            matched_share=round(v["matched"] / v["total"], 4) if v["total"] else None,
            pred_over_owned_actual=round(v["pred"] / v["owned_actual"], 4)
            if v["owned_actual"]
            else None,
        )
        for (s, d), v in cover.items()
    }
    (OUT / "results.json").write_text(
        json.dumps(
            dict(
                meta=meta,
                mechanisms=summaries,
                coverage=cover_json,
                season_comparison=season_cmp,
                rules=rules,
                calendar=calendar,
            ),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    with (OUT / "per_slice.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(
            ["as_of", "mechanism", "n_pred", "pred_matched", "n_actual_days", "act_matched"]
        )
        for (as_of, lab), v in sorted(per_slice.items()):
            wr.writerow([as_of, lab, v["n_pred"], v["pred_matched"], v["n_act"], v["act_matched"]])
    write_report(meta, summaries, cover, season_cmp, rules, calendar)
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
