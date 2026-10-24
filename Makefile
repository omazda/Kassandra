.PHONY: setup demo calibrate error-bank train evaluate evaluate-v3 run check
setup:
	uv sync --extra dev --frozen
demo:
	uv run cashflow bootstrap --dataset dataset/
calibrate:
	uv run cashflow build-calibration
error-bank:
	uv run cashflow build-error-bank
train:
	uv run cashflow train
evaluate:
	uv run cashflow evaluate
evaluate-v3:
	uv run cashflow evaluate-v3
run:
	uv run python scripts/dev.py
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -q
