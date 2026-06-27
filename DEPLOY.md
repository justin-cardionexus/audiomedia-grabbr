# Deploying AudioMedia to Fly.io

This app deploys as **three tiers** on Fly.io:

| Tier | Fly app | What it is |
|---|---|---|
| **Database** | Fly Postgres `audiomedia-db` | Legacy Fly Postgres (a Fly app); injects `DATABASE_URL` into the backend. |
| **Web / backend** | `audiomedia-backend` | Reflex Python backend (`reflex run --backend-only`). Serves the websocket/event API, uploaded audio + rendered videos, and the Google OAuth routes. |
| **Presentation / frontend** | `audiomedia-frontend` | Static SPA from `reflex export --frontend-only`, served by nginx. |

```
browser ──https──▶ audiomedia-frontend (nginx, static SPA)
   │                     │ (backend URL baked in at build time)
   └──wss / https──▶ audiomedia-backend (Reflex) ──▶ Fly Postgres
                          │
                          └── /data volume: uploaded audio, rendered .mp4, Whisper model cache
```

The frontend is built with the backend's public URL baked in (`REFLEX_API_URL`),
so the SPA connects to `wss://audiomedia-backend.fly.dev/_event` and fetches
media from the backend origin.

---

## Prerequisites

- [`flyctl`](https://fly.io/docs/flyctl/install/) installed and logged in: `fly auth login`
- A local **`.env`** (copy from `.env.example`) containing at least:
  - `ANTHROPIC_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`
  - optional: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `HF_TOKEN`,
    `SMTP_HOSTNAME`/`SMTP_USERNAME`/`SMTP_PASSWORD` (passwordless email sign-in)
  - `DATABASE_URL` is **not** needed here — Fly injects it when Postgres is attached.
- Docker is **not** required locally; Fly builds the images remotely.

---

## One-command deploy

```bash
./deploy.sh
```

Defaults: prefix `audiomedia`, region `syd`, legacy Fly Postgres, a 10 GB volume.
Override via env vars:

```bash
PREFIX=myapp REGION=lhr VOLUME_SIZE=20 ORG=my-org ./deploy.sh
```

The script is **idempotent** — re-run it any time to redeploy.

### What `deploy.sh` does

1. Preflight: checks `flyctl`, login, and that `.env` exists.
2. Creates the two apps if missing (`audiomedia-backend`, `audiomedia-frontend`).
3. **Postgres**: creates a legacy Fly Postgres app `audiomedia-db` if missing
   (`fly postgres create`), then `fly postgres attach` → injects `DATABASE_URL`
   as a backend secret.
4. **Volume**: creates `audiomedia_data` (mounted at `/data`) on the backend.
5. **Secrets**: stages the keys from `.env` onto the backend
   (`fly secrets set --stage`).
6. **Backend deploy**: `fly deploy -c fly.backend.toml` (URLs/CORS passed via
   `--env` so they track `PREFIX`). On boot the container runs
   `reflex db migrate` then starts the backend.
7. **Frontend deploy**: `fly deploy -c fly.frontend.toml` with
   `--build-arg REFLEX_API_URL=https://audiomedia-backend.fly.dev` so the static
   build targets the live backend.
8. Prints both URLs.

---

## Custom domains (optional)

To serve the app on your own domains instead of the `*.fly.dev` URLs:

1. Create **CNAME** DNS records pointing your domains at the fly.dev URLs, e.g.
   `app.example.com → audiomedia-frontend.fly.dev` and
   `api.example.com → audiomedia-backend.fly.dev`.
2. Set them in `.env` (read by `deploy.sh`, not the app) — bare host or full URL:
   ```bash
   FRONTEND_DOMAIN=app.example.com
   BACKEND_DOMAIN=api.example.com
   ```
3. `./deploy.sh` — it then:
   - runs `fly certs add` for each custom domain (Fly auto-validates via your DNS);
   - sets `REFLEX_DEPLOY_URL`, app `FRONTEND_URL`/`BACKEND_URL`, and
     `GOOGLE_REDIRECT_URI` to the custom domains;
   - sets `REFLEX_CORS_ALLOWED_ORIGINS` to **both** the custom and fly.dev frontend
     origins (so the app works loaded from either).

**How traffic flows:** the browser SPA always connects to the **fly.dev backend**
(`REFLEX_API_URL`, baked at build — always has a valid cert, no race). The custom
**backend** domain is used for branded **OAuth redirects + magic-link URLs** only.
So a not-yet-issued custom cert can't break the live app's websocket.

Watch cert issuance: `fly certs show app.example.com --app audiomedia-frontend`.
Preview the resolved wiring without deploying: `PRINT_ONLY=1 ./deploy.sh`.

Leave `FRONTEND_DOMAIN`/`BACKEND_DOMAIN` unset to keep using the fly.dev URLs.

---

## First-time post-deploy steps

### Google sign-in (optional)
If you set `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, add this **Authorized
redirect URI** to your Google OAuth client (Google Cloud Console → Credentials):

```
https://audiomedia-backend.fly.dev/auth/google/callback
```

If you set a custom `BACKEND_DOMAIN`, use that host instead (e.g.
`https://api.example.com/auth/google/callback`). `deploy.sh` prints the exact
value to register. (Magic-link sign-in needs no provider config.)

### Seed a QA user (optional)
The database starts empty — register a user in the UI, or seed the QA account:

```bash
fly ssh console --app audiomedia-backend \
  -C "uv run --no-sync python scripts/create_qa_user.py"
# → qa_admin / QaTest!2026
```

---

## Verifying a deployment

```bash
fly logs --app audiomedia-backend     # expect: "Applying database migrations" → "App Running"
curl -s -o /dev/null -w '%{http_code}\n' https://audiomedia-backend.fly.dev/ping   # 200
open https://audiomedia-frontend.fly.dev
```

Then in the browser: register/login → upload a short clip → watch the stepper
(transcribe → analyze → search → compose) → the final video plays and downloads
(served from the backend origin — confirms volume + CORS + `api_url`).

---

## Redeploying after code changes

Just re-run `./deploy.sh`. Notes:

- **Schema changes**: create the migration locally first
  (`uv run reflex db makemigrations --message "…"`), commit it under `alembic/`,
  then deploy — the backend entrypoint applies pending migrations on boot.
- **Frontend-only change**: the frontend is rebuilt each deploy with the backend
  URL baked in; no manual step needed.
- **New secret**: add it to `.env` and re-run `./deploy.sh` (or
  `fly secrets set --app audiomedia-backend KEY=value`).

---

## Configuration reference

Set in `fly.backend.toml` `[env]` (overridden by `deploy.sh` per `PREFIX` and
custom domains). The values below are the fly.dev defaults:

| Variable | Value | Why |
|---|---|---|
| `REFLEX_API_URL` | `https://audiomedia-backend.fly.dev` | Backend origin the SPA + uploads use (**stays fly.dev** even with a custom backend domain) |
| `REFLEX_DEPLOY_URL` | custom or fly.dev frontend | Frontend public URL |
| `REFLEX_CORS_ALLOWED_ORIGINS` | custom + fly.dev frontend | Allows the SPA's websocket/API calls from either origin |
| `REFLEX_UPLOADED_FILES_DIR` | `/data/uploaded_files` | Uploaded audio + rendered video on the volume |
| `HF_HOME` | `/data/hf-cache` | Whisper model cache on the volume (downloads once) |
| `BACKEND_URL` / `FRONTEND_URL` / `GOOGLE_REDIRECT_URI` | custom or fly.dev URLs | OAuth + magic-link browser navigations |

Deploy-time only (read by `deploy.sh`, not the app): `FRONTEND_DOMAIN`,
`BACKEND_DOMAIN` — the custom domains (default to fly.dev). See **Custom domains**.

Secrets (never in toml): `ANTHROPIC_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`SMTP_HOSTNAME`/`SMTP_USERNAME`/`SMTP_PASSWORD` (+ optional `SMTP_PORT`/`SMTP_FROM`/`SMTP_STARTTLS`),
`DATABASE_URL` (auto via attach).

---

## Troubleshooting

- **`fly postgres create` prompts/flag errors**: the legacy Postgres CLI surface
  varies by flyctl version. If the non-interactive flags differ, run
  `fly postgres create --name audiomedia-db --region <region>` interactively, then
  `fly postgres attach audiomedia-db --app audiomedia-backend`. Either way the goal
  is a `DATABASE_URL` secret on the backend.
- **Postgres operations**: legacy Fly Postgres is a normal Fly app you operate —
  e.g. `fly postgres connect -a audiomedia-db`, and back it up yourself
  (it is not a managed/HA offering).
- **Backend OOM during transcription/render**: bump memory in `fly.backend.toml`
  `[[vm]] memory` (whisper + moviepy want headroom).
- **First transcription is slow**: the Whisper model downloads to `/data/hf-cache`
  on first use, then it's warm.
- **App name taken**: Fly names are global — re-run with `PREFIX=…`.
- **Media 404 after redeploy**: ensure the volume exists and is mounted at `/data`
  (`fly volumes list --app audiomedia-backend`).

---

## Scaling notes (current limits)

The backend runs as a **single machine** (`min_machines_running=1`, no autostop):
Reflex state is in-memory, the processing pipeline runs in-process, and the volume
is single-attach. To scale horizontally you'd need:

- **Redis** for shared session state (`REFLEX_REDIS_URL`), and
- **Object storage** (e.g. S3/Tigris) for uploads + rendered videos instead of a
  local volume, with `get_upload_url` pointed at it.

The frontend (static nginx) scales freely and is set to scale to zero when idle.
