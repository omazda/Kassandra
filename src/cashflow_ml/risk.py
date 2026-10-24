import json
from pathlib import Path

import numpy as np


def add_uncertainty(
    risk,
    points,
    as_of,
    recipe: str,
    scale: float,
    root: Path,
    external_events: bool,
    events=None,
    docs: bool = False,
):
    if recipe.startswith("structural|"):
        # X6: калиброванная симуляция по артефакту calibration/structural.json (D7).
        if external_events:
            risk.limitation = "Внешние события изменили разложение; перенос калибровки на них не проверен. Вероятность недоступна. Один счёт не равен всей ликвидности."
            return risk
        from cashflow_ml.calibration import add_calibrated_uncertainty

        return add_calibrated_uncertainty(risk, points, events or [], as_of, scale, root, docs)
    path = root / "error_bank.json"
    if recipe.startswith("ensemble|"):
        # X10: путь ансамбля не зависит от календаря (он меняет только разложение на события).
        path, recipe = root / "ensemble" / "error_bank.json", recipe.rsplit("|", 1)[0]
    bank = json.loads(path.read_text()) if path.exists() else []
    eligible = [b for b in bank if b["recipe"] == recipe and b["available_to"] <= str(as_of)]
    if len(eligible) < 20 or len({b["account_id"] for b in eligible}) < 5:
        return risk
    rng = np.random.default_rng(42)
    # Resample whole cumulative error paths. Never sample marginal days independently.
    selected = rng.integers(0, len(eligible), size=1000)
    errors = np.asarray([eligible[i]["normalized_balance_error"] for i in selected]) * scale
    central = np.asarray([p.closing_balance_minor for p in points])
    paths = central[None, :] + errors
    risk.probability_30d = float(np.mean(np.min(paths, axis=1) < risk.threshold_minor))
    risk.calibration_status = "experimental_uncalibrated"
    risk.probability_source = (
        "Повторная выборка целых внеобучающих 30-дневных траекторий; нормировка прошлым оборотом"
    )
    risk.trajectory_count, risk.simulations = len(eligible), 1000
    risk.limitation = "Экспериментальная модельная частота; калибровка E06 не выполнена. Зависимые окна и перенос между компаниями ограничивают интерпретацию. Один счёт не отражает всю ликвидность."
    if external_events:
        # Product decision: keep the probability with registered events, state the assumption.
        risk.probability_source += "; события реестра учтены как надёжные"
        risk.limitation = (
            "События реестра считаются надёжными: банк ошибок описывает только ошибку модели, "
            "поэтому при сдвиге событий риск выше — смотрите стресс-оценку. " + risk.limitation
        )
    quantiles = np.quantile(paths, [0.1, 0.9], axis=0)
    for i, point in enumerate(points):
        point.p10_minor, point.p90_minor = int(quantiles[0, i]), int(quantiles[1, i])
    if recipe.startswith("ensemble|"):
        calibrate_alarm(risk, as_of, root)
    return risk


def calibrate_alarm(risk, as_of, root: Path):
    """X11: isotonic calibration of the ensemble probability by label D2 and the alarm threshold.

    Fitted on the selection slice (targets up to `available_to`); earlier dates keep the raw
    frequency, otherwise the calibrator would know outcomes after as_of."""
    path = root / "gap_alarm.json"
    if not path.exists():
        # The fitted calibrator ships with the package; build_x11.py refits it into artifacts/.
        path = Path(__file__).with_name("gap_alarm.json")
    if risk.probability_30d is None or not path.exists():
        return risk
    data = json.loads(path.read_text())
    if str(as_of) <= data["available_to"]:
        return risk
    raw = risk.probability_30d
    risk.probability_30d = float(np.interp(raw, data["iso_x"], data["iso_y"]))
    risk.alarm = risk.probability_30d >= data["threshold"]
    risk.calibration_status = "calibrated_on_selection"
    risk.probability_source += (
        f"; изотоническая калибровка X11 по метке D2 (срез выбора, {data['windows']} окон), "
        f"сырая частота {raw:.0%}"
    )
    risk.limitation = (
        "Вероятность откалибрована на срезе выбора март–май 2026 и проверена на ретроспективе "
        f"июль–август: тревога при вероятности от {data['threshold']:.0%}. "
        "Разрыв — отрицательный остаток или день, который спасло пополнение владельца или кредит. "
        "Один счёт не отражает всю ликвидность."
    )
    return risk
