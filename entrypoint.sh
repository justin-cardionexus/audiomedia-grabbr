#!/usr/bin/env sh
# Backend container entrypoint: ensure storage dirs, run migrations, start backend.
set -e

mkdir -p "${REFLEX_UPLOADED_FILES_DIR:-/data/uploaded_files}" "${HF_HOME:-/data/hf-cache}"

echo "==> Applying database migrations"
uv run --no-sync reflex db migrate

echo "==> Starting Reflex backend (prod, backend-only) on 0.0.0.0:8000"
exec uv run --no-sync reflex run --env prod --backend-only \
    --backend-host 0.0.0.0 --backend-port 8000
