"""X6: интервалы и вероятность разрыва (D7). Запуск из корня:
    LOKY_MAX_CPU_COUNT=2 uv run python experiments/results/v3/x6/run_x6.py
Этап 1 (долгий): прогон протокола и анализ, запись results.json и артефакта калибровки.
Отчёт собирает make_report.py.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from cashflow_ml.calibration import build  # noqa: E402
from cashflow_services.cli import service  # noqa: E402

OUT = Path(__file__).resolve().parent


def main():
    t0 = time.time()
    config = json.loads((ROOT / "experiments/p3.json").read_text())
    svc = service()
    work = OUT / "_work"
    out = build(svc, config, svc.artifacts, work, 2, lambda t: print(t, flush=True))
    report = out["report"]
    report["dropped_windows"] = out["dropped_windows"]
    report["seconds"] = time.time() - t0
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    print(f"готово за {time.time() - t0:.0f} с", flush=True)


if __name__ == "__main__":
    main()
