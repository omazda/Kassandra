import argparse
import csv
import hashlib
import io
import json
import os
import secrets
import zipfile
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from cashflow_ml.evaluation import evaluate
from cashflow_ml.models import train_model
from cashflow_services.service import Service


def service():
    load_dotenv()
    return Service(
        os.getenv("CASHFLOW_DB", "data/cashflow.sqlite"),
        os.getenv("CASHFLOW_ARTIFACTS", "artifacts"),
    )


def account_data(svc):
    with svc.store.connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM accounts ORDER BY id")]
    return {a: (svc.store.transactions(a), svc.store.daily(a)) for a in ids}


def train_residual(svc, data, trained_to, kind=None, progress=print):
    """Обучить GBM остатка (X5) для structural: рецепт из решения X5 или явный kind.

    kind - ключ models.GBM_CONFIGS, с суффиксом ':cum' для постановки E05. Если решение X5 -
    профиль по дням недели и kind не задан, обучать нечего."""
    from cashflow_ml import structural

    kind = kind or structural.residual_decision()["residual"]
    if kind == "profile":
        progress("GBM остатка: решение X5 - профиль по дням недели, обучение не требуется")
        return None
    formulation = "cumulative" if kind.endswith(":cum") else "daily"
    info = structural.load_company_info(os.getenv("CASHFLOW_COMPANIES", "dataset/companies.csv"))
    industries = {a: v.get("industry", "") for a, v in info.items()}
    structural.configure(structural.build_universe(data, industries), info)
    dataset = structural.build_residual_dataset(data, trained_to)
    return structural.train_residual_gbm(
        dataset, kind.removesuffix(":cum"), svc.registry.root, formulation
    )


def dataset_digest(data):
    digest = hashlib.sha256()
    for a, (txs, _) in sorted(data.items()):
        digest.update(a.encode())
        for tx in txs:
            digest.update(tx["fingerprint"].encode())
            digest.update(tx["category"].encode())
    return digest.hexdigest()


def read_companies(raw: bytes) -> dict:
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";")
    return {c["company_id"]: c["company_name"] for c in rows}


def import_file(svc, path, demo=False):
    path = Path(path)
    names = {}
    if path.is_dir():
        # Unpacked archive: only the two top-level files; statements/ and nested copies are skipped.
        payload = (path / "all_operations.csv").read_bytes()
        names = read_companies((path / "companies.csv").read_bytes())
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            # Single explicit route. Never also import statements/.
            payload = z.read("all_operations.csv")
            names = read_companies(z.read("companies.csv"))
    else:
        payload = path.read_bytes()
    principal = {"id": "local", "accounts": ["*"]}
    report = svc.import_transactions(payload, principal, names, demo=demo)
    data = account_data(svc)
    reports = {a: svc.store.quality(a) for a in data}
    summary = {
        "companies": len(data),
        "operations": sum(len(t[0]) for t in data.values()),
        "inconsistent_transitions": sum(
            len(r["inconsistent_transitions"]) for r in reports.values()
        ),
        "material_transitions_over_one_cent": sum(
            abs(t["difference_minor"]) > 1
            for r in reports.values()
            for t in r["inconsistent_transitions"]
        ),
        "one_cent_transitions": sum(
            abs(t["difference_minor"]) == 1
            for r in reports.values()
            for t in r["inconsistent_transitions"]
        ),
        "suspicious_duplicate_groups": sum(
            len(r["suspicious_duplicates"]) for r in reports.values()
        ),
        "first_date": min(r["first_date"] for r in reports.values()),
        "last_date": max(r["last_date"] for r in reports.values()),
        "data_digest": dataset_digest(data),
        "source_sha256": hashlib.sha256(payload).hexdigest(),
    }
    output = Path("experiments/results")
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset_manifest.json").write_text(json.dumps(summary, indent=2))
    (output / "data_audit.json").write_text(
        json.dumps({"summary": summary, "accounts": reports}, ensure_ascii=False, indent=2)
    )
    print(json.dumps({"import": report, "audit": summary}, ensure_ascii=False, indent=2))


def evaluate_v3(svc, args):
    from cashflow_ml.protocol_v3 import METHODS, expand_origins, run_protocol

    config = json.loads(Path(args.config).read_text())
    if args.step_days:
        for split in ("selection", "retrospective"):
            config["splits"][split]["step_days"] = args.step_days
    names = args.methods.split(",") if args.methods else config["methods"]
    result = run_protocol(
        account_data(svc),
        {name: METHODS[name] for name in names},
        expand_origins(config),
        registry=svc.registry,
        config=config,
        out_dir=args.out,
        n_jobs=args.n_jobs or config.get("n_jobs", 2),
        progress=lambda t: print(t, flush=True),
    )
    print((Path(args.out) / "baseline.md").read_text())
    return result


def build_calibration(svc, args):
    """X6: артефакт калибровки structural (банк ошибок, конформные полосы, калибратор)."""
    from cashflow_ml.calibration import build

    config = json.loads(Path(args.config).read_text())
    out = build(
        svc,
        config,
        svc.artifacts,
        Path(args.work),
        n_jobs=args.n_jobs or config.get("n_jobs", 2),
        progress=lambda t: print(t, flush=True),
    )
    for key, v in out["report"]["variants"].items():
        print(key, v["chosen_simulation"], v["reliability_status"])
    return out


