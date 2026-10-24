"""Value of an additional source (criterion 3): registers of documents on the protocol v3 windows.

Payables: invoices/UPD/acts received from suppliers. A document enters the register of a window
only if its date is <= as_of (it was in hand); its amount is what was paid on it after as_of (the
invoice amount); the expected payment date is the document date plus this company's median
document-to-payment lag over payments made before as_of. Suppliers' invoices are nearly always
paid, so the survivorship bias of reading the register from later payments is small.

Receivables (customer invoices) are measured the same way but only as an upper bound: a real
register would also hold invoices that are never paid, which the archive cannot show.

Run from the repository root: uv run python experiments/results/v3/sources/evaluate_sources.py
"""

import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from cashflow_domain.engine import assemble, flow_shares, net_known_events  # noqa: E402
from cashflow_domain.schemas import PlannedEvent  # noqa: E402
from cashflow_ml import protocol_v3 as p3  # noqa: E402
from cashflow_ml.calendar_ru import next_working_day  # noqa: E402
from cashflow_ml.documents import parse_purpose, party_key  # noqa: E402
from cashflow_services.cli import account_data, service  # noqa: E402

OUT = Path(__file__).parent
HORIZON = 30
FALLBACK_LAG = {"out": 7, "in": 14}
REGISTERS: dict = {}


def documents(transactions):
    """Paid documents: (direction, party, kind, number, doc_date) -> list of (pay_date, amount)."""
    docs = defaultdict(list)
    for tx in transactions:
        parsed = parse_purpose(tx.get("payment_purpose"))
        doc = parsed.document if parsed else None
        if doc is None:
            continue
        key = (tx["direction"], party_key(tx), doc.kind, doc.number, doc.doc_date)
        docs[key].append(
            (date.fromisoformat(tx["operation_date"]), tx["amount_minor"], tx["category"])
        )
    return docs


def register(account, docs, origin, direction):
    """Register on `origin`: documents dated <= origin and paid after it."""
    lags = [
        (pays[0][0] - key[4]).days
        for key, pays in docs.items()
        if key[0] == direction and pays[0][0] <= origin and 0 <= (pays[0][0] - key[4]).days <= 120
    ]
    lag = int(statistics.median(lags)) if len(lags) >= 5 else FALLBACK_LAG[direction]
    events = []
    for key, pays in docs.items():
        if key[0] != direction or key[4] > origin or key[4] < origin - timedelta(days=120):
            continue
        future = [(d, a, c) for d, a, c in pays if d > origin]
        if not future:
            continue
        expected = next_working_day(max(key[4] + timedelta(days=lag), origin + timedelta(days=1)))
        events.append(
            PlannedEvent(
                id=f"doc:{account}:{key[1]}:{key[3]}:{key[4]}"[:160],
                account_id=account,
                component_id="doc",
                category=future[0][2] if direction == "out" else "customer_payment",
                amount_minor=sum(a for _, a, _ in future),
                direction=direction,
                due_date=expected,
                expected_date=expected,
                known_at=origin,
                source="invoice",
                status="confirmed",
            )
        )
    return events


def with_register(base_name, directions, netting=True):
    base = p3.METHODS[base_name]

    def forecaster(transactions, daily, as_of, ctx):
        flows, _, scale = ctx.prepared(False)
        if base_name == "hgb_v2":
            from threadpoolctl import threadpool_limits

            from cashflow_ml.features import feature_matrix
            from cashflow_ml.models import load_model

            meta = ctx.registry.select(as_of, False)
            with threadpool_limits(limits=1):
                values = (
                    load_model(meta["path"]).predict(feature_matrix(flows, as_of, scale)) * scale
                )
        else:
            past = flows.tail(90)
            by_dow = past.groupby(past.index.dayofweek).mean()
            values = np.asarray(
                [
                    by_dow.loc[(as_of + timedelta(days=h)).weekday()].to_numpy()
                    if (as_of + timedelta(days=h)).weekday() in by_dow.index
                    else past.mean().to_numpy()
                    for h in range(1, HORIZON + 1)
                ]
            )
        values = np.maximum(0, values)
        events = [e for d in directions for e in REGISTERS.get((ctx.account_id, as_of, d), [])]
        if netting:
            values, report = net_known_events(
                values, events, as_of, flow_shares(transactions, as_of)
            )
        points = assemble(as_of, ctx.opening_minor, values, events)
        return p3.Forecast([p.closing_balance_minor for p in points])

    forecaster.__name__ = f"{base.__name__}_with_{'_'.join(directions)}"
    return forecaster


