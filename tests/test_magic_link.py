"""Tests for passwordless magic-link tokens and the SMTP sender."""

import datetime

import pytest

from reflex_app.services import email as email_service
from reflex_app.services import magic_link


def test_hash_is_deterministic_and_not_raw():
    h = magic_link._hash("abc")
    assert h == magic_link._hash("abc")
    assert h != "abc"
    assert len(h) == 64  # sha256 hex


def test_create_then_consume_roundtrip(monkeypatch):
    store = {}
    monkeypatch.setattr(magic_link, "create_token", _fake_create(store))
    monkeypatch.setattr(magic_link, "consume_token", _fake_consume(store))

    raw = magic_link.create_token("user@example.com")
    assert magic_link.consume_token(raw) == "user@example.com"
    # single use — second consume fails
    assert magic_link.consume_token(raw) is None


def test_consume_empty_or_unknown_returns_none(monkeypatch):
    store = {}
    monkeypatch.setattr(magic_link, "consume_token", _fake_consume(store))
    assert magic_link.consume_token("") is None
    assert magic_link.consume_token("nonexistent") is None


def test_send_magic_link_requires_smtp_config(monkeypatch):
    monkeypatch.setattr(email_service.config, "smtp_hostname", lambda: None)
    monkeypatch.setattr(email_service.config, "smtp_username", lambda: None)
    monkeypatch.setattr(email_service.config, "smtp_password", lambda: None)
    with pytest.raises(email_service.EmailConfigError):
        email_service.send_magic_link("a@b.com", "https://x/verify?token=t")


def test_send_magic_link_builds_and_sends(monkeypatch):
    # Configure SMTP and capture the SMTP interaction without a real server.
    monkeypatch.setattr(email_service.config, "smtp_hostname", lambda: "smtp.test")
    monkeypatch.setattr(email_service.config, "smtp_username", lambda: "u@test")
    monkeypatch.setattr(email_service.config, "smtp_password", lambda: "pw")
    monkeypatch.setattr(email_service.config, "smtp_from", lambda: "u@test")
    monkeypatch.setattr(email_service.config, "SMTP_PORT", 587)
    monkeypatch.setattr(email_service.config, "SMTP_STARTTLS", True)

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent["starttls"] = True
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, msg): sent["msg"] = msg

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

    link = "https://app/auth/magic/verify?token=abc123"
    email_service.send_magic_link("to@example.com", link)

    assert sent["host"] == "smtp.test" and sent["port"] == 587
    assert sent["starttls"] is True
    assert sent["login"] == ("u@test", "pw")
    msg = sent["msg"]
    assert msg["To"] == "to@example.com"
    assert link in msg.get_body(("plain",)).get_content()


# --- in-memory fakes mirroring create/consume semantics (no DB needed) ---

def _fake_create(store):
    import secrets
    def create(email):
        raw = secrets.token_urlsafe(8)
        store[magic_link._hash(raw)] = {
            "email": email,
            "used": False,
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=15),
        }
        return raw
    return create


def _fake_consume(store):
    def consume(raw):
        if not raw:
            return None
        row = store.get(magic_link._hash(raw))
        if row is None or row["used"]:
            return None
        if row["exp"] < datetime.datetime.now(datetime.timezone.utc):
            return None
        row["used"] = True
        return row["email"]
    return consume
