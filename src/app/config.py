"""Settings from environment variables. The model is configuration, not code.

Switching provider or model means changing MODEL_PROVIDER / MODEL_ID only.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")

    model_provider: Literal["bedrock", "openrouter"] = "bedrock"
    model_id: str = "eu.amazon.nova-2-lite-v1:0"
    aws_region: str = "eu-central-1"

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    database_url: str = "postgresql://app:app@localhost:5432/app"


def get_settings() -> Settings:
    return Settings()
