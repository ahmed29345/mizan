# syntax=docker/dockerfile:1
# Production image for the Mizan API.
#   Build: docker build -t mizan:local .
#   Run:   docker compose --profile app up -d --build   (API on http://127.0.0.1:8080)

# ---------- stage 1: builder (has uv; installs dependencies + our package) ----------
FROM python:3.12-slim AS builder

# Pin uv to the same minor version as the project (pyproject: uv_build >=0.13.7,<0.14).
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
# COMPILE_BYTECODE: pre-compile .py -> .pyc now, so containers start faster.
# LINK_MODE=copy: the cache is a separate mount, so copy files instead of hard-linking.
# PYTHON_DOWNLOADS=0: use this image's Python 3.12, never download another one.

WORKDIR /app

# Layer 1 - DEPENDENCIES ONLY. Uses just pyproject.toml + uv.lock, so this slow layer is
# cached and reused until a dependency changes. Editing our code does not invalidate it.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project --no-editable

# Layer 2 - OUR CODE. Changes often, so it comes last: rebuilds only redo this fast step.
# --no-dev: no pytest/ruff in production. --no-editable: install mizan INTO .venv, so the
# runtime stage needs only the .venv folder, not the source tree.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------- stage 2: runtime (no uv, no build tools, no source code) ----------
FROM python:3.12-slim AS runtime

# Run as an unprivileged user: if the app is ever compromised, the attacker is not root.
RUN groupadd --system --gid 10001 mizan \
    && useradd --system --uid 10001 --gid mizan --no-create-home --shell /usr/sbin/nologin mizan

WORKDIR /app
# Files stay owned by root: the app user can READ its code but not change it, so an attacker
# who gets into the running app cannot rewrite the app itself.
COPY --from=builder /app/.venv /app/.venv
# Needed at runtime by the migrate job (Alembic reads its config from pyproject.toml) and
# by the seed command. Tests, prototype and eval results stay out (see .dockerignore).
COPY pyproject.toml alembic.ini ./
COPY migrations ./migrations
COPY data/seed ./data/seed
COPY data/eval/golden.jsonl ./data/eval/golden.jsonl

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production
# UNBUFFERED: logs appear immediately in `docker logs` (not stuck in a buffer).
# No secrets here: DATABASE_URL, keys etc. come from environment variables at run time.

USER mizan
EXPOSE 8000

# Docker's own health check, calling our /health endpoint (no curl in slim images).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# One process per container: scale by running more containers, not more workers inside one.
# --proxy-headers: trust X-Forwarded-* from the load balancer in front of the API.
CMD ["uvicorn", "mizan.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
