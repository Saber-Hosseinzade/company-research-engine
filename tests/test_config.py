"""Offline tests: no AWS, no model calls, no database."""

import pytest

from app.config import Settings


def test_defaults_need_no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MODEL_PROVIDER", "MODEL_ID", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.model_provider == "bedrock"
    assert settings.aws_region == "eu-central-1"


def test_model_is_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("MODEL_ID", "some/model")
    settings = Settings(_env_file=None)
    assert (settings.model_provider, settings.model_id) == ("openrouter", "some/model")
