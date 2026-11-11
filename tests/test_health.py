"""Tests for GET /health.

============================ YOUR TASK (week 1) ============================

pytest runs every function whose name starts with `test_`.
`client` is a fixture from tests/conftest.py: add it as a parameter and pytest passes it in.

1. test_health_ok(client)
     - call   response = client.get("/health")
     - assert response.status_code == 200
     - assert response.json() == {"status": "ok", "database": "ok"}
     (needs the database running:  docker compose up -d)

2. Stretch: test_health_db_down(client)
     Simulate a dead database WITHOUT stopping Docker, using FastAPI dependency overrides:
         from mizan.db import get_session
         from mizan.main import app

         class BrokenSession:
             async def execute(self, *args, **kwargs):
                 raise ConnectionError("db down")

         async def broken_session():
             yield BrokenSession()

         app.dependency_overrides[get_session] = broken_session
         ... call /health, assert 503 ...
         app.dependency_overrides.clear()     # always clean up, or other tests break

     This is the same trick you'll use later to replace the real LLM with a fake one in tests.
============================================================================
"""

# TODO(you): write test_health_ok here.
