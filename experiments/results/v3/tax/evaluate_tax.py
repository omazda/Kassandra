"""X7: tax regime inference and ENP forecast on weekly slices.

Run from the project root:  uv run python experiments/results/v3/tax/evaluate_tax.py

Selection slices: weekly as_of 2026-03-01 .. 2026-05-31 (14). Retro: weekly 2026-07-01 ..
2026-08-26 (9, as in X0 protocol). Horizon 30 days. Detectors and the calendar see operations
<= as_of only; operations after as_of are used here only to score. The base-month definition and
three guards of the calendar were revised after the first selection run (see commentary.md,
item 7), so selection numbers are optimistic.

Methods: x7 (regime calendar, falls back to X2 when the regime is unknown / not sizeable),
x2 (DefaultTaxCalendar of X2), naive (median of the last three ENP payments, median day of month).
Outputs next to this file: results.json, regimes.csv, per_slice.csv, report.md
(report.md = generated tables + commentary.md when present).
"""

import csv
import json
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from cashflow_ml.calendar_ru import nominal_date, working_days_between  # noqa: E402
from cashflow_ml.mechanisms import detect_mechanisms  # noqa: E402
from cashflow_ml.tax_calendar import (  # noqa: E402
    ORDINARY_MONTHS,
    REGIME_LABELS,
    _is_enp,
    build_tax_calendar,
    naive_median_forecast,
    nominal_month,
)
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).resolve().parent
HORIZON = 30
SETS = {
    "selection": (date(2026, 3, 1), date(2026, 5, 31)),
    "retro": (date(2026, 7, 1), date(2026, 8, 31)),
}
METHODS = ("x7", "x2", "naive")


