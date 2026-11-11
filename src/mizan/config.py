"""Application settings, read from environment variables (and .env in local dev).

pydantic-settings maps env vars to typed fields: DATABASE_URL -> database_url.
A missing or wrongly-typed value fails at startup, not in the middle of a request.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://mizan:mizan@localhost:5432/mizan"
    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lru_cache makes this a cheap singleton)."""
    return Settings()
