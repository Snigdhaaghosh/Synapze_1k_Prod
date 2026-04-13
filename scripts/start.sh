#!/bin/bash
set -euo pipefail

echo "🚀 Synapze Enterprise — startup"

# Check required env vars
REQUIRED_VARS=(JWT_SECRET ANTHROPIC_API_KEY DATABASE_URL REDIS_URL GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET)
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var:-}" ]; then
    echo "❌ Missing required env var: $var"
    exit 1
  fi
done

# Wait for Postgres
echo "⏳ Waiting for Postgres..."
until pg_isready -d "$DATABASE_URL" -q; do sleep 1; done
echo "✅ Postgres ready"

# Wait for Redis
echo "⏳ Waiting for Redis..."
until redis-cli -u "$REDIS_URL" ping | grep -q PONG; do sleep 1; done
echo "✅ Redis ready"

echo "🚀 Starting API..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${UVICORN_WORKERS:-4}" \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout-keep-alive 75 \
  --access-log \
  --no-use-colors
