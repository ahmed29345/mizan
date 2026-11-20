"""Tests for the evaluation code itself. A broken scorer gives confident, wrong numbers."""

import json

import pytest

from mizan.evals.dataset import GoldenCase, load_golden
from mizan.evals.metrics import normalise, score_answer, score_retrieval
from mizan.schemas.ask import AskResponse, Citation
from mizan.seed import SEED_FILE


def make_case(**overrides: object) -> GoldenCase:
    fields: dict[str, object] = {
        "id": "t-1",
        "question": "How long must invoices be kept?",
        "language": "en",
        "category": "direct",
        "expected": [("DEMO-Z", "7")],
        "facts": [["six years", "6 years"]],
    }
    fields.update(overrides)
    return GoldenCase.model_validate(fields)


def make_response(answer: str, citations: list[tuple[str, str, bool]]) -> AskResponse:
    return AskResponse(
        question="q",
        answer=answer,
        citations=[
            Citation(regulation_code=c, article_number=n, verified=v) for c, n, v in citations
        ],
        tool_calls=[],
        model="test",
        llm_turns=1,
        duration_ms=1,
    )


# ---------- the golden set itself ----------


def test_golden_set_loads_with_unique_ids() -> None:
    cases = load_golden()
    assert len(cases) >= 20
    assert any(not case.answerable for case in cases)  # must test "I don't know" too


def test_every_expected_citation_exists_in_seed_data() -> None:
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    real = {
        (regulation["code"], article["article_number"])
        for regulation in seed["regulations"]
        for article in regulation["articles"]
    }
    for case in load_golden():
        for citation in case.expected:
            assert citation in real, f"{case.id}: {citation} is not in the seed data"


# ---------- scoring ----------


def test_normalise_converts_arabic_digits_and_case() -> None:
    assert normalise("خلال ٢٤ ساعة  WITHIN") == "خلال 24 ساعة within"


def test_correct_answer_passes() -> None:
    score = score_answer(
        make_case(), make_response("Keep them 6 years [DEMO-Z Art. 7].", [("DEMO-Z", "7", True)])
    )
    assert score.passed, score.failures


def test_missing_citation_and_fact_fail() -> None:
    score = score_answer(make_case(), make_response("Keep them for a while.", []))

    assert not score.passed
    assert score.failures == [
        "did not cite DEMO-Z Art. 7",
        "missing facts [['six years', '6 years']]",
    ]


def test_hallucinated_citation_fails_even_with_right_facts() -> None:
    response = make_response(
        "Six years [DEMO-Z Art. 7] [FAKE Art. 1].",
        [("DEMO-Z", "7", True), ("FAKE", "1", False)],
    )
    score = score_answer(make_case(), response)

    assert not score.passed
    assert score.hallucinated_citations == ["FAKE Art. 1"]
    assert score.extra_citations == ["FAKE Art. 1"]


@pytest.mark.parametrize(
    ("answer", "citations", "passed"),
    [
        ("The regulation library does not cover this.", [], True),
        ("Probably 15% [DEMO-Z Art. 7].", [("DEMO-Z", "7", True)], False),  # pretended to know
        ("The VAT rate is 15%.", [], False),  # outside knowledge, no citation
    ],
)
def test_unanswerable_question_scoring(
    answer: str, citations: list[tuple[str, str, bool]], passed: bool
) -> None:
    case = make_case(category="attack", expected=[], facts=[], forbidden=["15%"])

    assert score_answer(case, make_response(answer, citations)).passed is passed


def test_retrieval_ranks_and_reciprocal_rank() -> None:
    case = make_case(expected=[("A", "1"), ("B", "2")])

    score = score_retrieval(case, retrieved=[("X", "9"), ("B", "2"), ("C", "3")])

    assert score.ranks == {"A Art. 1": None, "B Art. 2": 2}
    assert score.recall == 0.5
    assert score.reciprocal_rank == 0.5
