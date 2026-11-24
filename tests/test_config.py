"""Offline tests: no AWS, no model calls, no database."""

from pathlib import Path

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


def test_empty_values_in_env_file_mean_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # .env.example lists every setting with an empty value (S2); a copy of it must still load.
    for name in ("MODEL_PROVIDER", "MODEL_ID", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env.local"
    env_file.write_text("MODEL_PROVIDER=\nMODEL_ID=\nDATABASE_URL=\n")
    settings = Settings(_env_file=env_file)
    defaults = Settings(_env_file=None)
    assert settings.model_provider == defaults.model_provider
    assert settings.model_id == defaults.model_id
    assert settings.database_url == defaults.database_url
