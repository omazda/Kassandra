import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.multioutput import MultiOutputRegressor
from threadpoolctl import threadpool_limits

from cashflow_ml.features import FEATURE_VERSION, categories_at, feature_matrix, flow_frame, prepare


class ModelRegistry:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def select_residual(self, as_of: date, kind: str | None = None) -> dict:
        """Лучшая по trained_to GBM остатка (gres-*) для as_of; kind: имя рецепта или любой."""
        choices = []
        for path in self.root.glob("gres-*/metadata.json"):
            meta = json.loads(path.read_text())
            if meta["trained_to"] <= str(as_of) and kind in (None, meta["kind"]):
                choices.append((meta["trained_to"], path, meta))
        if not choices:
            raise ValueError(f"Нет обученной GBM остатка ({kind or 'любой'}) на {as_of}")
        _, path, meta = max(choices, key=lambda item: item[0])
        return {**meta, "path": str(path.parent / "model.joblib")}

    def select(self, as_of: date, calendar: bool, model_version: str | None = None) -> dict:
        choices = []
        for path in self.root.glob("hgb-*/metadata.json"):
            meta = json.loads(path.read_text())
            if (
                meta["trained_to"] <= str(as_of)
                and meta["calendar"] == calendar
                and (model_version is None or meta["model_version"] == model_version)
            ):
                choices.append((meta["trained_to"], path, meta))
        if not choices:
            raise ValueError(
                "Нет обученной модели для этой исторической даты и режима календаря. Выполните cashflow train или выберите базовый метод."
            )
        _, path, meta = max(choices, key=lambda item: item[0])
        return {**meta, "path": str(path.parent / "model.joblib")}


@lru_cache(maxsize=8)
def load_model(path):
    # Only locally trained artifacts from the configured private registry are loaded.
    return joblib.load(path)


def predict(
    transactions,
    as_of: date,
    model: str,
    use_calendar: bool,
    registry: ModelRegistry,
    model_version: str | None = None,
    account_id: str | None = None,
    legal_form: str | None = None,
    docs: bool = True,
    daily: list[dict] | None = None,
):
    if model == "ensemble":
        from cashflow_ml import ensemble

        return ensemble.predict(
            transactions, daily, as_of, account_id, registry, use_calendar, model_version
        )
    if model == "structural":
        from cashflow_ml.structural import predict_structural

        if not account_id:
            raise ValueError("Для структурной модели нужен account_id")
        result = predict_structural(
            transactions, as_of, account_id, legal_form, docs=docs, models_root=registry.root
        )
        if model_version is not None and result[3] != model_version:
            raise ValueError("Запрошенная версия модели недоступна на выбранную дату")
        return result
    flows, components, scale = prepare(transactions, as_of, use_calendar)
    trained_to, version = None, f"{model}-v1"
    if model == "constant":
        values = np.zeros((30, 3))
        components = {}  # Constant is strictly the last observed balance benchmark.
    elif model == "mean":
        values = np.tile(flows.tail(60).mean().to_numpy(), (30, 1))
    elif model == "weekday":
        past = flows.tail(90)
        by_dow = past.groupby(past.index.dayofweek).mean()
        values = np.asarray(
            [
                by_dow.loc[(as_of + timedelta(days=h)).weekday()].to_numpy()
                if (as_of + timedelta(days=h)).weekday() in by_dow.index
                else past.mean().to_numpy()
                for h in range(1, 31)
            ]
        )
    else:
        meta = registry.select(as_of, use_calendar, model_version)
        with threadpool_limits(limits=1):
            values = load_model(meta["path"]).predict(feature_matrix(flows, as_of, scale)) * scale
        trained_to, version = date.fromisoformat(meta["trained_to"]), meta["model_version"]
    if model_version is not None and version != model_version:
        raise ValueError("Запрошенная версия модели недоступна на выбранную дату")
    return np.maximum(0, values), components, scale, version, trained_to


def training_origins(start: date, trained_to: date):
    cursor = start + timedelta(days=89)
    while cursor + timedelta(days=30) <= trained_to:
        yield cursor
        cursor += timedelta(days=28)


