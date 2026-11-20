"""Run the evaluation against the dev database (seeded + indexed) with the real models.

  uv run python -m mizan.evals.run                  # retrieval + agent answers
  uv run python -m mizan.evals.run --retrieval-only # fast: search quality only, no LLM
  uv run python -m mizan.evals.run --judge          # also LLM-as-judge groundedness check
  uv run python -m mizan.evals.run --only pdpl      # only cases whose id starts with "pdpl"

Results are printed and saved to data/eval/results/<timestamp>.json, together with the
settings and git commit, so two runs can be compared ("did my prompt change help?").
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mizan.agent.graph import AgentStepLimitError, answer_question
from mizan.agent.llm import get_chat_model, get_judge_model
from mizan.agent.prompts import SYSTEM_PROMPT
from mizan.agent.tools import SEARCH_K
from mizan.config import get_settings
from mizan.db import SessionLocal, engine
from mizan.evals.dataset import GOLDEN_FILE, GoldenCase, load_golden
from mizan.evals.judge import judge_groundedness
from mizan.evals.metrics import AnswerScore, RetrievalScore, score_answer, score_retrieval
from mizan.models import Chunk
from mizan.rag.embeddings import Embedder, get_embedder
from mizan.services.regulations import RegulationNotFoundError, get_regulation
from mizan.services.search import search_chunks

RESULTS_DIR = GOLDEN_FILE.parent / "results"


async def retrieve_articles(
    session: AsyncSession, embedder: Embedder, question: str
) -> list[tuple[str, str]]:
    """Distinct articles, in rank order, from the same search the agent's first step runs."""
    hits = await search_chunks(session, embedder, question, k=SEARCH_K)
    ranked: list[tuple[str, str]] = []
    for hit in hits:
        key = (hit.regulation_code, hit.article_number)
        if key not in ranked:  # EN and AR chunks of one article count once
            ranked.append(key)
    return ranked


async def cited_sources(session: AsyncSession, citations: list[tuple[str, str]]) -> str:
    """Full text (both languages) of every cited article, for the judge."""
    parts = []
    for code, number in citations:
        try:
            regulation = await get_regulation(session, code)
        except RegulationNotFoundError:
            continue
        for article in regulation.articles:
            if article.article_number == number:
                parts.append(f"[{code} Art. {number}] ({article.language.value})\n{article.body}")
    return "\n\n".join(parts) or "(no sources)"


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def rate(values: list[bool]) -> str:
    return (
        f"{sum(values)}/{len(values)} ({100 * sum(values) / len(values):.0f}%)" if values else "-"
    )


async def run(cases: list[GoldenCase], *, retrieval_only: bool, use_judge: bool) -> dict[str, Any]:
    settings = get_settings()
    embedder = get_embedder()
    llm = get_chat_model()
    retrieval: list[RetrievalScore] = []
    answers: list[dict[str, Any]] = []

    async with SessionLocal() as session:
        indexed = await session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.embedding_model == embedder.model_name)
        )
        if not indexed:
            raise SystemExit(
                "No indexed chunks. Run: uv run python -m mizan.seed, "
                "then uv run python -m mizan.index"
            )

        print(f"\nRetrieval (top {SEARCH_K} chunks, {embedder.model_name})")
        for case in cases:
            if not case.answerable:
                continue
            score = score_retrieval(case, await retrieve_articles(session, embedder, case.question))
            retrieval.append(score)
            ranks = ", ".join(f"{k} -> {v or 'MISSED'}" for k, v in score.ranks.items())
            print(f"  {'ok  ' if score.recall == 1 else 'MISS'} {case.id:<10} {ranks}")

        if not retrieval_only:
            print(f"\nAgent answers ({settings.llm_model})")
            for case in cases:
                record: dict[str, Any] = {"case": case.model_dump(mode="json")}
                try:
                    response = await answer_question(
                        case.question,
                        session=session,
                        embedder=embedder,
                        llm=llm,
                        max_steps=settings.agent_max_steps,
                    )
                except AgentStepLimitError:
                    print(f"  FAIL {case.id:<10} step limit reached")
                    record["score"] = {"passed": False, "failures": ["step limit reached"]}
                    answers.append(record)
                    continue

                score: AnswerScore = score_answer(case, response)
                record |= {
                    "response": response.model_dump(mode="json"),
                    "score": score.model_dump(),
                }

                verdict_text = ""
                if use_judge and response.citations:
                    verdict = await judge_groundedness(
                        get_judge_model(),
                        question=case.question,
                        answer=response.answer,
                        sources=await cited_sources(
                            session,
                            [(c.regulation_code, c.article_number) for c in response.citations],
                        ),
                    )
                    record["judge"] = verdict.model_dump()
                    verdict_text = (
                        "  judge: grounded"
                        if verdict.grounded
                        else (f"  judge: UNSUPPORTED {verdict.unsupported_claims}")
                    )

                status = "pass" if score.passed else "FAIL"
                reasons = "; ".join(score.failures)
                seconds = response.duration_ms / 1000
                print(f"  {status} {case.id:<10} {seconds:>5.1f}s  {reasons}{verdict_text}")
                answers.append(record)

    await engine.dispose()
    return summarise(cases, retrieval, answers, settings_snapshot=settings.model_dump())


