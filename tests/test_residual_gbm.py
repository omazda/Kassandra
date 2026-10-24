from datetime import date, timedelta

import numpy as np
import pandas as pd
from test_structural import AS_OF, history, past, setup_universe, teardown_function  # noqa: F401

from cashflow_ml import structural as S
from cashflow_ml.features import (
    EXT_COLUMNS,
    FEATURE_VERSION,
    STREAMS,
    ExtraContext,
    feature_matrix,
    feature_matrix_ext,
    feature_matrix_width,
    industry_column,
)
from cashflow_ml.models import GBM_CONFIGS, GBMResidual, ModelRegistry


def flows(days=120):
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        rng.random((days, 3)) * 1000,
        index=pd.date_range(AS_OF - timedelta(days=days - 1), AS_OF),
        columns=STREAMS,
    )


def test_extended_features_shape_calendar_and_no_future():
    f = flows()
    ctx = ExtraContext(
        2, [AS_OF - timedelta(days=5), AS_OF + timedelta(days=10)], [AS_OF + timedelta(days=27)],
        0.3, np.ones((30, 2)) * 500,
    )  # fmt: skip
    x = feature_matrix_ext(f, AS_OF, 1000.0, ctx)
    assert x.shape == (30, feature_matrix_width() + len(EXT_COLUMNS))
    assert FEATURE_VERSION == "past-flow-v2"
    assert np.allclose(x[:, : feature_matrix_width()], feature_matrix(f, AS_OF, 1000.0))
    col = {name: feature_matrix_width() + i for i, name in enumerate(EXT_COLUMNS)}
    assert industry_column() == col["industry"] and set(x[:, col["industry"]]) == {2}
    d1 = AS_OF + timedelta(days=1)  # 2026-02-02, понедельник
    assert x[0, col["is_working"]] == 1 and x[0, col["is_holiday"]] == 0
    sat = (date(2026, 2, 7) - AS_OF).days - 1
    assert x[sat, col["is_working"]] == 0
    new_year = feature_matrix_ext(f, date(2025, 12, 31), 1000.0, ctx)
    assert new_year[0, col["is_holiday"]] == 1  # 1 января - будний нерабочий день
    # зарплата за 5 дней до as_of и ожидаемая через 10: за день до неё остался 1 день
    assert np.isclose(x[8, col["days_to_salary"]], 1 / 30)
    assert np.isclose(x[9, col["days_since_salary"]], 0.0)
    assert np.isclose(x[0, col["days_since_salary"]], 6 / 30)
    assert np.isclose(x[-1, col["registry_in_cum"]], 30 * 500 / 1000)
    del d1
    # будущее потока не влияет на признаки
    f2 = f.copy()
    assert np.allclose(x, feature_matrix_ext(f2, AS_OF, 1000.0, ctx))


def test_cumulative_formulation_roundtrip():
    y = np.zeros((30, 3))
    y[:, 2] = 100.0
    cum = S.window_targets(y, "cumulative")
    assert cum.shape == (3, 3) and cum[0, 2] == 700 and cum[2, 2] == 3000
    daily = S.cumulative_to_daily(cum, AS_OF)
    assert daily.shape == (30, 3)
    assert np.allclose(np.cumsum(daily, axis=0)[[6, 13, 29]], cum)
    # в нерабочие дни поток 0 (3 и 4 дня — суббота и воскресенье 7-8 февраля)
    assert daily[(date(2026, 2, 7) - AS_OF).days - 1, 2] == 0


def test_gbm_libraries_fit_predict_and_category():
    rng = np.random.default_rng(0)
    x = rng.random((400, 5))
    x[:, 3] = rng.integers(-1, 4, 400)
    y = np.column_stack([x[:, 0] + (x[:, 3] == 2), x[:, 1], rng.random(400) * 0.01])
    for kind in ("hgb_a", "lgbm_a", "cat_a"):
        library, params = GBM_CONFIGS[kind]
        model = GBMResidual(
            library, {**params, **({"iterations": 40} if library == "catboost" else {})}, 3
        )
        pred = model.fit(x, y).predict(x)
        assert pred.shape == (400, 3) and np.isfinite(pred).all()
        assert np.corrcoef(pred[:, 0], y[:, 0])[0, 1] > 0.8
        again = GBMResidual(library, model.params, 3).fit(x, y).predict(x)
        assert np.allclose(pred, again)  # seed 42, воспроизводимо


