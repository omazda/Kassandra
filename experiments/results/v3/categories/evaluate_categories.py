"""X1: quality check of the categorizer on the frozen, hand-labelled sample of 400 operations.

Run from the repository root: uv run python experiments/results/v3/categories/evaluate_categories.py

Inputs: dataset/all_operations.csv and sample_400_labeled.csv (next to this file). The sample was
drawn once (seed 42, strata = predicted category x direction, templates round-robin inside a
stratum) and labelled by hand from the counterparty and purpose text, without looking at the
prediction. pred_firstpass is the prediction of the rules at the moment of labelling; the current
rules are re-applied here, so any later rule change is visible as a difference between the two.
"""

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd

from cashflow_services.categories import categorize, counterparty_template, purpose_template

HERE = Path(__file__).parent
DATASET = Path("dataset/all_operations.csv")
CRITICAL = {
    "acquiring", "sbp", "cash_in", "customer_payment", "marketplace_payout", "contract_prepayment",
    "contract_settlement", "revenue", "owner_funding", "loan_in", "own_transfer", "owner_withdrawal",
    "refund", "salary", "tax", "rent", "debt_service",
}  # fmt: skip


def legacy_categorize(purpose: str, direction: str) -> str:
    """The categorizer of data.py before X1 (kept only to show the before/after distribution)."""
    s = unicodedata.normalize("NFKC", purpose).lower().replace("ё", "е")
    rules = [
        ("own_transfer", r"собственн\w* средств|между счет|на свой счет|со своего счета"),
        ("refund", r"возврат|ошибочн"),
        (
            "owner_funding",
            r"учредител|владел|пополнение|финансов\w* помощ|взнос.*средств|договор.*займа",
        ),
        ("tax", r"налог|\bенп\b|\bенс\b|взнос.*страх|страхов\w* взнос"),
        ("salary", r"зарплат|заработн|аванс.*сотруд|аванс.*з/п|окончательн\w* расчет.*сотруд"),
        ("rent", r"аренд"),
        ("debt_service", r"кредит|лизинг|погашен.*займ|процент.*займ"),
        ("bank_fee", r"комисси|ведение счета|обслуживание счета|\bрко\b"),
    ]
    for category, pattern in rules:
        if re.search(pattern, s):
            return category
    if direction == "in" and re.search(r"оплат|выручк|эквайр|реализац|поступлен.*продаж", s):
        return "revenue"
    if direction == "out" and re.search(r"оплат|закуп|постав|услуг|товар|материал|расход", s):
        return "operating_expense"
    return "unknown"


def load_dataset() -> pd.DataFrame:
    df = pd.read_csv(
        DATASET,
        sep=";",
        encoding="utf-8-sig",
        dtype={"counterparty_inn": str},
        keep_default_na=False,
    )
    df["direction"] = df["direction"].map({"Поступление": "in", "Списание": "out"})
    df["amount_minor"] = (df["amount"] * 100).round().astype("int64")
    return df


