# syntax=docker/dockerfile:1
#
# Multi-stage build:
#   builder: has uv, resolves and installs dependencies + our package into /app/.venv
#   runtime: copies only that virtualenv. No uv, no build tools, no source tree.

ARG PYTHON_IMAGE=python:3.13-slim

# ---------------------------------------------------------------- builder ---
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 1) Dependencies only. This layer is cached until pyproject.toml or uv.lock changes,
#    so editing application code doesn't trigger a full reinstall.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# 2) Our package, installed as a regular (non-editable) package into the venv.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------- runtime ---
FROM ${PYTHON_IMAGE} AS runtime

# Run as an unprivileged user, not root.
RUN groupadd --system app && useradd --system --gid app --no-create-home app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Migrations ship with the image so the same image can run `alembic upgrade head`
# as a separate step before the app starts (ADR-020).
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app migrations ./migrations

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    CHESS_API_HOST=0.0.0.0 \
    CHESS_LOG_FORMAT=json

USER app
EXPOSE 8000

CMD ["python", "-m", "chess_analytics.api"]
