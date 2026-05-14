# H-21-img: base image pinned by digest. Dependabot's `docker` ecosystem
# tracks /backend and auto-PRs digest bumps as upstream republishes the tag.
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856 AS base

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563 /uv /bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY app/ ./app/

# H-10: run as UID 1000. Chown /app so uv's writable cache (~/.cache/uv inside HOME) works.
RUN useradd -m -u 1000 lolday && chown -R lolday:lolday /app
# M-internal-split: copy + chmod the entrypoint AFTER useradd so the chown target
# user exists. /api/v1/internal/* is hosted by app.internal_app on :8001 (gated
# by NetworkPolicy to lolday-jobs); the public API stays on :8000.
COPY --chown=lolday:lolday entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
USER 1000

EXPOSE 8000 8001

CMD ["/app/entrypoint.sh"]