def test_dataset_target_excludes_registry_and_future_does_not_leak(tmp_path):
    txs = history()
    setup_universe(txs)
    S.configure(S._UNIVERSE, {"A": {"legal_form": "ООО", "industry": "Торговля"}})
    daily = [{"date": str(date(2025, 6, 1) + timedelta(days=k)), "valid": True} for k in range(300)]
    S._ARCHIVE_END["A"] = date(2026, 12, 31)
    data = {"A": (txs, daily)}
    ds = S.build_residual_dataset(data, date(2026, 1, 31))
    assert ds["x"].shape[1:] == (30, feature_matrix_width() + len(EXT_COLUMNS))
    assert ds["x"].shape[0] == ds["y"].shape[0] == len(ds["meta"]) > 0
    assert ds["industries"] == ["Торговля"] and set(ds["x"][:, :, industry_column()].ravel()) == {0}
    # клиентские платежи выставленных документов принадлежат реестру: в цели их нет
    # (оплата через 10 дней после документа: первые 10 дней горизонта принадлежат реестру)
    assert ds["y"][:, :10, 0].max() == 0 < ds["y"][:, 10:, 0].max()
    meta = S.train_residual_gbm(ds, "hgb_a", tmp_path)
    assert meta["kind"] == "hgb_a" and meta["training_rows"] == len(ds["meta"]) * 30
    assert ModelRegistry(tmp_path).select_residual(date(2026, 2, 1), "hgb_a")["kind"] == "hgb_a"
    cum = S.train_residual_gbm(ds, "hgb_a", tmp_path, "cumulative")
    assert cum["kind"] == "hgb_a:cum" and cum["training_rows"] == len(ds["meta"]) * 3
    # модель не видна до даты обучения
    try:
        ModelRegistry(tmp_path).select_residual(date(2026, 1, 1), "hgb_a")
        raise AssertionError("ожидалась ошибка")
    except ValueError:
        pass
    # структурный прогноз: GBM используется, будущее не влияет
    full = history()
    a = S.structural_forecast(past(full), AS_OF, "A", residual="hgb_a", models_root=tmp_path,
                              alpha=0.0)  # fmt: skip
    b = S.structural_forecast(full, AS_OF, "A", residual="hgb_a", models_root=tmp_path, alpha=0.0)
    assert a.meta["residual"] == "hgb_a" and a.meta["residual_model"] == meta["model_version"]
    assert np.allclose(a.values, b.values)
    e = S.structural_forecast(past(full), AS_OF, "A", residual="hgb_a:cum", models_root=tmp_path,
                              alpha=0.0)  # fmt: skip
    assert e.meta["residual"] == "hgb_a:cum" and (e.values >= 0).all()
    # без модели или без реестра остаток считается профилем
    p = S.structural_forecast(past(full), AS_OF, "A", residual="lgbm_a", models_root=tmp_path,
                              alpha=0.0)  # fmt: skip
    assert p.meta["residual"] == "profile"
    blended = S.structural_forecast(past(full), AS_OF, "A", residual="hgb_a", models_root=tmp_path,
                                    alpha=0.0, blend=0.5)  # fmt: skip
    prof = S.structural_forecast(past(full), AS_OF, "A", residual="profile", alpha=0.0)
    assert np.allclose(blended.values, 0.5 * a.values + 0.5 * prof.values)


def test_decision_file_default_is_profile_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "RESIDUAL_FILE", tmp_path / "none.json")
    assert S.residual_decision() == {"residual": "profile", "blend": 1.0}
    (tmp_path / "d.json").write_text('{"residual": "cat_a", "blend": 0.5}')
    monkeypatch.setattr(S, "RESIDUAL_FILE", tmp_path / "d.json")
    assert S.residual_decision() == {"residual": "cat_a", "blend": 0.5}
