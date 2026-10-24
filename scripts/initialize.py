"""Idempotent Compose initialization. Only this job reads the source archive."""

import json
import os
import secrets
from datetime import date
from pathlib import Path

from cashflow_ml.calibration import build as build_calibration
from cashflow_ml.evaluation import evaluate
from cashflow_ml.models import train_model
from cashflow_services.cli import account_data, dataset_digest, import_file, service

root = Path("/state")
settings = root / "runtime.env"
if not settings.exists():
    token = secrets.token_urlsafe(32)
    body = f"CASHFLOW_AUTH_MODE=token\nCASHFLOW_USERS_JSON='{json.dumps({token: {'id': 'local', 'accounts': ['*']}})}'\nDASH_TOKEN={token}\nCASHFLOW_DB=/state/data/cashflow.sqlite\nCASHFLOW_ARTIFACTS=/state/artifacts\n"
    fd = os.open(settings, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
svc = service()
import_file(svc, "/input/dataset", demo=True)
config = json.loads(Path("experiments/p1.json").read_text())
data = account_data(svc)
for calendar in (False, True):
    try:
        svc.registry.select(date.fromisoformat(config["trained_to"]), calendar)
    except ValueError:
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
if not (svc.artifacts / "error_bank.json").exists():
    _, bank = evaluate(data, svc.registry, svc.artifacts / "evaluation", config)
    (svc.artifacts / "error_bank.json").write_text(json.dumps(bank))
# Probability and P10/P90 of the structural model need this artifact.
if not (svc.artifacts / "calibration" / "structural.json").exists():
    build_calibration(
        svc,
        json.loads(Path("experiments/p3.json").read_text()),
        svc.artifacts,
        root / "calibration_work",
    )
# X10 ensemble (product default since 24.10.2026): horizon weights and their error bank.
if not (svc.artifacts / "ensemble" / "weights.json").exists():
    import subprocess

    subprocess.run(["cashflow", "build-ensemble"], check=True)
