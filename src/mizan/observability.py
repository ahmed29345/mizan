"""Tracing with Langfuse: record every agent run as a tree of steps, for debugging and audit.

A TRACE is one request (e.g. one /ask). Inside it, OBSERVATIONS form a tree:

    ask (agent)                                  <- our root span
      |- guardrail: input check
      |- LangGraph run                           <- recorded by the LangChain CallbackHandler
      |    |- retrieve -> search_regulations (tool)
      |    |- agent -> ChatOllama (generation: prompt, answer, tokens, latency)
      |- guardrail: output check

Design:
- OPTIONAL: no keys configured (tests, CI, Langfuse stopped) = tracing is a no-op. The app
  must never depend on the monitoring system being up.
- Built on OpenTelemetry (the industry standard for traces); Langfuse 5 uses it internally.
- Spans are sent in the BACKGROUND in batches, so tracing adds almost no latency. The flip
  side: short scripts must call flush() before exiting or the last traces are lost.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler

from mizan.config import get_settings

ObservationType = Literal["span", "agent", "chain", "tool", "guardrail"]


def tracing_enabled() -> bool:
    settings = get_settings()
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


@lru_cache
def _client() -> Langfuse:
    settings = get_settings()
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_host,
        environment=settings.environment,  # filter local / ci / production in the UI
    )


class Trace:
    """Handle for the current trace. All methods are safe no-ops when tracing is off."""

    def __init__(self, span: Any = None) -> None:
        self._span = span

    @property
    def trace_id(self) -> str | None:
        return self._span.trace_id if self._span else None

    @property
    def callbacks(self) -> list[BaseCallbackHandler]:
        """Pass in LangChain/LangGraph config={"callbacks": ...} to record every LLM/tool call.

        The handler attaches to whichever span is current, so graph steps nest under ours.
        """
        if not self._span:
            return []
        return [CallbackHandler(public_key=get_settings().langfuse_public_key)]

    def update(self, **fields: Any) -> None:
        if self._span:
            self._span.update(**fields)

    def record(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        input: Any = None,
        output: Any = None,
        warning: bool = False,
    ) -> None:
        """A finished child step, e.g. a guardrail check and its verdict."""
        if self._span:
            child = self._span.start_observation(
                name=name,
                as_type=as_type,
                input=input,
                output=output,
                level="WARNING" if warning else "DEFAULT",
            )
            child.end()


@contextmanager
def trace(
    name: str,
    *,
    as_type: ObservationType = "span",
    input: Any = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> Iterator[Trace]:
    """Start a trace (the root span). Everything inside the `with` block nests under it.

    session_id groups several traces in the UI, e.g. all steps of one review case
    (start, return, approve) even though they happen in different requests or days.
    """
    if not tracing_enabled():
        yield Trace()
        return
    # propagate_attributes FIRST, so the root span and every child get session/tags/metadata.
    with (
        propagate_attributes(trace_name=name, session_id=session_id, tags=tags, metadata=metadata),
        _client().start_as_current_observation(name=name, as_type=as_type, input=input) as span,
    ):
        yield Trace(span)


def score(trace_id: str | None, name: str, value: float, comment: str | None = None) -> None:
    """Attach a score to a trace, e.g. an evaluation result, so quality and traces line up."""
    if trace_id and tracing_enabled():
        _client().create_score(trace_id=trace_id, name=name, value=value, comment=comment)


def flush() -> None:
    """Send buffered traces now. Call at the end of short-lived scripts (CLI, eval runs)."""
    if tracing_enabled():
        _client().flush()
