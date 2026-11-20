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

    # Embeddings (week 3). Served locally by Ollama: free, private, no API key.
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"  # multilingual (Arabic + English), 1024 dimensions
    embedding_batch_size: int = 32  # texts per request to the model

    # Chat LLM for the agent (week 2). Local via Ollama; swap for a hosted model later.
    llm_model: str = "qwen3:8b"
    agent_max_steps: int = 6  # max LLM turns per question: stops runaway tool loops (and cost)
    # LLM-as-judge for evaluations. Thinking on: a judge must reason before deciding.
    judge_model: str = "qwen3:8b"


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lru_cache makes this a cheap singleton)."""
    return Settings()
