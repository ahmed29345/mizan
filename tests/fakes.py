"""Test doubles: stand-ins for slow, costly or external services."""

import math
import re
import zlib
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field, field_validator

from mizan.models import EMBEDDING_DIMENSIONS
from mizan.rag.embeddings import EmbeddingError


class FakeEmbedder:
    """Deterministic "bag of words" embedder: each word adds 1 to a fixed slot of the vector.

    Texts sharing words point in similar directions, so ranking behaves sensibly. It is fast,
    free, needs no Ollama (CI has none), and returns the same numbers on every run.
    It does NOT understand meaning or cross-language matches; that's what bge-m3 is for.
    """

    model_name = "fake-bag-of-words"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word.encode()) % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:  # empty text: avoid a zero vector (cosine of zero vector is undefined)
            vector[0], norm = 1.0, 1.0
        return [x / norm for x in vector]


class BrokenEmbedder:
    """Simulates Ollama being down."""

    model_name = "broken"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("embedding service down")


class ScriptedChatModel(BaseChatModel):
    """A fake LLM that replies with pre-written messages, in order.

    Real LLM output varies run to run and needs Ollama, so it can't be asserted exactly.
    This fake makes the agent's loop testable: we decide what the "model" says (including
    tool calls) and check what our code does with it. It also records what it was sent.
    """

    replies: list[AIMessage]
    received: list[list[BaseMessage]] = Field(default_factory=list)
    model: str = "scripted-fake"

    @field_validator("replies", mode="before")
    @classmethod
    def _wrap_text(cls, replies: list[AIMessage | str]) -> list[AIMessage]:
        # Lets tests write a plain string for a final text answer.
        return [AIMessage(content=r) if isinstance(r, str) else r for r in replies]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        return self  # the script already knows which tools it will "call"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        reply = self.replies[min(len(self.received), len(self.replies)) - 1]
        # A fresh copy with no id, like a real model's new message. Reusing one object would
        # keep its id, and LangGraph treats a repeated id as an UPDATE, not a new message.
        fresh = reply.model_copy(deep=True, update={"id": None})
        return ChatResult(generations=[ChatGeneration(message=fresh)])


def tool_call(name: str, call_id: str = "call-1", **args: Any) -> AIMessage:
    """An AIMessage in which the 'model' asks to run one tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class FakeStructuredModel:
    """Fake LLM for multi-agent tests: answers by the agent's ROLE, not by call order.

    The three checkers run in PARALLEL, so the order of calls is not fixed; routing by the
    "ROLE: ..." first line of the system prompt makes each agent's answer deterministic.
    `outputs[role]` is a list used in order for repeated calls (e.g. the writer's revision).
    """

    def __init__(self, outputs: dict[str, list[BaseModel]]) -> None:
        self.outputs = outputs
        self.calls: dict[str, list[list[BaseMessage]]] = defaultdict(list)

    def with_structured_output(self, schema: type[BaseModel]) -> RunnableLambda:
        async def respond(messages: list[BaseMessage]) -> BaseModel:
            role = str(messages[0].content).split("\n", 1)[0].removeprefix("ROLE: ")
            self.calls[role].append(messages)
            replies = self.outputs[role]
            reply = replies[min(len(self.calls[role]), len(replies)) - 1]
            assert isinstance(reply, schema), f"{role}: expected {schema.__name__}"
            return reply

        return RunnableLambda(respond)
