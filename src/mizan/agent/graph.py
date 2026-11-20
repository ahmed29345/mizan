"""The agent loop, built with LangGraph.

        START
          |
          v
     +----------+   always search the question first (guaranteed by code, not the prompt)
     | retrieve |
     +----------+
          |
          v
      +-------+   tool calls?  yes   +-------+
      | agent | -------------------> | tools |
      +-------+ <------------------- +-------+
          |         tool results
          | no (final answer)
          v
         END

- State: the list of messages so far (question, LLM replies, tool results).
- retrieve node: runs search_regulations on the question BEFORE the LLM is involved, so the
  model can never skip retrieval and answer from its own (outside) knowledge.
- agent node: send the messages to the LLM; it replies with text OR with tool calls.
- tools node: run the requested tools, append their results as ToolMessages.
- The loop repeats until the LLM answers without tool calls, or the step limit is hit.
"""

import logging
import re
import time

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.agent.guardrails import (
    check_input,
    leaks_system_prompt,
    refusal,
    ungrounded_numbers,
)
from mizan.agent.prompts import SYSTEM_PROMPT
from mizan.agent.tools import RetrievalLog, build_tools
from mizan.observability import Trace, trace
from mizan.rag.embeddings import Embedder
from mizan.schemas.ask import AskResponse, Citation, ToolCallLog

# Matches citations like [DEMO-SAMA-CSF Art. 3.2] or [DEMO-PDPL Art 29]
_CITATION = re.compile(r"\[([A-Z0-9][A-Z0-9-]*[A-Z0-9])\s+Art\.?\s*([0-9A-Za-z.\-]+?)\]")
# Some models wrap private reasoning in <think>...</think>; never show it to users.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


logger = logging.getLogger(__name__)


class AgentStepLimitError(Exception):
    """The agent kept calling tools without reaching an answer."""


def build_graph(llm: BaseChatModel, tools: list[BaseTool]) -> CompiledStateGraph:
    # bind_tools: tell the model which tools exist (names, docstrings, argument schemas).
    llm_with_tools = llm.bind_tools(tools)

    async def agent(state: MessagesState) -> dict[str, list[AIMessage]]:
        # The system prompt is added on every call but never stored in the state.
        reply = await llm_with_tools.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}  # MessagesState APPENDS this to the list

    search_tool = next(t for t in tools if t.name == "search_regulations")

    async def retrieve(state: MessagesState) -> dict[str, list[AIMessage | ToolMessage]]:
        question = str(state["messages"][-1].content)
        call_id = "initial-search"
        result = await search_tool.ainvoke({"query": question})
        # Recorded as if the model had called the tool: chat APIs expect every tool result
        # to follow a tool call, and the audit log shows this search like any other.
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": search_tool.name, "args": {"query": question}, "id": call_id}
                    ],
                ),
                ToolMessage(content=result, tool_call_id=call_id),
            ]
        }

    graph = StateGraph(MessagesState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))  # runs whatever tools the last reply asked for
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "agent")
    # tools_condition: last reply has tool calls -> "tools", otherwise -> END.
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")  # after tools, always go back to the LLM
    return graph.compile()


def extract_citations(answer: str, retrieved: set[tuple[str, str]]) -> list[Citation]:
    seen: dict[tuple[str, str], Citation] = {}
    for code, number in _CITATION.findall(answer):
        key = (code, number.rstrip("."))
        if key not in seen:
            seen[key] = Citation(
                regulation_code=key[0], article_number=key[1], verified=key in retrieved
            )
    return list(seen.values())


def check_output(
    answer: str, citations: list[Citation], log: RetrievalLog, question: str
) -> str | None:
    """Run the output guardrails. Returns the reason the answer must be blocked, or None."""
    if leaks_system_prompt(answer):
        return "output:system_prompt_leak"
    if any(not citation.verified for citation in citations):
        return "output:unverified_citation"
    unsupported = ungrounded_numbers(answer, sources=log.texts, question=question)
    if unsupported:
        logger.warning("Blocked numbers not found in sources: %s", sorted(unsupported))
        return "output:ungrounded_numbers"
    return None


