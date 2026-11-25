"""Offline tests for the Supabase startup check (AgentCore spec R11); the connection is faked."""

import logging
from types import TracebackType
from typing import Any

import pytest

from app import config, supabase


class FakeConn:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: type[BaseException] | BaseException | TracebackType | None) -> None:
        pass

    def execute(self, sql: str) -> None:
        self.sql.append(sql)


def test_reachable_logs_seconds_and_uses_tls(caplog: pytest.LogCaptureFixture) -> None:
    conn = FakeConn()
    seen: dict[str, Any] = {}

    def connect(url: str, **kwargs: Any) -> FakeConn:
        seen.update(kwargs, url=url)
        return conn

    caplog.set_level(logging.INFO)
    assert supabase.check("postgresql://user:pw@db.example/x", connect) is True
    assert seen["sslmode"] == "require" and seen["connect_timeout"] == 10
    assert conn.sql == ["SELECT 1"]
    assert "supabase reachable seconds=" in caplog.text
    assert "pw" not in caplog.text


def test_unreachable_logs_the_error_type_only_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    def connect(url: str, **kwargs: Any) -> FakeConn:
        raise OSError("connection to db.example user=secretuser failed")

    assert supabase.check("postgresql://secretuser:pw@db.example/x", connect) is False
    assert "supabase unreachable error=OSError" in caplog.text
    assert "secretuser" not in caplog.text


def test_unreadable_secret_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> None:
        raise config.SecretError("Cannot read the app secret (ClientError).")

    monkeypatch.setattr(config, "get_settings", broken)
    supabase.check_configured()
    assert "supabase unreachable error=SecretError" in caplog.text
