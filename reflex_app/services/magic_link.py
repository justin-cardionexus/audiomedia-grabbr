"""Create and consume single-use magic-link tokens for passwordless sign-in.

Only the SHA-256 hash of each token is persisted; the raw token travels only in
the emailed URL. Tokens are single-use and expire after a short TTL.
"""

from __future__ import annotations

import datetime
import hashlib
import secrets

import reflex as rx
from sqlmodel import select

from .. import config
from ..models import MagicLinkToken


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_token(email: str) -> str:
    """Mint a token for `email`, persist its hash + expiry, return the raw token."""
    raw = secrets.token_urlsafe(32)
    expiration = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=config.MAGIC_LINK_TTL_MINUTES
    )
    with rx.session() as session:
        session.add(
            MagicLinkToken(  # type: ignore[call-arg]
                email=email,
                token_hash=_hash(raw),
                expiration=expiration,
            )
        )
        session.commit()
    return raw


def consume_token(raw: str) -> str | None:
    """Validate + consume a token. Returns the email on success, else None.

    Succeeds only for an unused, unexpired token; marks it used atomically.
    """
    if not raw:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    with rx.session() as session:
        row = session.exec(
            select(MagicLinkToken).where(MagicLinkToken.token_hash == _hash(raw))
        ).one_or_none()
        if row is None or row.used:
            return None
        # Stored timestamps may be naive (SQLite) — compare in UTC.
        exp = row.expiration
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        if exp < now:
            return None
        row.used = True
        session.add(row)
        session.commit()
        return row.email