def slices(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def companies() -> dict[str, dict]:
    with open(ROOT / "dataset" / "companies.csv", encoding="utf-8-sig") as f:
        return {r["company_id"]: r for r in csv.DictReader(f, delimiter=";")}


def greedy_match(events: list[date], actual: list[date], tol: int = 1):
    pairs = sorted(
        (abs(working_days_between(min(e, a), max(e, a))), abs((e - a).days), i, j)
        for i, e in enumerate(events)
        for j, a in enumerate(actual)
        if abs(working_days_between(min(e, a), max(e, a))) <= tol
    )
    used_e, used_a, out = set(), set(), []
    for _dist, _, i, j in pairs:
        if i in used_e or j in used_a:
            continue
        used_e.add(i)
        used_a.add(j)
        out.append((i, j))
    return out


def new_acc():
    return dict(
        windows=0, sum_actual=0, sum_abs=0, sum_pred=0, ape=[], n_pred=0, n_act=0, matched=0,
        matched_ape=[], win_hit=0, win_n=0,
    )  # fmt: skip


def evaluate(data, comp_info):
    result = {}
    rows_slice = []
    regimes_at = {}
    per_company = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0, 0])))
    store = {}
    for set_name, (first, last) in SETS.items():
        acc = {m: defaultdict(new_acc) for m in METHODS}  # method -> group -> accumulator
        windows = []  # (account, as_of, month key, actual, {method: pred}, charge flag)
        for as_of in slices(first, last):
            lim = as_of.isoformat()
            end = as_of + timedelta(days=HORIZON)
            for account, (txs, _daily) in data.items():
                history = [t for t in txs if t["operation_date"] <= lim]
                future = [
                    t for t in txs if lim < t["operation_date"] <= end.isoformat() and _is_enp(t)
                ]
                actual_days = defaultdict(int)
                for t in future:
                    actual_days[date.fromisoformat(t["operation_date"])] += t["amount_minor"]
                legal = comp_info[account]["legal_form"]
                cal = build_tax_calendar(history, as_of, legal)
                if as_of in (SETS["selection"][0], SETS["retro"][0]):
                    regimes_at[(as_of.isoformat(), account)] = cal
                m7 = detect_mechanisms(history, as_of, tax_calendar=cal, use_season=False)
                m2 = detect_mechanisms(history, as_of, use_season=False)
                ev = {
                    "x7": [(e.date, e.amount_minor, e.window, e.mechanism)
                           for e in m7.events(HORIZON) if e.category == "tax"],
                    "x2": [(e.date, e.amount_minor, e.window, e.mechanism)
                           for e in m2.events(HORIZON) if e.category == "tax"],
                    "naive": [(d, a, None, "naive")
                              for d, a in naive_median_forecast(history, as_of, HORIZON)],
                }  # fmt: skip
                paid = defaultdict(int)
                for t in history:
                    if _is_enp(t):
                        paid[nominal_month(date.fromisoformat(t["operation_date"]))] += t[
                            "amount_minor"
                        ]
                store[(set_name, account, as_of)] = (ev, dict(paid), cal.regime.regime, cal.active)
                actual_total = sum(actual_days.values())
                groups = ["all"]
                if actual_total > 0:
                    # key: nominal month of the first ENP payment in the window
                    first_pay = min(actual_days)
                    groups.append("m" + "-".join(f"{x:02d}" for x in nominal_month(first_pay)))
                    charge_flag = any(
                        cal.is_advance_month(nominal_month(d)[1]) for d in actual_days
                    )
                    groups.append("charge" if charge_flag else "ordinary")
                    groups.append("regime:" + cal.regime.regime)
                else:
                    groups.append("no_actual")
                preds = {m: sum(a for _, a, _, _ in ev[m]) for m in METHODS}
                windows.append((account, as_of, groups, actual_total, preds, cal.active))
                for m in METHODS:
                    for g in groups:
                        a = acc[m][g]
                        a["windows"] += 1
                        a["sum_actual"] += actual_total
                        a["sum_pred"] += preds[m]
                        a["sum_abs"] += abs(preds[m] - actual_total)
                        if actual_total > 0:
                            a["ape"].append(abs(preds[m] - actual_total) / actual_total)
                    evs = sorted(ev[m], key=lambda e: e[0])
                    ad = sorted(actual_days)
                    pairs = greedy_match([e[0] for e in evs], ad)
                    for g in groups:
                        a = acc[m][g]
                        a["n_pred"] += len(evs)
                        a["n_act"] += len(ad)
                        a["matched"] += len(pairs)
                        for i, j in pairs:
                            a["matched_ape"].append(
                                abs(evs[i][1] - actual_days[ad[j]]) / actual_days[ad[j]]
                            )
                        for e in evs:
                            lo, hi = e[2] if e[2] else (e[0], e[0])
                            a["win_n"] += 1
                            a["win_hit"] += any(
                                working_days_between(min(lo, x), max(lo, x))
                                * (1 if x >= lo else -1)
                                >= -1
                                and working_days_between(min(x, hi), max(x, hi))
                                * (1 if hi >= x else -1)
                                >= -1
                                for x in ad
                            )
                    pc = per_company[set_name][m][account]
                    pc[0] += abs(preds[m] - actual_total)
                    pc[1] += actual_total
                rows_slice.append(
                    dict(set=set_name, as_of=as_of.isoformat(), account=account,
                         regime=cal.regime.regime, active=int(cal.active),
                         actual_rub=round(actual_total / 100, 2),
                         **{f"pred_{m}_rub": round(preds[m] / 100, 2) for m in METHODS},
                         x7_mechanisms="+".join(sorted({e[3] for e in ev["x7"]})))
                )  # fmt: skip
        result[set_name] = dict(acc=acc, windows=windows)
    return result, rows_slice, regimes_at, per_company, store


