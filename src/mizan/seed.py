"""Load the fictional sample regulations into the database.

Run:  uv run python -m mizan.seed

Idempotent: safe to run many times. Regulations that already exist are skipped, not duplicated.
It goes through the SAME service as the API, so seed data gets the same validation.
"""

import asyncio
import json
from pathlib import Path

from mizan.db import SessionLocal, engine
from mizan.schemas.regulation import RegulationCreate
from mizan.services.regulations import DuplicateRegulationError, create_regulation

# src/mizan/seed.py -> parents[2] is the repository root.
SEED_FILE = Path(__file__).resolve().parents[2] / "data" / "seed" / "regulations.json"


async def seed() -> None:
    raw = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    # Validate every record BEFORE writing anything: one bad record stops the whole run.
    regulations = [RegulationCreate.model_validate(item) for item in raw["regulations"]]

    async with SessionLocal() as session:
        for data in regulations:
            try:
                await create_regulation(session, data)
                print(f"  created  {data.code}  ({len(data.articles)} articles)")
            except DuplicateRegulationError:
                print(f"  skipped  {data.code}  (already exists)")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
