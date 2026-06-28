"""Runtime settings sourced from the environment.

Keys are read lazily so the app can boot (and show friendly errors) even when
some are missing. See `.env.example` for the full list.
"""

from __future__ import annotations

import os

# --- LLM ---
ANTHROPIC_MODEL = "claude-opus-4-8"


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


# --- Media search ---
def pexels_api_key() -> str | None:
    return os.environ.get("PEXELS_API_KEY") or None


def pixabay_api_key() -> str | None:
    return os.environ.get("PIXABAY_API_KEY") or None


# --- Google OAuth ---
# Where the frontend and backend are reachable (used to build OAuth redirects).
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", f"{BACKEND_URL}/auth/google/callback"
)


def google_client_id() -> str | None:
    return os.environ.get("GOOGLE_CLIENT_ID") or None


def google_client_secret() -> str | None:
    return os.environ.get("GOOGLE_CLIENT_SECRET") or None


def google_enabled() -> bool:
    return bool(google_client_id() and google_client_secret())


# --- Passwordless email sign-in (SMTP) ---
def smtp_hostname() -> str | None:
    return os.environ.get("SMTP_HOSTNAME") or None


def smtp_username() -> str | None:
    return os.environ.get("SMTP_USERNAME") or None


def smtp_password() -> str | None:
    return os.environ.get("SMTP_PASSWORD") or None


def smtp_from() -> str:
    return os.environ.get("SMTP_FROM") or (smtp_username() or "")


def smtp_enabled() -> bool:
    return bool(smtp_hostname() and smtp_username() and smtp_password())


SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "true").lower() != "false"
# How long an emailed magic link stays valid.
MAGIC_LINK_TTL_MINUTES = int(os.environ.get("MAGIC_LINK_TTL_MINUTES", "15"))


# --- Object storage (S3-compatible: MinIO local, Tigris in prod) ---
# AWS-standard names so Fly's Tigris integration (`fly storage create`) works
# out of the box; the S3_* aliases are conveniences for non-Fly setups.
def s3_bucket() -> str | None:
    return os.environ.get("BUCKET_NAME") or os.environ.get("S3_BUCKET") or None


def s3_endpoint_url() -> str | None:
    return (
        os.environ.get("AWS_ENDPOINT_URL_S3")
        or os.environ.get("S3_ENDPOINT_URL")
        or None
    )


def s3_access_key() -> str | None:
    return os.environ.get("AWS_ACCESS_KEY_ID") or None


def s3_secret_key() -> str | None:
    return os.environ.get("AWS_SECRET_ACCESS_KEY") or None


S3_REGION = os.environ.get("AWS_REGION") or os.environ.get("S3_REGION") or "auto"
# How long presigned media URLs stay valid.
PRESIGN_TTL_SECONDS = int(os.environ.get("PRESIGN_TTL_SECONDS", "3600"))


def storage_enabled() -> bool:
    return bool(s3_bucket() and s3_access_key() and s3_secret_key())


# --- Transcription ---
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")

# --- Pipeline defaults ---
DEFAULT_SEGMENT_SECONDS = 15
MIN_SEGMENT_SECONDS = 5
MAX_SEGMENT_SECONDS = 60

# How many media items to fetch per segment, per kind.
MEDIA_PER_SEGMENT = 3

# Accepted upload extensions (also enforced by rx.upload accept= dict).
AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm"]

# Valid media-type selections offered in the UI.
MEDIA_TYPES = ["images", "video", "both"]