async def answer_question(
    question: str,
    *,
    session: AsyncSession,
    embedder: Embedder,
    llm: BaseChatModel,
    max_steps: int,
    tags: list[str] | None = None,
) -> AskResponse:
    """Answer one question. Every run is traced (when tracing is configured)."""
    with trace("ask", as_type="agent", input=question, tags=["ask", *(tags or [])]) as run:
        result = await _answer(question, session, embedder, llm, max_steps, run)
        run.update(
            output=result.answer,
            metadata={"blocked_by": result.blocked_by, "llm_turns": result.llm_turns},
            level="WARNING" if result.blocked_by else "DEFAULT",
        )
        return result.model_copy(update={"trace_id": run.trace_id})


async def _answer(
    question: str,
    session: AsyncSession,
    embedder: Embedder,
    llm: BaseChatModel,
    max_steps: int,
    run: Trace,
) -> AskResponse:
    started = time.perf_counter()
    model_name = getattr(llm, "model", type(llm).__name__)

    def elapsed_ms() -> int:
        return round((time.perf_counter() - started) * 1000)

    # Guardrail 1, BEFORE the LLM: an obvious injection never reaches the model at all.
    reason = check_input(question)
    run.record(
        "guardrail: input",
        as_type="guardrail",
        input=question,
        output=reason or "passed",
        warning=bool(reason),
    )
    if reason:
        logger.warning("Blocked input (%s): %r", reason, question[:200])
        blocked_by = f"input:{reason}"
        return AskResponse(
            question=question,
            answer=refusal(blocked_by, question),
            citations=[],
            tool_calls=[],
            blocked_by=blocked_by,
            model=model_name,
            llm_turns=0,
            duration_ms=elapsed_ms(),
        )

    log = RetrievalLog()
    graph = build_graph(llm, build_tools(session, embedder, log))
    try:
        # Graph steps for N LLM turns: retrieve (1) + N agent turns + (N - 1) tool rounds
        # between them = 2N. So a limit of 2 * max_steps allows exactly max_steps LLM turns.
        final = await graph.ainvoke(
            {"messages": [HumanMessage(question)]},
            # callbacks: Langfuse records every node, tool call and LLM call of this run.
            config={"recursion_limit": max_steps * 2, "callbacks": run.callbacks},
        )
    except GraphRecursionError as error:
        raise AgentStepLimitError(f"No answer within {max_steps} LLM turns") from error

    ai_messages = [m for m in final["messages"] if isinstance(m, AIMessage)]
    answer = _THINK.sub("", str(ai_messages[-1].content)).strip()
    citations = extract_citations(answer, log.articles)

    # Guardrails 2 + 3, AFTER the LLM: the model's answer must pass before a user sees it.
    blocked_by = check_output(answer, citations, log, question)
    # The trace keeps the ORIGINAL answer even when blocked: reviewers need to see what
    # the model tried to say. (Access to traces must be restricted like any audit log.)
    run.record(
        "guardrail: output",
        as_type="guardrail",
        input=answer,
        output=blocked_by or "passed",
        warning=bool(blocked_by),
    )
    if blocked_by:
        # Fail closed. The original answer goes to the server log (for review), not the user.
        logger.warning("Blocked answer (%s): %r", blocked_by, answer[:500])
        answer, citations = refusal(blocked_by, question), []

    return AskResponse(
        question=question,
        answer=answer,
        citations=citations,
        tool_calls=[
            ToolCallLog(tool=call["name"], args=call["args"])
            for message in ai_messages
            for call in message.tool_calls
        ],
        blocked_by=blocked_by,
        model=model_name,
        llm_turns=len(ai_messages) - 1,  # minus the synthetic message from `retrieve`
        duration_ms=elapsed_ms(),
    )
