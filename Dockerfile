# One image for every Railway service (api, worker). Start command differs per service:
#   api:    uvicorn engine.api:app --host 0.0.0.0 --port $PORT      (default CMD)
#   worker: python -m engine.worker_main
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_LINK_MODE=copy
RUN pip install uv==0.9.* 2>/dev/null || pip install uv
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Chromium and its system libraries for evidence capture
RUN uv run playwright install --with-deps chromium

COPY . .
RUN useradd -m haul16 && chown -R haul16 /app /home/haul16
USER haul16
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["sh", "-c", "uvicorn engine.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
