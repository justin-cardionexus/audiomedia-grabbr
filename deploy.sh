#!/usr/bin/env bash
#
# Deploy AudioMedia to Fly.io as 3 tiers: Managed Postgres + backend + frontend.
# Idempotent: safe to re-run for repeated deployments.
#
# Overridable via env: PREFIX, REGION, VOLUME_SIZE, ENV_FILE, ORG.
# Prereqs: `fly auth login`, and a local .env containing the API keys/secrets.
#
set -euo pipefail

PREFIX="${PREFIX:-audiomedia}"
REGION="${REGION:-syd}"
VOLUME_SIZE="${VOLUME_SIZE:-10}"
ENV_FILE="${ENV_FILE:-.env}"
ORG_ARG=""
[ -n "${ORG:-}" ] && ORG_ARG="--org ${ORG}"

BACKEND="${PREFIX}-backend"
FRONTEND="${PREFIX}-frontend"
DB_CLUSTER="${PREFIX}-db"
VOLUME_NAME="audiomedia_data"            # must match [[mounts]] source in fly.backend.toml
BACKEND_URL="https://${BACKEND}.fly.dev"
FRONTEND_URL="https://${FRONTEND}.fly.dev"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# --- 0. Preflight ---------------------------------------------------------
FLY="$(command -v fly || command -v flyctl || true)"
[ -n "$FLY" ] || { echo "flyctl not found — install from https://fly.io/docs/flyctl/install/"; exit 1; }
"$FLY" auth whoami >/dev/null 2>&1 || { echo "Not logged in — run 'fly auth login'"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE (needs ANTHROPIC_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, optionally GOOGLE_*/HF_TOKEN)"; exit 1; }

# --- 1. Apps (create if missing) -----------------------------------------
app_exists() { "$FLY" apps list 2>/dev/null | awk '{print $1}' | grep -qx "$1"; }
for app in "$BACKEND" "$FRONTEND"; do
  if app_exists "$app"; then log "App $app already exists"; else
    log "Creating app $app"; "$FLY" apps create "$app" $ORG_ARG
  fi
done

# --- 2. Managed Postgres (create if missing) + attach --------------------
if "$FLY" mpg list 2>/dev/null | grep -q "$DB_CLUSTER"; then
  log "Managed Postgres '$DB_CLUSTER' already exists"
else
  log "Creating Managed Postgres '$DB_CLUSTER' in $REGION"
  # NOTE: `fly mpg` flags can vary by flyctl version; adjust if this errors.
  "$FLY" mpg create --name "$DB_CLUSTER" --region "$REGION" $ORG_ARG
fi
log "Attaching Postgres to $BACKEND (injects DATABASE_URL secret)"
"$FLY" mpg attach "$DB_CLUSTER" --app "$BACKEND" || \
  echo "   (attach reported an issue — it may already be attached; continuing)"

# --- 3. Persistent volume (create if missing) ----------------------------
if "$FLY" volumes list --app "$BACKEND" 2>/dev/null | grep -q "$VOLUME_NAME"; then
  log "Volume '$VOLUME_NAME' already exists"
else
  log "Creating volume '$VOLUME_NAME' (${VOLUME_SIZE}GB) in $REGION"
  "$FLY" volumes create "$VOLUME_NAME" --app "$BACKEND" --region "$REGION" --size "$VOLUME_SIZE" -y
fi

# --- 4. Backend secrets from .env ----------------------------------------
log "Staging backend secrets from $ENV_FILE"
set -a; . "./$ENV_FILE"; set +a
SECRETS=()
for key in ANTHROPIC_API_KEY PEXELS_API_KEY PIXABAY_API_KEY \
           GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET HF_TOKEN; do
  val="${!key:-}"
  [ -n "$val" ] && SECRETS+=("$key=$val")
done
if [ "${#SECRETS[@]}" -gt 0 ]; then
  "$FLY" secrets set --app "$BACKEND" --stage "${SECRETS[@]}"
else
  echo "   (no recognized secrets found in $ENV_FILE)"
fi

# --- 5. Deploy backend ----------------------------------------------------
# --env overrides keep URLs/CORS correct even when PREFIX is customized.
log "Deploying backend ($BACKEND)"
"$FLY" deploy --app "$BACKEND" -c fly.backend.toml \
  --env "REFLEX_API_URL=$BACKEND_URL" \
  --env "REFLEX_DEPLOY_URL=$FRONTEND_URL" \
  --env "REFLEX_CORS_ALLOWED_ORIGINS=$FRONTEND_URL" \
  --env "BACKEND_URL=$BACKEND_URL" \
  --env "FRONTEND_URL=$FRONTEND_URL" \
  --env "GOOGLE_REDIRECT_URI=$BACKEND_URL/auth/google/callback"

# --- 6. Deploy frontend (bake backend URL into the static build) ---------
log "Deploying frontend ($FRONTEND) → REFLEX_API_URL=$BACKEND_URL"
"$FLY" deploy --app "$FRONTEND" -c fly.frontend.toml \
  --build-arg "REFLEX_API_URL=$BACKEND_URL" \
  --build-arg "REFLEX_DEPLOY_URL=$FRONTEND_URL"

# --- Done -----------------------------------------------------------------
log "Deployment complete"
echo "  Frontend: $FRONTEND_URL"
echo "  Backend:  $BACKEND_URL"
echo "  Google OAuth: add ${BACKEND_URL}/auth/google/callback as an authorized redirect URI."
echo "  Seed a QA user: fly ssh console --app $BACKEND -C 'uv run --no-sync python scripts/create_qa_user.py'"
