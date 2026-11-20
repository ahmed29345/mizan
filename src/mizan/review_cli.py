"""Run compliance reviews from the terminal.

  uv run python -m mizan.review_cli start data/seed/cases/demo-wallet.json
  uv run python -m mizan.review_cli show <case-id>
  uv run python -m mizan.review_cli decide <case-id> approve
  uv run python -m mizan.review_cli decide <case-id> return "Explain the Art. 12 finding"

Each command is a separate process: `decide` resumes a review paused by an earlier `start`,
which works because the paused graph is saved in Postgres (the checkpointer).
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from mizan.agent.llm import get_chat_model
from mizan.db import SessionLocal, engine
from mizan.models import Case
from mizan.review.checkpointer import postgres_checkpointer
from mizan.review.graph import build_review_graph
from mizan.schemas.case import CaseCreate, DecisionRequest
from mizan.services import cases as service

ICONS = {"compliant": "OK  ", "non_compliant": "FAIL", "needs_info": "INFO"}


def print_case(case: Case) -> None:
    print(f"\nCase {case.id}\n{case.title}\nStatus: {case.status}  (revision {case.revision})")
    report = case.report
    if not report:
        return
    print(f"\nRISK: {report['risk_level'].upper()}\n\n{report['summary']}\n\nFindings:")
    for f in report["findings"]:
        print(f"  {ICONS[f['status']]}  {f['regulation_code']} Art. {f['article_number']}")
        print(f"        {f['explanation']}")
    print("\nRecommended actions:")
    for action in report["recommended_actions"]:
        print(f"  - {action}")
    if case.reviewer_comment:
        print(f"\nOfficer's comment: {case.reviewer_comment}")


async def main(args: argparse.Namespace, new_case: CaseCreate | None) -> None:
    async with postgres_checkpointer() as checkpointer, SessionLocal() as session:
        graph = build_review_graph(get_chat_model(), checkpointer)
        if args.command == "start":
            assert new_case is not None
            case = await service.create_case(session, new_case)
            print(f"Created case {case.id}. Running the agents (about 1-2 minutes)...")
            case = await service.start_review(session, graph, case.id)
        elif args.command == "decide":
            decision = DecisionRequest(decision=args.decision, comment=args.comment)
            case = await service.decide(session, graph, uuid.UUID(args.case_id), decision)
        else:
            case = await service.get_case(session, uuid.UUID(args.case_id))
        print_case(case)
        if case.status == "awaiting_approval":
            print(f"\nNext: uv run python -m mizan.review_cli decide {case.id} approve")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-agent compliance review")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start").add_argument("file")
    commands.add_parser("show").add_argument("case_id")
    decide = commands.add_parser("decide")
    decide.add_argument("case_id")
    decide.add_argument("decision", choices=["approve", "return"])
    decide.add_argument("comment", nargs="?")
    arguments = parser.parse_args()
    # Read the file BEFORE async code starts: blocking disk I/O inside `async def` would
    # freeze the event loop (ruff rule ASYNC240).
    case_file = None
    if arguments.command == "start":
        raw = json.loads(Path(arguments.file).read_text(encoding="utf-8"))
        case_file = CaseCreate.model_validate({k: v for k, v in raw.items() if k[0] != "_"})
    asyncio.run(main(arguments, case_file))
