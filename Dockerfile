FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 LOKY_MAX_CPU_COUNT=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.13.5
COPY pyproject.toml uv.lock ./
# Dependencies (~2 GB) first: this layer is rebuilt only when the lock file changes.
RUN uv sync --frozen --no-dev --no-install-project
# OCR of scanned invoices and contracts: tesseract with Russian, poppler for PDF pages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY src ./src
COPY apps ./apps
COPY experiments ./experiments
COPY docs ./docs
COPY scripts ./scripts
COPY examples ./examples
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000 8050
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
