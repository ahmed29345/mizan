"""Which chat model the agent uses. One place to change it (local Ollama now, hosted later)."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from mizan.config import get_settings


@lru_cache
def get_chat_model() -> BaseChatModel:
    """FastAPI dependency; tests override it with a scripted fake model."""
    settings = get_settings()
    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_url,
        temperature=0,  # same question -> (nearly) same answer; we want facts, not creativity
        reasoning=False,  # qwen3 can "think out loud" first; off = faster answers for now
    )


ChatModelDep = Annotated[BaseChatModel, Depends(get_chat_model)]


@lru_cache
def get_judge_model() -> BaseChatModel:
    """The evaluation judge. Thinking ON: a fast yes/no without reasoning proved unreliable
    (it flagged near-verbatim quotes as unsupported). Slower is fine for offline evals."""
    settings = get_settings()
    return ChatOllama(
        model=settings.judge_model, base_url=settings.ollama_url, temperature=0, reasoning=True
    )