def class_report(truth: pd.Series, pred: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    labels = sorted(set(truth) | set(pred))
    rows = []
    for label in labels:
        tp = int(((truth == label) & (pred == label)).sum())
        fp = int(((truth != label) & (pred == label)).sum())
        fn = int(((truth == label) & (pred != label)).sum())
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
        rows.append(
            dict(category=label, support=tp + fn, predicted=tp + fp, precision=precision,
                 recall=recall, f1=f1)
        )  # fmt: skip
    return pd.DataFrame(rows), pd.crosstab(truth, pred).reindex(
        index=labels, columns=labels, fill_value=0
    )


def summary(truth: pd.Series, pred: pd.Series, amounts: pd.Series) -> dict:
    report, matrix = class_report(truth, pred)
    present = report[(report["support"] > 0) | (report["predicted"] > 0)]
    critical = present[present["category"].isin(CRITICAL)]
    ok = truth == pred
    return dict(
        operations=len(truth),
        classes_with_support=len(present),
        accuracy=float(ok.mean()),
        errors=int((~ok).sum()),
        macro_f1=float(present["f1"].mean()),
        macro_f1_critical=float(critical["f1"].mean()),
        critical_classes=len(critical),
        macro_precision=float(present["precision"].dropna().mean()),
        macro_recall=float(present["recall"].dropna().mean()),
        amount_weighted_accuracy=float(amounts[ok].sum() / amounts.sum()),
        report=report,
        matrix=matrix,
    )


def distribution(df: pd.DataFrame, column: str) -> pd.DataFrame:
    grouped = df.groupby(["direction", column])["amount_minor"].agg(["size", "sum"]).reset_index()
    grouped.columns = ["direction", "category", "operations", "amount_minor"]
    totals = grouped.groupby("direction")["amount_minor"].transform("sum")
    grouped["amount_share"] = grouped["amount_minor"] / totals
    return grouped


def main() -> None:
    sample = pd.read_csv(HERE / "sample_400_labeled.csv", sep=";", encoding="utf-8-sig", dtype=str,
                         keep_default_na=False)  # fmt: skip
    sample["amount_minor"] = (sample["amount"].astype(float) * 100).round().astype("int64")
    sample["pred_current"] = [
        categorize(p, d, c)
        for p, d, c in zip(
            sample["payment_purpose"],
            sample["direction"],
            sample["counterparty_name"],
            strict=True,
        )
    ]
    result = {}
    for name, column in (("firstpass", "pred_firstpass"), ("current", "pred_current")):
        s = summary(sample["true_category"], sample[column], sample["amount_minor"])
        s["report"].round(4).to_csv(HERE / f"per_class_{name}.csv", index=False)
        s["matrix"].to_csv(HERE / f"confusion_{name}.csv")
        result[name] = {k: v for k, v in s.items() if k not in {"report", "matrix"}}
    result["sample_category_counts"] = dict(Counter(sample["true_category"]))
    changed = sample[sample["pred_firstpass"] != sample["pred_current"]]
    result["rules_changed_after_labelling"] = int(len(changed))

    full = load_dataset()
    full["after"] = [
        categorize(p, d, c)
        for p, d, c in zip(
            full["payment_purpose"], full["direction"], full["counterparty_name"], strict=True
        )
    ]
    full["before"] = [
        legacy_categorize(p, d)
        for p, d in zip(full["payment_purpose"], full["direction"], strict=True)
    ]
    before, after = distribution(full, "before"), distribution(full, "after")
    before["version"], after["version"] = "before", "after"
    pd.concat([before, after]).round(6).to_csv(HERE / "distribution_before_after.csv", index=False)

    rev = {"revenue", "acquiring", "sbp", "cash_in", "customer_payment", "marketplace_payout",
           "contract_prepayment", "contract_settlement"}  # fmt: skip
    inflow = full[full["direction"] == "in"]
    total_in = int(inflow["amount_minor"].sum())
    result["full_dataset"] = dict(
        operations=len(full),
        companies=int(full["company_id"].nunique()),
        inflow_amount_minor=total_in,
        revenue_share_of_inflow_before=float(
            inflow.loc[inflow["before"] == "revenue", "amount_minor"].sum() / total_in
        ),
        revenue_share_of_inflow_after=float(
            inflow.loc[inflow["after"].isin(rev), "amount_minor"].sum() / total_in
        ),
        financing_share_of_inflow_after=float(
            inflow.loc[inflow["after"].isin({"owner_funding", "loan_in", "own_transfer"}), "amount_minor"].sum()
            / total_in
        ),
        unknown_share_of_amount_before=float(
            full.loc[full["before"] == "unknown", "amount_minor"].sum() / full["amount_minor"].sum()
        ),
        unknown_share_of_amount_after=float(
            full.loc[full["after"] == "unknown", "amount_minor"].sum() / full["amount_minor"].sum()
        ),
        unknown_share_of_inflow_after=float(
            inflow.loc[inflow["after"] == "unknown", "amount_minor"].sum() / total_in
        ),
        unknown_operations_after=int((full["after"] == "unknown").sum()),
        distinct_counterparty_templates=int(full["counterparty_name"].map(counterparty_template).nunique()),
        distinct_purpose_templates=int(full["payment_purpose"].map(purpose_template).nunique()),
    )  # fmt: skip
    (HERE / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    changed.to_csv(
        HERE / "labelled_vs_current_diff.csv", sep=";", index=False, encoding="utf-8-sig"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
