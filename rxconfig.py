import os

import reflex as rx
from dotenv import load_dotenv

# Load .env from the project root (if present) before anything reads os.environ.
# Reflex does not auto-load .env, so this is what makes ANTHROPIC_API_KEY,
# PEXELS_API_KEY, PIXABAY_API_KEY, HF_TOKEN, DATABASE_URL, etc. available to the
# app. override=False so real shell env vars still win.
load_dotenv(override=False)

# Persistence: SQLite for local dev, Postgres-ready for production.
# Set DATABASE_URL (e.g. postgres://user:pass@host:5432/db) to override. Fly's
# managed Postgres hands out `postgres://…`; SQLAlchemy needs an explicit driver,
# so normalize to the psycopg (v3) dialect.
def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///reflex.db")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DB_URL = _db_url()

# Distributed state: when a Redis URL is present, Reflex switches from in-memory
# to the Redis-backed state manager automatically, so multiple backend instances
# share state. Bridge Fly Redis's `REDIS_URL` to Reflex's expected setting.
REDIS_URL = os.environ.get("REFLEX_REDIS_URL") or os.environ.get("REDIS_URL") or None

config = rx.Config(
    app_name="reflex_app",
    db_url=DB_URL,
    # psycopg v3 is async-capable, so it serves both sync and async engines.
    async_db_url=DB_URL if DB_URL.startswith("postgresql") else None,
    redis_url=REDIS_URL,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(),
    ],
)
