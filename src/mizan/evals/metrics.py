"""Scoring functions. Pure functions (no database, no LLM), so they are easy to unit test."""

from pydantic import BaseModel

from mizan.evals.dataset import GoldenCase
from mizan.schemas.ask import AskResponse

# Arabic-Indic (٠-٩) and Persian (۰-۹) digits -> 0-9, so "٢٤ ساعة" matches the fact "24".
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalise(text: str) -> str:
    return " ".join(text.translate(_DIGITS).lower().split())


# ---------- retrieval ----------


class RetrievalScore(BaseModel):
    case_id: str
    ranks: dict[str, int | None]  # "CODE Art. N" -> position among retrieved articles (1 = top)
    recall: float  # share of expected articles that were retrieved at all
    reciprocal_rank: float  # 1 / position of the first expected article (0 if none)


def score_retrieval(case: GoldenCase, retrieved: list[tuple[str, str]]) -> RetrievalScore:
    """`retrieved`: distinct (code, article) in ranked order, as the agent would see them."""
    ranks = {
        f"{code} Art. {number}": (
            retrieved.index((code, number)) + 1 if (code, number) in retrieved else None
        )
        for code, number in case.expected
    }
    found = [rank for rank in ranks.values() if rank is not None]
    return RetrievalScore(
        case_id=case.id,
        ranks=ranks,
        recall=len(found) / len(ranks) if ranks else 1.0,
        reciprocal_rank=1 / min(found) if found else 0.0,
    )


# ---------- answers ----------


class AnswerScore(BaseModel):
    case_id: str
    passed: bool
    failures: list[str]  # human-readable reasons, empty when passed
    citation_recall: float  # expected articles the answer cited
    extra_citations: list[str]  # cited but not expected (noise, or a sign of guessing)
    hallucinated_citations: list[str]  # cited but never retrieved
    facts_missing: list[list[str]]
    forbidden_found: list[str]


def score_answer(case: GoldenCase, response: AskResponse) -> AnswerScore:
    answer = normalise(response.answer)
    cited = {(c.regulation_code, c.article_number) for c in response.citations}
    expected = set(case.expected)

    hallucinated = [
        f"{c.regulation_code} Art. {c.article_number}" for c in response.citations if not c.verified
    ]
    extra = sorted(f"{code} Art. {num}" for code, num in cited - expected)
    facts_missing = [
        group for group in case.facts if not any(normalise(alt) in answer for alt in group)
    ]
    forbidden = [phrase for phrase in case.forbidden if normalise(phrase) in answer]
    recall = len(cited & expected) / len(expected) if expected else 1.0

    failures: list[str] = []
    if case.answerable:
        missing = sorted(f"{code} Art. {num}" for code, num in expected - cited)
        if missing:
            failures.append(f"did not cite {', '.join(missing)}")
        if facts_missing:
            failures.append(f"missing facts {facts_missing}")
    else:
        # The right behaviour is to say "not covered": any citation means it pretended.
        if cited:
            failures.append(f"cited {sorted(cited)} for a question the library cannot answer")
    if forbidden:
        failures.append(f"used outside knowledge: {forbidden}")
    if hallucinated:
        failures.append(f"hallucinated citations {hallucinated}")

    return AnswerScore(
        case_id=case.id,
        passed=not failures,
        failures=failures,
        citation_recall=recall,
        extra_citations=extra,
        hallucinated_citations=hallucinated,
        facts_missing=facts_missing,
        forbidden_found=forbidden,
    )
