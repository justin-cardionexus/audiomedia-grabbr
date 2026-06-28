#!/usr/bin/env bash
#
# Deploy AudioMedia to Fly.io as 3 tiers: Fly Postgres (legacy) + backend + frontend.
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

# Fly-assigned URLs (always available, always have a valid cert).
FLY_BACKEND_URL="https://${BACKEND}.fly.dev"
FLY_FRONTEND_URL="https://${FRONTEND}.fly.dev"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# --- 0. Preflight ---------------------------------------------------------
FLY="$(command -v fly || command -v flyctl || true)"
[ -n "$FLY" ] || { echo "flyctl not found — install from https://fly.io/docs/flyctl/install/"; exit 1; }
"$FLY" auth whoami >/dev/null 2>&1 || { echo "Not logged in — run 'fly auth login'"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE (needs ANTHROPIC_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, optionally GOOGLE_*/SMTP_*/HF_TOKEN)"; exit 1; }

# Load .env early so FRONTEND_DOMAIN/BACKEND_DOMAIN (and secrets) are in scope.
# Preserve any command-line overrides of the domain knobs (shell env wins over
# .env), then source .env for everything else.
_cli_frontend_domain="${FRONTEND_DOMAIN:-}"
_cli_backend_domain="${BACKEND_DOMAIN:-}"
set -a; . "./$ENV_FILE"; set +a
[ -n "$_cli_frontend_domain" ] && FRONTEND_DOMAIN="$_cli_frontend_domain"
[ -n "$_cli_backend_domain" ] && BACKEND_DOMAIN="$_cli_backend_domain"

# --- Resolve public URLs --------------------------------------------------
# Custom domains are configured via FRONTEND_DOMAIN / BACKEND_DOMAIN (bare host
# or full URL). They CNAME to the fly.dev URLs and default to them when unset.
_norm() { local v="${1#http://}"; v="${v#https://}"; printf 'https://%s' "${v%%/}"; }
_host() { local v="${1#https://}"; v="${v#http://}"; printf '%s' "${v%%/*}"; }

PUBLIC_BACKEND_URL="$([ -n "${BACKEND_DOMAIN:-}" ] && _norm "$BACKEND_DOMAIN" || echo "$FLY_BACKEND_URL")"
PUBLIC_FRONTEND_URL="$([ -n "${FRONTEND_DOMAIN:-}" ] && _norm "$FRONTEND_DOMAIN" || echo "$FLY_FRONTEND_URL")"

# The browser SPA's data origin stays on fly.dev (always certed); the custom
# backend domain is used only for branded OAuth/magic-link browser navigations.
SPA_API_URL="$FLY_BACKEND_URL"

# CORS: allow the custom frontend origin AND the fly.dev frontend origin (so the
# app works loaded from either). Dedup when no custom domain is set.
if [ "$PUBLIC_FRONTEND_URL" = "$FLY_FRONTEND_URL" ]; then
  CORS_ORIGINS="$FLY_FRONTEND_URL"
else
  CORS_ORIGINS="$PUBLIC_FRONTEND_URL,$FLY_FRONTEND_URL"
fi

# Dry-run: print the resolved wiring and exit (no Fly calls).
if [ -n "${PRINT_ONLY:-}" ]; then
  echo "PREFIX=$PREFIX  REGION=$REGION"
  echo "FLY_FRONTEND_URL=$FLY_FRONTEND_URL"
  echo "FLY_BACKEND_URL=$FLY_BACKEND_URL"
  echo "PUBLIC_FRONTEND_URL=$PUBLIC_FRONTEND_URL"
  echo "PUBLIC_BACKEND_URL=$PUBLIC_BACKEND_URL"
  echo "SPA REFLEX_API_URL=$SPA_API_URL"
  echo "REFLEX_DEPLOY_URL=$PUBLIC_FRONTEND_URL"
  echo "REFLEX_CORS_ALLOWED_ORIGINS=$CORS_ORIGINS"
  echo "GOOGLE_REDIRECT_URI=$PUBLIC_BACKEND_URL/auth/google/callback"
  [ "$PUBLIC_FRONTEND_URL" != "$FLY_FRONTEND_URL" ] && echo "cert (frontend): $(_host "$PUBLIC_FRONTEND_URL")"
  [ "$PUBLIC_BACKEND_URL"  != "$FLY_BACKEND_URL"  ] && echo "cert (backend):  $(_host "$PUBLIC_BACKEND_URL")"
  exit 0
fi

# --- 1. Apps (create if missing) -----------------------------------------
app_exists() { "$FLY" apps list 2>/dev/null | awk '{print $1}' | grep -qx "$1"; }
for app in "$BACKEND" "$FRONTEND"; do
  if app_exists "$app"; then log "App $app already exists"; else
    log "Creating app $app"; "$FLY" apps create "$app" $ORG_ARG
  fi
done

# --- 2. Postgres (legacy Fly Postgres app) — create if missing + attach ---
# Legacy Fly Postgres is itself a Fly app, so reuse the app-existence check.
if app_exists "$DB_CLUSTER"; then
  log "Postgres '$DB_CLUSTER' already exists"
else
  log "Creating legacy Fly Postgres '$DB_CLUSTER' in $REGION"
  # Flags make it non-interactive; bump sizes for production workloads.
  "$FLY" postgres create --name "$DB_CLUSTER" --region "$REGION" $ORG_ARG \
    --initial-cluster-size 1 --vm-size shared-cpu-1x --volume-size 1
fi
log "Attaching Postgres to $BACKEND (injects DATABASE_URL secret)"
"$FLY" postgres attach "$DB_CLUSTER" --app "$BACKEND" --yes || \
  echo "   (attach reported an issue — it may already be attached; continuing)"

# --- 3. Shared services: object storage (Tigris) + Redis -----------------
# Media lives in object storage and state in Redis, so backend machines are
# stateless and can scale horizontally (no single-attach volume). The CLI
# surface for these varies by flyctl version — if a step needs attention,
# provision it from the Fly dashboard and set the secrets (see DEPLOY.md),
# then re-run. NOTE: these come from Fly provisioning, NOT from .env (your .env
# holds the LOCAL MinIO/Redis values, which must not reach production).
if "$FLY" secrets list --app "$BACKEND" 2>/dev/null | grep -q "BUCKET_NAME"; then
  log "Object storage already configured on $BACKEND"
else
  log "Provisioning Tigris object storage for $BACKEND (sets AWS_*/BUCKET_NAME secrets)"
  "$FLY" storage create --app "$BACKEND" --name "${PREFIX}-media" --yes 2>/dev/null || \
    echo "   (provision Tigris manually: 'fly storage create --app $BACKEND'; see DEPLOY.md)"
fi
if "$FLY" secrets list --app "$BACKEND" 2>/dev/null | grep -q "REDIS_URL"; then
  log "Redis already configured on $BACKEND"
else
  log "Redis not configured — provision Fly Redis and set REDIS_URL"
  echo "   Run: fly redis create   then: fly secrets set --app $BACKEND REDIS_URL=<connection-url>"
  echo "   (see DEPLOY.md — skipping automatic Redis provisioning)"
fi

# --- 4. Backend secrets from .env (already sourced above) -----------------
log "Staging backend secrets from $ENV_FILE"
SECRETS=()
for key in ANTHROPIC_API_KEY PEXELS_API_KEY PIXABAY_API_KEY \
           GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET HF_TOKEN \
           SMTP_HOSTNAME SMTP_USERNAME SMTP_PASSWORD SMTP_PORT SMTP_FROM SMTP_STARTTLS; do
  val="${!key:-}"
  [ -n "$val" ] && SECRETS+=("$key=$val")
done
if [ "${#SECRETS[@]}" -gt 0 ]; then
  "$FLY" secrets set --app "$BACKEND" --stage "${SECRETS[@]}"
else
  echo "   (no recognized secrets found in $ENV_FILE)"
fi

# --- 5. TLS certs for custom domains (skip when using fly.dev) ------------
# DNS (CNAME) is already set, so Fly auto-validates. Idempotent.
if [ "$PUBLIC_FRONTEND_URL" != "$FLY_FRONTEND_URL" ]; then
  log "Adding TLS cert for $(_host "$PUBLIC_FRONTEND_URL") on $FRONTEND"
  "$FLY" certs add "$(_host "$PUBLIC_FRONTEND_URL")" --app "$FRONTEND" || \
    echo "   (cert add reported an issue — it may already exist; continuing)"
fi
if [ "$PUBLIC_BACKEND_URL" != "$FLY_BACKEND_URL" ]; then
  log "Adding TLS cert for $(_host "$PUBLIC_BACKEND_URL") on $BACKEND"
  "$FLY" certs add "$(_host "$PUBLIC_BACKEND_URL")" --app "$BACKEND" || \
    echo "   (cert add reported an issue — it may already exist; continuing)"
fi

# --- 6. Deploy backend ----------------------------------------------------
# --env overrides keep URLs/CORS correct for custom domains (and any PREFIX).
# REFLEX_API_URL (the SPA's data origin) stays on the fly.dev backend; the
# custom backend domain is used for OAuth/magic-link browser navigations.
log "Deploying backend ($BACKEND)"
"$FLY" deploy --app "$BACKEND" -c fly.backend.toml \
  --env "REFLEX_API_URL=$SPA_API_URL" \
  --env "REFLEX_DEPLOY_URL=$PUBLIC_FRONTEND_URL" \
  --env "REFLEX_CORS_ALLOWED_ORIGINS=$CORS_ORIGINS" \
  --env "BACKEND_URL=$PUBLIC_BACKEND_URL" \
  --env "FRONTEND_URL=$PUBLIC_FRONTEND_URL" \
  --env "GOOGLE_REDIRECT_URI=$PUBLIC_BACKEND_URL/auth/google/callback"

# --- 7. Deploy frontend (bake the SPA's backend URL into the static build) -
log "Deploying frontend ($FRONTEND) → REFLEX_API_URL=$SPA_API_URL"
"$FLY" deploy --app "$FRONTEND" -c fly.frontend.toml \
  --build-arg "REFLEX_API_URL=$SPA_API_URL" \
  --build-arg "REFLEX_DEPLOY_URL=$PUBLIC_FRONTEND_URL"

# --- Done -----------------------------------------------------------------
log "Deployment complete"
echo "  Frontend: $PUBLIC_FRONTEND_URL"
echo "  Backend:  $PUBLIC_BACKEND_URL"
if [ "$PUBLIC_FRONTEND_URL" != "$FLY_FRONTEND_URL" ] || [ "$PUBLIC_BACKEND_URL" != "$FLY_BACKEND_URL" ]; then
  echo "  Custom domains: watch cert issuance with 'fly certs show <domain> --app <app>'."
fi
echo "  Google OAuth: add ${PUBLIC_BACKEND_URL}/auth/google/callback as an authorized redirect URI."
echo "  Seed a QA user: fly ssh console --app $BACKEND -C 'uv run --no-sync python scripts/create_qa_user.py'"
