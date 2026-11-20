# Mizan

An Arabic/English AI compliance copilot for Saudi financial regulations. This is a learning and portfolio project: answers cite their sources (RAG), several AI agents review a case with human approval, and the system is evaluated and observable.

- Requirements: https://claude.ai/artifact/6oVYevmJpgp2iGwQ8BKegb
- Clickable prototype: [prototype/index.html](prototype/index.html)

All regulation text and case data in this repository are fictional examples.

## Local development

```bash
docker compose up -d                 # Postgres 17 + pgvector
cp .env.example .env                 # first time only
uv sync                              # install dependencies
uv run alembic upgrade head          # create / update tables
uv run python -m mizan.seed          # load fictional sample regulations (safe to re-run)
ollama pull bge-m3                   # embedding model, first time only (Ollama app must be running)
uv run python -m mizan.index         # chunk + embed regulations for search (safe to re-run)
ollama pull qwen3:8b                 # local LLM for the agent, first time only
uv run python -m mizan.ask "How fast must a bank report a cyber incident?"
uv run fastapi dev                   # http://127.0.0.1:8000/docs
uv run pytest                        # tests (use their own mizan_test database + a fake embedder)
uv run ruff check . && uv run ruff format .
```

Ask the agent over HTTP: `POST /ask` with `{"question": "..."}` (try it at /docs).

Try semantic search: http://127.0.0.1:8000/search?q=How%20fast%20must%20a%20bank%20report%20a%20hack

## Observability (optional): Langfuse traces

Every agent run can be recorded as a trace: each step, prompt, answer, tool call, guardrail
verdict, token count and timing. Langfuse runs locally in Docker, so nothing leaves the machine.

```bash
cp .env.langfuse.example .env.langfuse        # first time: fill in random secrets
docker compose -f docker-compose.langfuse.yml --env-file .env.langfuse up -d
# put the same LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY in .env, then use the app as usual
```

Open http://localhost:3000 (login: LANGFUSE_ADMIN_EMAIL / LANGFUSE_ADMIN_PASSWORD from
.env.langfuse). Tracing switches off automatically when the keys are empty (tests, CI).

After changing a model: `uv run alembic revision --autogenerate -m "describe change"`, read the
generated file in `migrations/versions/`, then `uv run alembic upgrade head`.
