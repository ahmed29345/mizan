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

## Web UI (for demos)

```bash
uv run fastapi dev --port 8001     # then open http://127.0.0.1:8001  (redirects to /ui/)
```

Four screens: **Ask the rules** (chat with clickable citations and a "behind the scenes"
panel), **Regulation library** (English/Arabic side by side, semantic search), **Case review**
(run the five-agent review, approve or return it) and **AI quality** (latest full evaluation).
The EN/عربي switch flips the layout right-to-left. Direct links: `/ui/?lang=ar#library`,
`/ui/#review/<case-id>`. The UI is plain HTML/CSS/JS in `src/mizan/web/`, served by FastAPI.

## Run the API in Docker (production image)

```bash
docker compose --profile app up -d --build   # builds the image, runs migrations, starts the API
curl http://127.0.0.1:8080/health
docker compose --profile app stop api        # stop it again (the database keeps running)
```

The image (see `Dockerfile`) is multi-stage, runs as a non-root user, contains no tests or
secrets, and gets all settings from environment variables. Migrations run as a separate
one-off `migrate` job before the API starts. CI builds the image and checks `/health`.

## Run on Kubernetes (local cluster with kind)

```bash
brew install kind kubernetes-cli
kind create cluster --name mizan
cp k8s/secrets.env.example k8s/secrets.env      # first time only
k8s/deploy.sh                                   # build, load, migrate, roll out (2 replicas)
kubectl -n mizan port-forward svc/mizan-api 8090:80   # then http://127.0.0.1:8090/health
kind delete cluster --name mizan                # remove it when done (frees ~1 GB RAM)
```

Postgres and Ollama stay on the Mac and are reached through `ExternalName` services, like a
managed database in production. Probes: `/livez` (liveness, no database check) and
`/health` (readiness, checks the database). Pods run as non-root with a read-only filesystem.

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
