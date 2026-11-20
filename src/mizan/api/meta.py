"""Read-only endpoints that support the web UI: system info and the latest evaluation."""

import json
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException, status

from mizan import __version__
from mizan.config import get_settings
from mizan.evals.dataset import load_golden
from mizan.observability import tracing_enabled

router = APIRouter(tags=["meta"])


def _langfuse_reachable(url: str) -> bool:
    """Quick check, so the UI never shows trace links to a Langfuse that isn't running."""
    try:
        with urllib.request.urlopen(f"{url}/api/public/health", timeout=1) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


@router.get("/meta")
def meta() -> dict[str, Any]:
    """What is running: shown in the UI header and useful when recording a demo."""
    settings = get_settings()
    return {
        "version": __version__,
        "environment": settings.environment,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        # Link target for "open trace" buttons; None when tracing is off.
        "langfuse_url": (
            settings.langfuse_host
            if tracing_enabled() and _langfuse_reachable(settings.langfuse_host)
            else None
        ),
    }


@router.get("/evals/latest", responses={404: {"description": "No evaluation run saved yet"}})
def latest_eval() -> dict[str, Any]:
    """The newest COMPLETE evaluation run (data/eval/results/*.json), trimmed for display.

    Partial runs (e.g. `--only attack` or `--retrieval-only`) are skipped, so the page always
    reflects the whole golden set rather than whichever small test ran last.
    """
    files = sorted((get_settings().data_dir / "eval" / "results").glob("*.json"), reverse=True)
    golden_size = len(load_golden())
    reports = (json.loads(f.read_text(encoding="utf-8")) for f in files)
    found = next(
        (
            (f, r)
            for f, r in zip(files, reports, strict=True)
            if len(r.get("answers", [])) >= golden_size
        ),
        None,
    )
    if found is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No complete evaluation run yet. Run: uv run python -m mizan.evals.run",
        )
    latest, report = found
    cases = []
    for item in report.get("answers", []):
        case, response = item["case"], item.get("response", {})
        cases.append(
            {
                "id": case["id"],
                "question": case["question"],
                "category": case["category"],
                "language": case["language"],
                "passed": item["score"]["passed"],
                "failures": item["score"].get("failures", []),
                "answer": response.get("answer"),
                "blocked_by": response.get("blocked_by"),
                "duration_ms": response.get("duration_ms"),
            }
        )
    return {
        "file": latest.name,
        "run_at": report.get("run_at"),
        "git_commit": report.get("git_commit"),
        "settings": report.get("settings", {}),
        "summary": report.get("summary", {}),
        "cases": cases,
    }
