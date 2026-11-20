"""The golden set: questions whose correct answers (articles + key facts) we know in advance."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from mizan.models import Language

GOLDEN_FILE = Path(__file__).resolve().parents[3] / "data" / "eval" / "golden.jsonl"

Category = Literal["direct", "paraphrase", "multi", "unanswerable", "attack"]


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    language: Language
    category: Category
    # Articles a correct answer must cite: [(regulation_code, article_number), ...].
    # Empty = the library does NOT contain the answer; the agent should say so.
    expected: list[tuple[str, str]]
    # Facts the answer must state. Each inner list = acceptable alternatives (any one is enough).
    facts: list[list[str]] = []
    # Phrases that must NOT appear (e.g. an answer from the model's own outside knowledge).
    forbidden: list[str] = []

    @property
    def answerable(self) -> bool:
        return bool(self.expected)


def load_golden(path: Path = GOLDEN_FILE) -> list[GoldenCase]:
    # JSONL = one JSON object per line: easy to diff, review and append to in git.
    lines = path.read_text(encoding="utf-8").splitlines()
    cases = [GoldenCase.model_validate_json(line) for line in lines if line.strip()]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids in golden set")
    return cases
