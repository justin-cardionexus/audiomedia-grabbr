# AudioMedia

Turn spoken audio into an illustrated video. Upload or record audio and the app
transcribes it, uses **Claude** to derive a visual search query for each segment,
fetches matching stock **images/video** (Pexels + Pixabay), and stitches it all
into a **final video synced to your original audio** — playable and downloadable
in the browser.

Built with [Reflex](https://reflex.dev) (full-stack Python).


Use it [here](https://audiomedia-frontend.fly.dev)

A default user has been setup for QA: qa_admin / QaTest!2026

Currently, sign in via Google only works for pre-approved users.

---

## How it works

```
audio ─▶ transcribe (faster-whisper) ─▶ per-segment search queries (Claude)
      ─▶ stock media search (Pexels/Pixabay) ─▶ compose final video (moviepy/ffmpeg)
```

> **Why a transcription step?** LLMs (Claude included) can't ingest raw audio —
> the Messages API takes text/images. So speech is transcribed first, then the
> transcript is segmented into *N*-second windows and each window becomes one
> concise visual search query.

The transcript is split into fixed-length windows (default 15s). For each window
Claude returns a short stock-media query; the app searches Pexels/Pixabay for the
chosen media type, then builds one MP4 where each window's media plays over its
slice of the timeline, with the original audio as the soundtrack.

---

## Features

- **Two input methods** — drag-and-drop file upload *or* in-browser microphone
  recording (record → preview → submit).
- **Configurable per project** — media type (**images / video / both**) and
  segment length (5–60s, default 15s).
- **Live pipeline progress** — a stage stepper (Transcribe → Analyze → Search →
  Compose → Complete) with a descriptive status line and a progress bar; reload-safe.
- **Final video** — composed MP4 synced to the audio, with a player and a
  **Download** button.
- **Per-user projects** — dashboard of your runs; each project persists its
  transcript, segments, matched media, and rendered video.
- **Authentication** — username/password ([`reflex-local-auth`](https://pypi.org/project/reflex-local-auth/)),
  an optional **"Sign in with Google"** OAuth flow, and optional **passwordless
  sign-in** via an emailed magic link (SMTP).
- **Delete errored runs** from the dashboard.
- **Tests** — a `pytest` suite for the core logic.
- **Deployable** — 3-tier Fly.io setup (see [DEPLOY.md](DEPLOY.md)).

---

## Tech stack

| Concern | Choice |
|---|---|
| Web framework | Reflex 0.9.x (Python frontend + backend) |
| Transcription | `faster-whisper` (local, on-device) |
| LLM | Claude `claude-opus-4-8` via the `anthropic` SDK |
| Media search | Pexels + Pixabay REST APIs (`httpx`) |
| Video compositing | `moviepy` (ffmpeg bundled via `imageio-ffmpeg`) |
| Auth | `reflex-local-auth` + custom Google OAuth |
| Database | SQLite (dev) / PostgreSQL (prod) via SQLModel + Alembic |
| Packaging | `uv` |

---

## Prerequisites

- **Python 3.12** (managed by [`uv`](https://docs.astral.sh/uv/)).
- **Node 22** — the repo pins it via `.nvmrc` (`nvm use`). Reflex needs ≥ 22.12.
- API keys (see Configuration): an **Anthropic** key, plus free **Pexels** and
  **Pixabay** keys. Google OAuth credentials are optional.

---

## Setup & run (local dev)

```bash
# 1. Node version (Reflex needs 22.12+)
nvm use                       # reads .nvmrc → Node 22.13.1

# 2. Python deps
uv sync                       # creates .venv from pyproject.toml + uv.lock

# 3. Configure secrets
cp .env.example .env          # then fill in your API keys (see below)

# 4. Database (SQLite by default; migrations are committed under alembic/)
uv run reflex db migrate

# 5. Run it
uv run reflex run
```

Then open **http://localhost:3000** (backend runs on `:8000`).

> First transcription downloads the Whisper model (~once); subsequent runs are warm.

### Seed a QA user (optional)

```bash
uv run python scripts/create_qa_user.py     # → qa_admin / QaTest!2026
```

Or just register a new account in the UI.

---

## Configuration

Copy `.env.example` → `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude — generates a search query per segment |
| `PEXELS_API_KEY` | ✅ | Stock media search ([pexels.com/api](https://www.pexels.com/api/)) |
| `PIXABAY_API_KEY` | ✅ | Stock media search ([pixabay.com/api/docs](https://pixabay.com/api/docs/)) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | Enables "Sign in with Google" |
| `SMTP_HOSTNAME` / `SMTP_USERNAME` / `SMTP_PASSWORD` | — | Enables passwordless magic-link sign-in (optional `SMTP_PORT`/`SMTP_FROM`/`SMTP_STARTTLS`) |
| `HF_TOKEN` | — | Silences the Hugging Face download notice / raises rate limits |
| `WHISPER_MODEL_SIZE` | — | `tiny`/`base`/`small`/`medium`/`large-v3` (default `base`) |
| `DATABASE_URL` | — | Postgres URL for production (defaults to local SQLite) |

`.env` is loaded automatically (via `python-dotenv` in `rxconfig.py`).

To enable Google sign-in, create an OAuth 2.0 **Web** client in Google Cloud
Console and add the redirect URI `http://localhost:8000/auth/google/callback`
(use your deployed backend URL in production).

---

## Using the app

1. **Log in / register** (or use the QA account).
2. **New upload** → choose **media type** and **segment length**, then either
   drop an audio file or **record** from your mic (record → preview → submit).
3. Watch the **pipeline stepper** advance: transcribe → analyze (Claude) →
   search media → compose → complete. (You can reload mid-run — progress persists.)
4. On the **project page**, play and **download** the final video; each segment
   below shows its transcript, the search query Claude generated, and the matched
   images/videos.
5. The **dashboard** lists all your projects; errored runs can be deleted.

Supported audio: `.mp3 .wav .m4a .aac .ogg .flac .webm` (recordings are `.webm`).

---

## Project structure

```
rxconfig.py                 # Reflex config (app name, DB URL, plugins)
reflex_app/
├── reflex_app.py           # app + page/route registration
├── config.py               # env-sourced settings
├── models.py               # SQLModel tables: AudioProject, Segment, MediaResult
├── schemas.py              # typed view-models for the UI
├── auth_routes.py          # Starlette routes for Google OAuth
├── services/               # transcription, llm, media_search, video_compose, google_oauth
├── state/                  # base, projects_state, pipeline_state (bg task), auth_state
├── pages/                  # index (dashboard), upload, project/[id], auth_pages
└── components/             # navbar, segment_card, pipeline_status, auth (require_login)
alembic/                    # DB migrations
tests/                      # pytest suite
scripts/                    # create_qa_user.py, screenshot_project.py
Dockerfile.* / fly.*.toml / deploy.sh / DEPLOY.md   # Fly.io deployment
```

---

## Testing

```bash
uv run pytest
```

Covers transcript windowing, media-search normalization + no-key fallback, the
LLM config guard, the video-compose timeline math, and pipeline status ordering.

---

## Deployment

A 3-tier Fly.io deployment (static frontend + Reflex backend + Postgres) is fully
scripted. See **[DEPLOY.md](DEPLOY.md)** — TL;DR:

```bash
fly auth login
./deploy.sh
```

Custom domains are supported via `FRONTEND_DOMAIN`/`BACKEND_DOMAIN` (CNAME'd to the
fly.dev URLs); `deploy.sh` provisions the TLS certs. See [DEPLOY.md](DEPLOY.md).

---

## Notes & limitations

- **Stock, not the open web** — media comes from Pexels/Pixabay libraries.
- **Compute** — transcription and video rendering are CPU-bound; long audio takes
  longer, and production needs a machine with adequate RAM (~2 GB).
- **Single-process state** — the pipeline runs in-process and app state is
  in-memory, so the backend is designed to run as a single instance (scaling out
  would need Redis + shared object storage — see DEPLOY.md).