def summarize(a):
    ape = sorted(a["ape"])
    mape = sorted(a["matched_ape"])
    return dict(
        windows=a["windows"],
        windows_with_actual=len(ape),
        mape=round(sum(ape) / len(ape), 4) if ape else None,
        median_ape=round(ape[len(ape) // 2], 4) if ape else None,
        wape=round(a["sum_abs"] / a["sum_actual"], 4) if a["sum_actual"] else None,
        sum_pred_over_actual=round(a["sum_pred"] / a["sum_actual"], 4) if a["sum_actual"] else None,
        n_pred_events=a["n_pred"],
        n_actual_payment_days=a["n_act"],
        precision_date_pm1wd=round(a["matched"] / a["n_pred"], 4) if a["n_pred"] else None,
        recall_date_pm1wd=round(a["matched"] / a["n_act"], 4) if a["n_act"] else None,
        precision_date_in_window=round(a["win_hit"] / a["win_n"], 4) if a["win_n"] else None,
        matched_pairs=len(mape),
        median_ape_matched=round(mape[len(mape) // 2], 4) if mape else None,
    )


def paired_subset(windows, method_a, method_b=None):
    """Windows with actual ENP where every method produced a forecast > 0."""
    return [w for w in windows if w[3] > 0 and all(w[4][m] > 0 for m in METHODS)]


def subset_metrics(ws):
    out = {}
    for m in METHODS:
        ape = [abs(w[4][m] - w[3]) / w[3] for w in ws]
        out[m] = dict(
            n=len(ws),
            mape=round(sum(ape) / len(ape), 4) if ape else None,
            median_ape=round(sorted(ape)[len(ape) // 2], 4) if ape else None,
            wape=round(sum(abs(w[4][m] - w[3]) for w in ws) / sum(w[3] for w in ws), 4)
            if ws
            else None,
        )
    return out


def bootstrap_wape_ratio(per_company, set_name, a, b, n=1000, seed=42):
    """Companies are resampled; statistic = WAPE(a) / WAPE(b). Ratio < 1 means a is better."""
    rng = random.Random(seed)
    accounts = sorted(per_company[set_name][a])
    vals = []
    for _ in range(n):
        pick = [accounts[rng.randrange(len(accounts))] for _ in accounts]
        ea = sum(per_company[set_name][a][x][0] for x in pick)
        eb = sum(per_company[set_name][b][x][0] for x in pick)
        if eb:
            vals.append(ea / eb)
    vals.sort()
    return dict(
        point=round(
            sum(v[0] for v in per_company[set_name][a].values())
            / sum(v[0] for v in per_company[set_name][b].values()),
            4,
        ),
        lo=round(vals[int(0.025 * len(vals))], 4),
        hi=round(vals[int(0.975 * len(vals)) - 1], 4),
    )


def actual_months(data):
    """account -> {nominal (y, m): (total, date of the largest payment)} from the full archive."""
    out = {}
    for account, (txs, _) in data.items():
        tot, big = defaultdict(int), {}
        for t in txs:
            if not _is_enp(t):
                continue
            d = date.fromisoformat(t["operation_date"])
            ym = nominal_month(d)
            tot[ym] += t["amount_minor"]
            if ym not in big or t["amount_minor"] > big[ym][1]:
                big[ym] = (d, t["amount_minor"])
        out[account] = {ym: (tot[ym], big[ym][0]) for ym in tot}
    return out


LEAD = (7, 13)  # days between the slice and the nominal due date of the month


def monthly(store, data, amonths, calendar_due):
    """One forecast per company and nominal month: the slice 7..13 days before the due date.

    Month forecast = ENP already paid in that month by as_of + events of the method falling in it.
    """
    rows = []
    for (set_name, account, as_of), (ev, paid, regime, active) in store.items():
        for ym, (actual, main_day) in amonths[account].items():
            due = calendar_due(*ym)
            lead = (due - as_of).days
            if not LEAD[0] <= lead <= LEAD[1]:
                continue
            row = dict(set=set_name, account=account, as_of=as_of, month=f"{ym[0]}-{ym[1]:02d}", regime=regime,
                       active=active, actual=actual, main_day=main_day,
                       paid_before=paid.get(ym, 0))  # fmt: skip
            for m in METHODS:
                mine = [e for e in ev[m] if nominal_month(e[0]) == ym]
                row[m] = row["paid_before"] + sum(e[1] for e in mine)
                main = max(mine, key=lambda e: e[1]) if mine else None
                row[m + "_date"] = main[0] if main else None
                row[m + "_window"] = main[2] if main else None
            rows.append(row)
    return rows


def monthly_summary(rows):
    def agg(rs):
        out = {}
        for m in METHODS:
            ape = sorted(abs(r[m] - r["actual"]) / r["actual"] for r in rs)
            out[m] = dict(
                n=len(rs),
                mape=round(sum(ape) / len(ape), 4) if ape else None,
                mape_capped=round(sum(min(a, 1.0) for a in ape) / len(ape), 4) if ape else None,
                share_ape_gt_300=round(sum(a > 3 for a in ape) / len(ape), 4) if ape else None,
                median_ape=round(ape[len(ape) // 2], 4) if ape else None,
                p90_ape=round(ape[int(0.9 * len(ape))], 4) if ape else None,
                wape=round(
                    sum(abs(r[m] - r["actual"]) for r in rs) / sum(r["actual"] for r in rs), 4
                )
                if rs
                else None,
                bias=round(sum(r[m] for r in rs) / sum(r["actual"] for r in rs), 4) if rs else None,
                share_within_10pct=round(sum(a <= 0.1 for a in ape) / len(ape), 4) if ape else None,
            )
            hits = tot = whit = 0
            for r in rs:
                if r["main_day"] <= r["as_of"]:
                    continue  # main payment was already made at as_of: nothing to date
                tot += 1
                d = r[m + "_date"]
                if (
                    d is not None
                    and abs(working_days_between(min(d, r["main_day"]), max(d, r["main_day"]))) <= 1
                ):
                    hits += 1
                w = r[m + "_window"]
                if w and w[0] - timedelta(days=1) <= r["main_day"] <= w[1] + timedelta(days=1):
                    whit += 1
                elif d is not None and not w and abs((d - r["main_day"]).days) <= 1:
                    whit += 1
            out[m].update(
                dated_months=tot,
                date_hit_pm1wd=round(hits / tot, 4) if tot else None,
                date_hit_with_window=round(whit / tot, 4) if tot else None,
            )
        return out

    res = {}
    for set_name in SETS:
        rs = [r for r in rows if r["set"] == set_name and r["actual"] > 0]
        entry = {"all": agg(rs)}
        for key in sorted({r["month"] for r in rs}):
            entry["month:" + key] = agg([r for r in rs if r["month"] == key])
        for key in sorted({r["regime"] for r in rs}):
            entry["regime:" + key] = agg([r for r in rs if r["regime"] == key])
        for key, flag in (("ordinary_month", True), ("charge_month", False)):
            entry["type:" + key] = agg(
                [r for r in rs if (int(r["month"][5:]) in ORDINARY_MONTHS) == flag]
            )
        entry["calendar_active"] = agg([r for r in rs if r["active"]])
        res[set_name] = entry
    return res


def regime_tables(regimes_at, comp_info):
    out = {}
    rows = []
    for (as_of, account), cal in sorted(regimes_at.items()):
        info = comp_info[account]
        r = cal.regime
        rows.append(
            dict(as_of=as_of, company_id=account, legal_form=info["legal_form"],
                 industry=info["industry"], regime=r.regime, confidence=r.confidence,
                 active=int(cal.active), rate_eff=cal.params.get("rate_eff"),
                 on_due_share=cal.params.get("on_due_share"),
                 reason=r.evidence.get("reason") or r.evidence.get("rule"))
        )  # fmt: skip
    for as_of in sorted({r["as_of"] for r in rows}):
        sub = [r for r in rows if r["as_of"] == as_of]
        conf = defaultdict(list)
        for r in sub:
            conf[r["regime"]].append(r["confidence"])
        out[as_of] = dict(
            n=len(sub),
            counts=dict(Counter(r["regime"] for r in sub)),
            active=sum(r["active"] for r in sub),
            by_legal_form={
                lf: dict(Counter(r["regime"] for r in sub if r["legal_form"] == lf))
                for lf in ("ООО", "ИП")
            },
            agriculture=dict(
                Counter(r["regime"] for r in sub if r["industry"].startswith("Сельское"))
            ),
            non_agriculture_esxn=sum(
                1 for r in sub if r["regime"] == "esxn" and not r["industry"].startswith("Сельское")
            ),
            confidence_median={k: round(statistics.median(v), 3) for k, v in conf.items()},
            reasons=dict(Counter(r["reason"] for r in sub if r["regime"] == "unknown")),
        )
    return out, rows


def stability(data, comp_info):
    """Share of companies whose regime differs between consecutive weekly slices."""
    prev, changes, total = {}, 0, 0
    for as_of in slices(*SETS["selection"]):
        lim = as_of.isoformat()
        for account, (txs, _) in data.items():
            h = [t for t in txs if t["operation_date"] <= lim]
            r = build_tax_calendar(h, as_of, comp_info[account]["legal_form"]).regime.regime
            if account in prev:
                total += 1
                changes += r != prev[account]
            prev[account] = r
    return dict(transitions=total, changed=changes, share=round(changes / total, 4))


def main():
    t0 = time.time()
    svc = service()
    data = account_data(svc)
    comp_info = companies()
    result, rows_slice, regimes_at, per_company, store = evaluate(data, comp_info)
    reg, reg_rows = regime_tables(regimes_at, comp_info)
    mrows = monthly(store, data, actual_months(data), lambda y, m: nominal_date(y, m, 28, "next"))
    msum = monthly_summary(mrows)
    with open(OUT / "monthly_rows.csv", "w", newline="", encoding="utf-8-sig") as f:
        fields = ["set", "account", "month", "as_of", "regime", "active", "actual", "paid_before"]
        fields += list(METHODS) + [m + "_date" for m in METHODS]
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in mrows:
            w.writerow({k: (v / 100 if k in ("actual", "paid_before", *METHODS) else v)
                        for k, v in r.items()})  # fmt: skip
    out = dict(
        horizon_days=HORIZON,
        monthly=msum,
        companies=len(data),
        regimes=reg,
        regime_stability_selection=stability(data, comp_info),
        sets={},
    )
    for set_name, res in result.items():
        windows = res["windows"]
        entry = dict(slices=len(slices(*SETS[set_name])), windows=len(windows), methods={})
        for m in METHODS:
            entry["methods"][m] = {
                g: summarize(a)
                for g, a in sorted(res["acc"][m].items())
                if not g.startswith("regime:") or a["windows"]
            }
        entry["paired_subset_all_methods_forecast"] = subset_metrics(paired_subset(windows, "x7"))
        active = [w for w in windows if w[3] > 0 and w[5]]
        entry["active_calendar_windows"] = subset_metrics(active)
        entry["bootstrap_wape_ratio_x7_vs_naive"] = bootstrap_wape_ratio(
            per_company, set_name, "x7", "naive"
        )
        entry["bootstrap_wape_ratio_x7_vs_x2"] = bootstrap_wape_ratio(
            per_company, set_name, "x7", "x2"
        )
        out["sets"][set_name] = entry
    out["runtime_s"] = round(time.time() - t0, 1)
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    with open(OUT / "regimes.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(reg_rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(reg_rows)
    with open(OUT / "per_slice.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_slice[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows_slice)
    write_report(out)
    print("done", out["runtime_s"], "s")


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def write_report(out):
    L = ["# X7. Налоговый календарь: режим и события ЕНП", ""]
    L += [
        f"Горизонт {out['horizon_days']} дней, {out['companies']} компаний. Всё, что видят "
        "детектор режима и календарь, имеет operation_date <= as_of; факт ЕНП после as_of "
        "используется только для оценки. Из факта исключены взыскания по поручению налогового "
        "органа.",
        "",
        "## 1. Распределение режимов",
        "",
    ]
    for as_of, r in out["regimes"].items():
        L.append(f"### as_of {as_of} ({r['n']} компаний)")
        L.append("")
        L.append("| Режим | Компаний | Доля | Медиана уверенности |")
        L.append("|---|---:|---:|---:|")
        for reg in ("usn_6", "usn_15", "osno", "esxn", "unknown"):
            n = r["counts"].get(reg, 0)
            L.append(
                f"| {REGIME_LABELS[reg]} | {n} | {pct(n / r['n'])} | "
                f"{r['confidence_median'].get(reg, '-')} |"
            )
        L.append("")
        L.append(
            f"Календарь способен сам оценить суммы (active) у {r['active']} из {r['n']}; "
            f"у остальных события даёт X2. Причины unknown: {r['reasons']}. По форме: "
            f"{r['by_legal_form']}. Сельское хозяйство (7 компаний, отрасль режиму не "
            f"подаётся): {r['agriculture']}; ЕСХН вне сельского хозяйства: "
            f"{r['non_agriculture_esxn']}."
        )
        L.append("")
    st = out["regime_stability_selection"]
    L += [
        f"Устойчивость на срезах выбора: режим менялся в {st['changed']} из {st['transitions']} "
        f"переходов между соседними недельными срезами ({pct(st['share'])}).",
        "",
        "Эталонных меток режима в архиве нет, поэтому точность режима не измеряется; "
        "уверенность отражает число и согласованность наблюдений, а не вероятность верного "
        "ответа.",
        "",
    ]
    L += [
        "## 2. MAPE суммы ЕНП по месяцам (основная метрика)",
        "",
        "Одна оценка на компанию и номинальный месяц: срез за 7-13 дней до срока уплаты "
        "(28-е с переносом). Прогноз месяца = ЕНП, уже уплаченный в этом месяце к as_of, плюс "
        "события метода в этом месяце. Факт = все платежи ЕНП месяца (без взысканий). "
        "Месяцы без платежа ЕНП не входят. Срезы выбора дают месяцы 2026-03..05 "
        "(фев и июнь не покрыты: срезы начинаются 03-01 и кончаются 05-31), ретро - 2026-07, "
        "2026-08. Попадание даты: главный платёж месяца (наибольший по сумме) и главное событие "
        "метода, +-1 рабочий день; считается только там, где главный платёж позже as_of.",
        "",
    ]
    for set_name, title in (("selection", "срезы выбора"), ("retro", "ретро")):
        mm = out["monthly"][set_name]
        L += [f"### {title}", "", "| Группа | Месяцев | Метод | MAPE | MAPE (APE <= 100%) | Медиана APE | P90 APE | "
              "WAPE | прогноз / факт | в пределах 10% | Дата +-1 р.д. | Дата с окном |",
              "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]  # fmt: skip
        for g, per in mm.items():
            for m in METHODS:
                s_ = per[m]
                L.append(
                    f"| {g} | {s_['n']} | {m} | {pct(s_['mape'])} | {pct(s_['mape_capped'])} | "
                    f"{pct(s_['median_ape'])} | "
                    f"{pct(s_['p90_ape'])} | {pct(s_['wape'])} | {s_['bias']} | "
                    f"{pct(s_['share_within_10pct'])} | {pct(s_['date_hit_pm1wd'])} | "
                    f"{pct(s_['date_hit_with_window'])} |"
                )
        L.append("")
    n = 3
    for set_name, title in (("selection", "срезы выбора"), ("retro", "ретро")):
        e = out["sets"][set_name]
        L += [
            f"## {n}. Окна 30 дней (вторичная метрика): {title}",
            "",
            f"Срезов {e['slices']}, окон (компания x срез) {e['windows']}. Окно с фактом ЕНП: "
            f"{e['methods']['x7']['all']['windows_with_actual']}.",
            "",
            "Сумма ЕНП в окне (вся выборка окон с фактом; прогноз 0 считается ошибкой 100%):",
            "",
            "| Метод | MAPE | Медиана APE | WAPE | прогноз / факт |",
            "|---|---:|---:|---:|---:|",
        ]
        for m in METHODS:
            s = e["methods"][m]["all"]
            L.append(
                f"| {m} | {pct(s['mape'])} | {pct(s['median_ape'])} | {pct(s['wape'])} | "
                f"{s['sum_pred_over_actual']} |"
            )
        b1, b2 = e["bootstrap_wape_ratio_x7_vs_naive"], e["bootstrap_wape_ratio_x7_vs_x2"]
        L += [
            "",
            f"WAPE x7 / WAPE naive = {b1['point']} (95% по компаниям {b1['lo']}..{b1['hi']}); "
            f"WAPE x7 / WAPE x2 = {b2['point']} ({b2['lo']}..{b2['hi']}). Меньше 1 - лучше.",
            "",
            "Окна, где все три метода дали прогноз (одинаковая выборка):",
            "",
            "| Метод | Окон | MAPE | Медиана APE | WAPE |",
            "|---|---:|---:|---:|---:|",
        ]
        for m in METHODS:
            s = e["paired_subset_all_methods_forecast"][m]
            L.append(
                f"| {m} | {s['n']} | {pct(s['mape'])} | {pct(s['median_ape'])} | {pct(s['wape'])} |"
            )
        L += [
            "",
            "Только окна, где календарь режима вёл ЕНП сам (active):",
            "",
            "| Метод | Окон | MAPE | Медиана APE | WAPE |",
            "|---|---:|---:|---:|---:|",
        ]
        for m in METHODS:
            s = e["active_calendar_windows"][m]
            L.append(
                f"| {m} | {s['n']} | {pct(s['mape'])} | {pct(s['median_ape'])} | {pct(s['wape'])} |"
            )
        L += [
            "",
            "По типу окна и по месяцу (окна с фактом; месяц = номинальный месяц первого "
            "платежа в окне):",
            "",
            "| Группа | Окон | MAPE x7 | MAPE x2 | MAPE naive | WAPE x7 | WAPE x2 | WAPE naive |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        groups = sorted(
            g for g in e["methods"]["x7"] if g.startswith(("m", "charge", "ordinary")) 
        )  # fmt: skip
        for g in groups:
            s = {m: e["methods"][m][g] for m in METHODS}
            L.append(
                f"| {g} | {s['x7']['windows_with_actual']} | {pct(s['x7']['mape'])} | "
                f"{pct(s['x2']['mape'])} | {pct(s['naive']['mape'])} | {pct(s['x7']['wape'])} | "
                f"{pct(s['x2']['wape'])} | {pct(s['naive']['wape'])} |"
            )
        L += [
            "",
            "По режиму (окна с фактом):",
            "",
            "| Режим | Окон | MAPE x7 | MAPE x2 | MAPE naive | WAPE x7 | WAPE x2 | WAPE naive |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for g in sorted(g for g in e["methods"]["x7"] if g.startswith("regime:")):
            s = {m: e["methods"][m][g] for m in METHODS}
            L.append(
                f"| {g[7:]} | {s['x7']['windows_with_actual']} | {pct(s['x7']['mape'])} | "
                f"{pct(s['x2']['mape'])} | {pct(s['naive']['mape'])} | {pct(s['x7']['wape'])} | "
                f"{pct(s['x2']['wape'])} | {pct(s['naive']['wape'])} |"
            )
        L += [
            "",
            "Попадание даты (событие в пределах +-1 рабочего дня от платежа ЕНП, "
            "сопоставление один к одному):",
            "",
            "| Метод | Прогноз-событий | Факт-дней | Precision | Recall | Precision с окном дат | "
            "Медиана APE по парам |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for m in METHODS:
            s = e["methods"][m]["all"]
            L.append(
                f"| {m} | {s['n_pred_events']} | {s['n_actual_payment_days']} | "
                f"{pct(s['precision_date_pm1wd'])} | {pct(s['recall_date_pm1wd'])} | "
                f"{pct(s['precision_date_in_window'])} | {pct(s['median_ape_matched'])} |"
            )
        L.append("")
        n += 1
    L.append(f"Время прогона: {out['runtime_s']} с.")
    commentary = OUT / "commentary.md"
    if commentary.exists():
        L += ["", commentary.read_text(encoding="utf-8")]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