def train_model(
    account_data: dict,
    trained_to: date,
    calendar: bool,
    registry: ModelRegistry,
    data_digest: str,
    config: dict,
) -> dict:
    features, targets, origins_manifest = [], [], []
    for account_id, (txs, daily) in sorted(account_data.items()):
        if not daily:
            continue
        valid = {d["date"]: d["valid"] for d in daily}
        start = date.fromisoformat(daily[0]["date"])
        last = min(trained_to, date.fromisoformat(daily[-1]["date"]))
        for origin in training_origins(start, last):
            if not all(valid.get(str(origin + timedelta(days=h)), False) for h in range(-89, 31)):
                continue
            flows, components, scale = prepare(txs, origin, calendar)
            x = feature_matrix(flows, origin, scale)
            future = flow_frame(
                categories_at(txs, origin),
                origin + timedelta(days=1),
                origin + timedelta(days=30),
                components,
            )
            features.append(x)
            targets.append(future.to_numpy() / scale)
            origins_manifest.append(
                {
                    "account_id": account_id,
                    "origin": str(origin),
                    "target_end": str(origin + timedelta(days=30)),
                }
            )
    if not features:
        raise ValueError("Нет пригодных обучающих окон с полным будущим горизонтом")
    x, y = np.concatenate(features), np.concatenate(targets)
    params = config["hgb"]
    estimator = MultiOutputRegressor(HistGradientBoostingRegressor(**params, random_state=42))
    with threadpool_limits(limits=1):
        estimator.fit(x, y)
    recipe = {
        "trained_to": str(trained_to),
        "calendar": calendar,
        "data_digest": data_digest,
        "features": FEATURE_VERSION,
        "parameters": params,
        "seed": 42,
        "training_windows": len(origins_manifest),
        "training_rows": len(x),
        "target": "revenue_in/other_in/out in minor units normalized by past turnover",
    }
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:10]
    version = f"hgb-{trained_to}-{'residual' if calendar else 'full'}-{digest}"
    folder = registry.root / version
    folder.mkdir(parents=True, exist_ok=True)
    meta = {
        **recipe,
        "model_version": version,
        "max_target_date": max(r["target_end"] for r in origins_manifest),
        "max_origin": max(r["origin"] for r in origins_manifest),
    }
    joblib.dump(estimator, folder / "model.joblib")
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2))
    (folder / "training_origins.json").write_text(json.dumps(origins_manifest, indent=2))
    return meta


# ------------------------------------------------------------------ GBM остатка (X5, E04/E05)

GBM_CONFIGS = {
    "hgb_a": ("hgb", {"max_iter": 80, "max_leaf_nodes": 15, "learning_rate": 0.06,
                      "l2_regularization": 2.0, "min_samples_leaf": 40}),
    "hgb_b": ("hgb", {"max_iter": 200, "max_leaf_nodes": 31, "learning_rate": 0.04,
                      "l2_regularization": 5.0, "min_samples_leaf": 100}),
    "lgbm_a": ("lgbm", {"n_estimators": 150, "num_leaves": 15, "learning_rate": 0.05,
                        "min_child_samples": 100, "subsample": 0.8, "subsample_freq": 1,
                        "colsample_bytree": 0.8, "reg_lambda": 5.0}),
    "lgbm_b": ("lgbm", {"n_estimators": 300, "num_leaves": 31, "learning_rate": 0.03,
                        "min_child_samples": 200, "subsample": 0.8, "subsample_freq": 1,
                        "colsample_bytree": 0.6, "reg_lambda": 10.0}),
    "cat_a": ("catboost", {"iterations": 300, "depth": 6, "learning_rate": 0.08, "l2_leaf_reg": 5.0}),
    "cat_b": ("catboost", {"iterations": 500, "depth": 4, "learning_rate": 0.06, "l2_leaf_reg": 10.0}),
}  # fmt: skip


class GBMResidual:
    """Один регрессор на поток (revenue, other_in, out) для HGB, LightGBM или CatBoost.

    Колонка отрасли (целый код, -1 неизвестна) идёт как категория. seed 42, один поток вычислений.
    """

    def __init__(self, library: str, params: dict, cat_index: int | None = None, seed: int = 42):
        if library not in ("hgb", "lgbm", "catboost"):
            raise ValueError(f"Неизвестная библиотека GBM: {library}")
        self.library, self.params, self.cat_index, self.seed = (
            library,
            dict(params),
            cat_index,
            seed,
        )
        self.models: list = []

    def _x(self, x):
        x = np.asarray(x, dtype=float)
        if self.cat_index is None:
            return x
        x = x.copy()
        if self.library == "hgb":
            x[:, self.cat_index] = np.where(x[:, self.cat_index] < 0, np.nan, x[:, self.cat_index])
        elif self.library == "catboost":
            import pandas as pd

            frame = pd.DataFrame(x)
            frame[self.cat_index] = frame[self.cat_index].astype(int)
            return frame
        else:
            x[:, self.cat_index] = np.maximum(x[:, self.cat_index], 0)
        return x

    def fit(self, x, y):
        y = np.asarray(y, dtype=float)
        xs = self._x(x)
        cat = [] if self.cat_index is None else [self.cat_index]
        self.models = []
        for k in range(y.shape[1]):
            if self.library == "hgb":
                m = HistGradientBoostingRegressor(
                    **self.params, categorical_features=cat or None, random_state=self.seed
                )
                m.fit(xs, y[:, k])
            elif self.library == "lgbm":
                from lightgbm import LGBMRegressor

                m = LGBMRegressor(
                    **self.params, random_state=self.seed, n_jobs=1, verbose=-1,
                    deterministic=True, force_row_wise=True,
                )  # fmt: skip
                m.fit(xs, y[:, k], categorical_feature=cat or "auto")
            else:
                from catboost import CatBoostRegressor

                m = CatBoostRegressor(
                    **self.params, random_seed=self.seed, thread_count=1, verbose=False,
                    cat_features=cat or None, allow_writing_files=False,
                )  # fmt: skip
                m.fit(xs, y[:, k])
            self.models.append(m)
        return self

    def predict(self, x):
        xs = self._x(x)
        return np.column_stack([m.predict(xs) for m in self.models])
