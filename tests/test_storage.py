"""Pure tests for the storage module (no network / no boto3 client)."""

import pytest

from reflex_app import config
from reflex_app.services import storage


def test_content_type_by_extension():
    assert storage._content_type("a/b.mp4") == "video/mp4"
    assert storage._content_type("x.m4a") == "audio/mp4"
    assert storage._content_type("x.webm") == "audio/webm"
    assert storage._content_type("x.unknown") == "application/octet-stream"


def test_storage_enabled_requires_bucket_and_creds(monkeypatch):
    monkeypatch.setattr(config, "s3_bucket", lambda: None)
    monkeypatch.setattr(config, "s3_access_key", lambda: "k")
    monkeypatch.setattr(config, "s3_secret_key", lambda: "s")
    assert config.storage_enabled() is False

    monkeypatch.setattr(config, "s3_bucket", lambda: "b")
    assert config.storage_enabled() is True


def test_client_raises_without_config(monkeypatch):
    monkeypatch.setattr(config, "s3_bucket", lambda: None)
    monkeypatch.setattr(storage, "_client", None)
    with pytest.raises(storage.StorageConfigError):
        storage._get_client()


def test_bucket_env_aliases(monkeypatch):
    # BUCKET_NAME (Fly/Tigris) takes precedence; S3_BUCKET is the fallback alias.
    monkeypatch.setenv("BUCKET_NAME", "primary")
    monkeypatch.setenv("S3_BUCKET", "fallback")
    assert config.s3_bucket() == "primary"
    monkeypatch.delenv("BUCKET_NAME")
    assert config.s3_bucket() == "fallback"
