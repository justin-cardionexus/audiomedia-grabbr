"""Database models.

`reflex_local_auth` provides the `LocalUser` / `LocalAuthSession` tables; we
import it so its metadata is registered for migrations and FK targets resolve.
Our own tables are scoped to a user via `user_id -> localuser.id`.
"""

from __future__ import annotations

import datetime

import reflex as rx
import reflex_local_auth  # noqa: F401  (registers LocalUser/LocalAuthSession tables)
import sqlalchemy
import sqlmodel


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# Project status values (stored as plain strings for portability).
class Status:
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    SEARCHING = "searching"
    COMPOSING = "composing"
    COMPLETE = "complete"
    ERROR = "error"


class AudioProject(rx.Model, table=True):
    """One uploaded audio file and the pipeline run over it."""

    user_id: int = sqlmodel.Field(foreign_key="localuser.id", index=True)
    filename: str
    stored_path: str  # filename within the Reflex upload dir
    media_type: str = "both"  # "images" | "video" | "both"
    segment_seconds: int = 15
    status: str = Status.UPLOADED
    transcript: str | None = sqlmodel.Field(
        default=None, sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    )
    # Final rendered MP4 (media synced to the original audio), stored in upload dir.
    video_path: str | None = None
    error: str | None = None
    created_at: datetime.datetime = sqlmodel.Field(default_factory=_utcnow)


class Segment(rx.Model, table=True):
    """One time-window of the transcript and its derived search query."""

    project_id: int = sqlmodel.Field(foreign_key="audioproject.id", index=True)
    index: int
    start_sec: float
    end_sec: float
    text: str = sqlmodel.Field(
        default="", sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
    )
    search_query: str = ""


class MediaResult(rx.Model, table=True):
    """One image or video result attached to a segment."""

    segment_id: int = sqlmodel.Field(foreign_key="segment.id", index=True)
    kind: str  # "image" | "video"
    url: str
    thumbnail_url: str = ""
    source: str = ""  # "pexels" | "pixabay"
    width: int = 0
    height: int = 0
    attribution: str = ""


class MagicLinkToken(rx.Model, table=True):
    """A single-use, short-lived token backing a passwordless email sign-in.

    Only the SHA-256 hash of the token is stored; the raw token lives solely in
    the emailed link.
    """

    email: str = sqlmodel.Field(index=True)
    token_hash: str = sqlmodel.Field(index=True)
    expiration: datetime.datetime
    used: bool = False
    created_at: datetime.datetime = sqlmodel.Field(default_factory=_utcnow)
