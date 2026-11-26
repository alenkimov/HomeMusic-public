# Build context: repository root (see docker-compose.yml).

# --- PWA ---------------------------------------------------------------------------------
FROM node:22.20.0-bookworm-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json* ./
# npm's cache is kept between builds (BuildKit), not in the image
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY web/ ./
# the version the web app shows beside the Hub's («Меню»: web/vite.config.ts)
COPY VERSION /src/VERSION
RUN npm run build

# --- Deno (JS runtime yt-dlp needs for YouTube) --------------------------------------------
FROM denoland/deno:bin-2.9.8 AS deno

# --- Hub -------------------------------------------------------------------------------------
FROM python:3.12-slim-trixie
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH
# libchromaprint-tools: `fpcalc`, the picture of a file's sound (app/library/acoustic.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git libchromaprint-tools ca-certificates tzdata postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY hub/pyproject.toml hub/uv.lock ./
# uv's cache is kept between builds (BuildKit): a new uv.lock downloads only what changed
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY hub/ ./
RUN --mount=type=cache,target=/root/.cache/uv rm -rf /app/.venv /app/data && uv sync --frozen --no-dev
COPY --from=web /src/hub/app/static /app/app/static
# the version and «Что нового» (app/version.py), and what was built: the commit (`HM_COMMIT`, given by
# scripts/update-server.ps1) and when — last, so that a new commit rebuilds this layer only
COPY VERSION CHANGELOG.md /app/
ARG HM_COMMIT=""
RUN printf '%s' "$HM_COMMIT" > /app/BUILD_COMMIT && date -u +%Y-%m-%dT%H:%M:%SZ > /app/BUILD_AT
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
