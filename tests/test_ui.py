"""The web UI is served, and the endpoints it relies on behave."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mizan.config import get_settings
from mizan.evals.dataset import load_golden


def test_root_redirects_to_the_ui(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_ui_files_are_served(client: TestClient) -> None:
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "Mizan" in page.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/ui/styles.css").status_code == 200


def test_meta_describes_the_running_system(client: TestClient) -> None:
    body = client.get("/meta").json()
    assert {"version", "llm_model", "embedding_model", "environment"} <= body.keys()
    assert body["langfuse_url"] is None  # tracing is off in tests


def test_case_templates_include_expected_findings(client: TestClient) -> None:
    templates = client.get("/cases/templates").json()
    demo = next(t for t in templates if t["id"] == "demo-wallet")
    assert len(demo["documents"]) == 4
    assert demo["expected"]["DEMO-SAMA-CSF 3.2"].startswith("non_compliant")


@pytest.mark.anyio
async def test_case_list_shows_risk(api: httpx.AsyncClient) -> None:
    created = await api.post(
        "/cases", json={"title": "List me", "documents": [{"name": "d", "text": "t"}]}
    )
    cases = (await api.get("/cases")).json()
    row = next(c for c in cases if c["id"] == created.json()["id"])
    assert row["status"] == "draft"
    assert row["risk_level"] is None


def _write_run(directory: Path, name: str, answers: int) -> None:
    case = {"id": "x", "question": "q", "category": "direct", "language": "en"}
    run = {
        "summary": {"answer_pass_rate": 1.0},
        "answers": [{"case": case, "score": {"passed": True, "failures": []}}] * answers,
    }
    (directory / name).write_text(json.dumps(run), encoding="utf-8")


def test_latest_eval_skips_partial_runs(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = tmp_path / "eval" / "results"
    results.mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    assert client.get("/evals/latest").status_code == 404  # nothing saved yet

    _write_run(results, "20260101-000000.json", answers=len(load_golden()))  # full run
    _write_run(results, "20260102-000000.json", answers=1)  # newer, but partial (--only)

    body = client.get("/evals/latest").json()
    assert body["file"] == "20260101-000000.json"
    assert len(body["cases"]) == len(load_golden())