def build_ensemble(svc, args):
    """X10: веса ансамбля по горизонту и его банк ошибок (artifacts/ensemble/)."""
    from cashflow_ml import ensemble

    config = json.loads(Path(args.config).read_text())
    p1 = json.loads(Path(args.train_config).read_text())
    data = account_data(svc)
    fitset = ensemble.build_fitset(
        data,
        svc.registry,
        svc.artifacts,
        config,
        p1,
        dataset_digest(data),
        n_jobs=args.n_jobs or config.get("n_jobs", 2),
        progress=lambda t: print(t, flush=True),
    )
    model = ensemble.fit_variant(fitset, args.variant)
    path = ensemble.write_artifacts(svc.artifacts, model, ensemble.error_bank(fitset, model))
    print(f"{model['version']} -> {path}")
    return model


def main():
    parser = argparse.ArgumentParser(description="Kassandra reproducible commands")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path")
    imp.add_argument("--demo", action="store_true")
    train = sub.add_parser("train")
    train.add_argument("--trained-to", default="2025-12-31")
    train.add_argument("--config", default="experiments/p1.json")
    train.add_argument(
        "--residual-kind", default=None, help="рецепт GBM остатка; по умолчанию из X5"
    )
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("--config", default="experiments/p1.json")
    v3 = sub.add_parser("evaluate-v3")
    v3.add_argument("--config", default="experiments/p3.json")
    v3.add_argument("--out", default="experiments/results/v3/protocol")
    v3.add_argument("--methods", default=None, help="через запятую; по умолчанию из конфига")
    v3.add_argument("--step-days", type=int, default=None, help="шаг срезов выбора и ретро")
    v3.add_argument("--n-jobs", type=int, default=None)
    cal = sub.add_parser("build-calibration")
    cal.add_argument("--config", default="experiments/p3.json")
    cal.add_argument("--work", default="experiments/results/v3/x6/_work")
    cal.add_argument("--n-jobs", type=int, default=None)
    ens = sub.add_parser("build-ensemble")
    ens.add_argument("--config", default="experiments/p3.json")
    ens.add_argument("--train-config", default="experiments/p1.json")
    ens.add_argument("--variant", default=None, help="traj | bal; по умолчанию из рецепта X10")
    ens.add_argument("--n-jobs", type=int, default=None)
    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--dataset", default="cashflow_dataset_participants.zip")
    bootstrap.add_argument("--skip-training", action="store_true")
    sub.add_parser("audit")
    bank = sub.add_parser("build-error-bank")
    bank.add_argument("--config", default="experiments/p1.json")
    args = parser.parse_args()
    svc = service()
    if args.command == "import":
        import_file(svc, args.path, args.demo)
    elif args.command == "audit":
        print(Path("experiments/results/data_audit.json").read_text())
    elif args.command == "train":
        data = account_data(svc)
        config = json.loads(Path(args.config).read_text())
        for calendar in (False, True):
            meta = train_model(
                data,
                date.fromisoformat(args.trained_to),
                calendar,
                svc.registry,
                dataset_digest(data),
                config,
            )
            print(json.dumps(meta, indent=2))
        meta = train_residual(svc, data, date.fromisoformat(args.trained_to), args.residual_kind)
        if meta:
            print(json.dumps(meta, indent=2, ensure_ascii=False))
    elif args.command == "evaluate-v3":
        evaluate_v3(svc, args)
    elif args.command == "build-calibration":
        build_calibration(svc, args)
    elif args.command == "build-ensemble":
        from cashflow_ml.ensemble import DEFAULT_VARIANT

        args.variant = args.variant or DEFAULT_VARIANT
        build_ensemble(svc, args)
    elif args.command == "build-error-bank":
        # Probability for the default model; results go to artifacts/, tracked v2 files stay as is.
        _, bank = evaluate(
            account_data(svc),
            svc.registry,
            svc.artifacts / "evaluation",
            json.loads(Path(args.config).read_text()),
            progress=lambda t: print(t, flush=True),
        )
        (svc.artifacts / "error_bank.json").write_text(json.dumps(bank))
        print(f"Банк ошибок: {len(bank)} траекторий -> {svc.artifacts / 'error_bank.json'}")
    elif args.command == "evaluate":
        result, bank = evaluate(
            account_data(svc),
            svc.registry,
            Path("experiments/results"),
            json.loads(Path(args.config).read_text()),
            progress=lambda t: print(t, flush=True),
        )
        (svc.artifacts / "error_bank.json").write_text(json.dumps(bank))
        print(json.dumps(result["summary"], indent=2))
    else:
        # Never overwrite user settings or print generated credentials.
        if not Path(".env").exists():
            token = secrets.token_urlsafe(32)
            users = json.dumps({token: {"id": "local", "accounts": ["*"]}})
            body = f"CASHFLOW_AUTH_MODE=token\nCASHFLOW_USERS_JSON='{users}'\nDASH_TOKEN={token}\n"
            fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(body)
            print("Локальный доступ настроен в .env; секрет не выводится.")
        import_file(svc, args.dataset, demo=True)
        if not args.skip_training:
            data = account_data(svc)
            config = json.loads(Path("experiments/p1.json").read_text())
            for calendar in (False, True):
                print(
                    train_model(
                        data,
                        date.fromisoformat(config["trained_to"]),
                        calendar,
                        svc.registry,
                        dataset_digest(data),
                        config,
                    )["model_version"],
                    flush=True,
                )
            gbm = train_residual(svc, data, date.fromisoformat(config["trained_to"]))
            if gbm:
                print(gbm["model_version"], flush=True)


if __name__ == "__main__":
    main()
