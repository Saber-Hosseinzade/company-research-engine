"""Settings from environment variables. The model is configuration, not code.

Switching provider or model means changing MODEL_PROVIDER / MODEL_ID only.

On AgentCore, APP_SECRET_ID names one Secrets Manager secret (JSON) holding the keys in SECRET_KEYS; they
replace the environment values at startup (AgentCore spec R5). Secret values are never logged.
"""

import json
from functools import cache
from typing import Any, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings read from the app secret when APP_SECRET_ID is set.
SECRET_KEYS = ("OPENROUTER_API_KEY", "SUPABASE_DATABASE_URL")


class SecretError(RuntimeError):
    """The app secret cannot be read or is malformed. The message never carries a secret value."""


class Settings(BaseSettings):
    # env_ignore_empty: an empty value (as in .env.example) means "not set", so the default applies.
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore", env_ignore_empty=True)

    model_provider: Literal["bedrock", "openrouter"] = "bedrock"
    model_id: str = "eu.amazon.nova-2-lite-v1:0"
    aws_region: str = "eu-central-1"

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Embedding model on OpenRouter; changing it means re-running `make embed`.
    embedding_model_id: str | None = None

    database_url: str = "postgresql://app:app@localhost:5432/app"

    # SEC EDGAR requires a User-Agent naming the client and a contact email.
    sec_user_agent: str | None = None

    # AgentCore deployment (infra/). Unset locally.
    app_secret_id: str | None = None  # Secrets Manager secret with SECRET_KEYS
    db_cluster_arn: str | None = None  # set: the filings database is Aurora through the RDS Data API
    db_secret_arn: str | None = None  # the cluster's credential secret, for the Data API
    db_name: str = "app"
    supabase_database_url: SecretStr | None = None  # checked at startup only (R11)


def _secrets_client(region: str) -> Any:
    import boto3

    return boto3.client("secretsmanager", region_name=region)


@cache
def _secret_values(secret_id: str, region: str) -> dict[str, str]:
    """The SECRET_KEYS present in the secret, read once per process."""
    try:
        raw = _secrets_client(region).get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as exc:
        raise SecretError(f"Cannot read the app secret ({type(exc).__name__}).") from None
    try:
        values = json.loads(raw)
    except ValueError:
        raise SecretError("The app secret is not a JSON object.") from None
    if not isinstance(values, dict):
        raise SecretError("The app secret is not a JSON object.")
    return {k: str(values[k]) for k in SECRET_KEYS if values.get(k)}


def get_settings() -> Settings:
    settings = Settings()
    if settings.app_secret_id:
        values = _secret_values(settings.app_secret_id, settings.aws_region)
        settings = settings.model_copy(
            update={k.lower(): SecretStr(v) if k == "SUPABASE_DATABASE_URL" else v for k, v in values.items()}
        )
    return settings