def paired(records, base, variant, key):
    """Median over companies of (base error - variant error) / base error: > 0 is better."""
    by = defaultdict(lambda: defaultdict(list))
    for r in records:
        if r["method"] in (base, variant):
            by[r["account_id"]][r["method"]].append(r[key])
    gains = [
        (np.mean(m[base]) - np.mean(m[variant])) / np.mean(m[base])
        for m in by.values()
        if m[base] and m[variant] and np.mean(m[base]) > 0
    ]
    return p3.bootstrap_median(gains)


def main():
    svc = service()
    data = account_data(svc)
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    origins = p3.expand_origins(config)
    origins = {k: v for k, v in origins.items() if k in ("selection", "retrospective")}
    coverage = defaultdict(
        lambda: {
            "windows": 0,
            "with_payables": 0,
            "with_receivables": 0,
            "payables_minor": 0,
            "receivables_minor": 0,
        }
    )
    for account, (txs, _) in data.items():
        docs = documents(txs)
        for days in origins.values():
            for origin in days:
                for direction in ("out", "in"):
                    REGISTERS[(account, origin, direction)] = register(
                        account, docs, origin, direction
                    )
    methods = {
        "constant": p3.METHODS["constant"],
        "hgb_v2": p3.METHODS["hgb_v2"],
        "hgb+payables": with_register("hgb_v2", ["out"]),
        "hgb+payables_additive": with_register("hgb_v2", ["out"], netting=False),
        "hgb+receivables_ub": with_register("hgb_v2", ["in"]),
        "hgb+both_ub": with_register("hgb_v2", ["out", "in"]),
        "weekday": p3.METHODS["weekday"],
        "weekday+payables": with_register("weekday", ["out"]),
    }
    result = p3.run_protocol(
        data, methods, origins, registry=svc.registry, config=config, n_jobs=2, progress=print
    )
    records = result["records"]
    for r in records:
        if r["method"] != "constant":
            continue
        key = (r["account_id"], date.fromisoformat(r["origin"]))
        c = coverage[r["split"]]
        c["windows"] += 1
        pay, rec = REGISTERS.get((*key, "out"), []), REGISTERS.get((*key, "in"), [])
        horizon_end = key[1] + timedelta(days=HORIZON)
        pay_h = sum(e.amount_minor for e in pay if e.expected_date <= horizon_end)
        rec_h = sum(e.amount_minor for e in rec if e.expected_date <= horizon_end)
        c["with_payables"] += bool(pay_h)
        c["with_receivables"] += bool(rec_h)
        c["payables_minor"] += pay_h
        c["receivables_minor"] += rec_h
    summary = result["summary"]
    comparisons = {}
    for split in ("selection", "retrospective"):
        rows = [r for r in records if r["split"] == split]
        comparisons[split] = {
            f"{variant} vs {base}": {
                "trajectory": paired(rows, base, variant, "mae_traj_minor"),
                "minimum": paired(rows, base, variant, "mae_min_minor"),
            }
            for base, variant in [
                ("hgb_v2", "hgb+payables"),
                ("hgb_v2", "hgb+payables_additive"),
                ("hgb+payables_additive", "hgb+payables"),
                ("hgb_v2", "hgb+receivables_ub"),
                ("hgb_v2", "hgb+both_ub"),
                ("weekday", "weekday+payables"),
            ]
        }
    out = {
        "coverage": coverage,
        "comparisons": comparisons,
        "summary": summary,
        "dropped_windows": len(result["dropped_windows"]),
    }
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(
        json.dumps(
            {"coverage": coverage, "comparisons": comparisons},
            ensure_ascii=False,
            indent=1,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
