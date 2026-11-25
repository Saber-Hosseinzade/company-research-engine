"""Offline tests: no AWS, no model calls, no database."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import config
from app.config import SecretError, Settings, get_settings


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


class FakeSecrets:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.calls: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803 (boto3 name)
        self.calls.append(SecretId)
        return {"SecretString": self.secret}


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[FakeSecrets]]:
    """Fake Secrets Manager; tests append the client get_settings() will use."""
    made: list[FakeSecrets] = []
    monkeypatch.setattr(config, "_secrets_client", lambda region: made[0])
    monkeypatch.setattr(config, "Settings", lambda: Settings(_env_file=None))
    config._secret_values.cache_clear()
    yield made
    config._secret_values.cache_clear()


def test_secret_overlays_keys_once(monkeypatch: pytest.MonkeyPatch, secrets: list[FakeSecrets]) -> None:
    secrets.append(
        FakeSecrets('{"OPENROUTER_API_KEY": "sk-secret", "SUPABASE_DATABASE_URL": "postgresql://s"}')
    )
    monkeypatch.setenv("APP_SECRET_ID", "app/secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    first, second = get_settings(), get_settings()
    assert first.openrouter_api_key == "sk-secret"
    assert first.supabase_database_url is not None
    assert first.supabase_database_url.get_secret_value() == "postgresql://s"
    assert "postgresql://s" not in repr(first)
    assert second.openrouter_api_key == "sk-secret"
    assert secrets[0].calls == ["app/secret"]  # read once per process


def test_no_secret_id_means_no_secrets_call(
    monkeypatch: pytest.MonkeyPatch, secrets: list[FakeSecrets]
) -> None:
    monkeypatch.delenv("APP_SECRET_ID", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert get_settings().openrouter_api_key == "from-env"
    assert secrets == []


@pytest.mark.parametrize("secret", ["not json sk-secret", '["sk-secret"]'])
def test_malformed_secret_fails_without_its_value(
    monkeypatch: pytest.MonkeyPatch, secrets: list[FakeSecrets], secret: str
) -> None:
    secrets.append(FakeSecrets(secret))
    monkeypatch.setenv("APP_SECRET_ID", "app/secret")
    with pytest.raises(SecretError) as caught:
        get_settings()
    assert "sk-secret" not in str(caught.value)


def test_env_example_lists_every_new_deployment_setting() -> None:
    # AgentCore spec R6: each new setting is listed in .env.example with an empty value (S2).
    lines = (Path(__file__).parents[1] / ".env.example").read_text().splitlines()
    for name in ("APP_SECRET_ID", "DB_CLUSTER_ARN", "DB_SECRET_ARN", "DB_NAME", "SUPABASE_DATABASE_URL"):
        assert f"{name}=" in lines, name
        assert name.lower() in Settings.model_fields
