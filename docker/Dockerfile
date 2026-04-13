# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user
RUN groupadd --gid 1001 synapze && \
    useradd --uid 1001 --gid synapze --shell /bin/sh --create-home synapze

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /home/synapze/.local
COPY --chown=synapze:synapze app/ ./app/

ENV PATH=/home/synapze/.local/bin:$PATH
RUN playwright install chromium --with-deps 2>/dev/null || true

USER synapze

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Gunicorn + UvicornWorker:
# - Gunicorn handles process management, signals, worker respawning
# - UvicornWorker gives us async within each worker
# - --max-requests restarts workers to prevent memory leaks
CMD ["sh", "-c", \
     "gunicorn app.main:app \
      --bind 0.0.0.0:8000 \
      --workers ${UVICORN_WORKERS:-4} \
      --worker-class uvicorn.workers.UvicornWorker \
      --timeout 120 \
      --keep-alive ${UVICORN_TIMEOUT_KEEP_ALIVE:-75} \
      --max-requests ${UVICORN_LIMIT_MAX_REQUESTS:-10000} \
      --max-requests-jitter 500 \
      --backlog ${UVICORN_BACKLOG:-2048} \
      --access-logfile - \
      --error-logfile - \
      --log-level info \
      --forwarded-allow-ips '*'"]