def summarise(
    cases: list[GoldenCase],
    retrieval: list[RetrievalScore],
    answers: list[dict[str, Any]],
    settings_snapshot: dict[str, Any],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "retrieval_recall": round(statistics.mean(s.recall for s in retrieval), 3)
        if retrieval
        else None,
        "retrieval_mrr": round(statistics.mean(s.reciprocal_rank for s in retrieval), 3)
        if retrieval
        else None,
    }
    print("\nSummary")
    if retrieval:
        print(f"  retrieval recall@{SEARCH_K}:  {summary['retrieval_recall']:.0%}")
        print(
            f"  retrieval MRR:        {summary['retrieval_mrr']:.2f}  (1.00 = always ranked first)"
        )

    if answers:
        by_case = {case.id: case for case in cases}
        passed = [a["score"]["passed"] for a in answers]
        groups: dict[str, list[bool]] = defaultdict(list)
        for a in answers:
            case = by_case[a["case"]["id"]]
            groups[f"category={case.category}"].append(a["score"]["passed"])
            groups[f"language={case.language.value}"].append(a["score"]["passed"])
        durations = [a["response"]["duration_ms"] for a in answers if "response" in a]
        hallucinated = sum(len(a["score"].get("hallucinated_citations", [])) for a in answers)
        judged = [a["judge"]["grounded"] for a in answers if "judge" in a]

        summary |= {
            "answer_pass_rate": round(sum(passed) / len(passed), 3),
            "groups": {k: round(sum(v) / len(v), 3) for k, v in sorted(groups.items())},
            "hallucinated_citations": hallucinated,
            "median_latency_ms": statistics.median(durations) if durations else None,
            "judge_grounded_rate": round(sum(judged) / len(judged), 3) if judged else None,
        }
        print(f"  answers passed:       {rate(passed)}")
        for name, values in sorted(groups.items()):
            print(f"    {name:<24} {rate(values)}")
        print(f"  hallucinated cites:   {hallucinated}")
        if durations:
            print(f"  median latency:       {statistics.median(durations) / 1000:.1f}s")
        if judged:
            print(f"  judge: grounded       {rate(judged)}")

    return {
        "run_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit(),
        # A fingerprint of the prompt: results are only comparable for the same prompt.
        "system_prompt_sha": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12],
        "settings": {
            k: settings_snapshot[k]
            for k in ("llm_model", "embedding_model", "agent_max_steps", "judge_model")
        },
        "summary": summary,
        "retrieval": [s.model_dump() for s in retrieval],
        "answers": answers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Mizan against the golden set")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--judge", action="store_true", help="add LLM-as-judge groundedness")
    parser.add_argument("--only", default="", help="run only case ids with this prefix")
    args = parser.parse_args()

    cases = [c for c in load_golden() if c.id.startswith(args.only)]
    report = asyncio.run(run(cases, retrieval_only=args.retrieval_only, use_judge=args.judge))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")


if __name__ == "__main__":
    main()
