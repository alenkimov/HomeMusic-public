#!/usr/bin/env bash
# Local development: Postgres, Navidrome and youtube-pot in Docker (docker-compose.dev.yml), the Hub and the web on
# the host with reload. Needs Docker, uv, Node 22, ffmpeg, fpcalc (libchromaprint-tools), deno.
#
#   scripts/dev.sh up      containers up; the first time also .env.dev, config.dev.yaml and the music folders
#   scripts/dev.sh hub     the Hub on :8080 (uvicorn --reload)
#   scripts/dev.sh web     the PWA on :5173 (Vite, /api goes to :8080)
#   scripts/dev.sh test    the Hub's tests (pytest args pass through: scripts/dev.sh test -k tags)
#   scripts/dev.sh scan    ask Navidrome to rescan data/music/library
#   scripts/dev.sh down    containers down (data stays; `down -v` wipes it)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f docker-compose.dev.yml)

load_env() {
  [[ -f .env.dev ]] || { echo "No .env.dev: run scripts/dev.sh up first" >&2; exit 1; }
  set -a
  # shellcheck disable=SC1091
  source .env.dev
  set +a
  # the Hub runs in hub/: paths of .env.dev are from the repository root
  local name value
  for name in MUSIC_ROOT DATA_DIR CONFIG_FILE YTDLP_CACHE_DIR; do
    value="${!name:-}"
    [[ -n "$value" && "$value" != /* ]] && export "$name=$ROOT/$value"
  done
  return 0
}

init() {
  if [[ ! -f .env.dev ]]; then
    cp .env.dev.example .env.dev
    key="$(cd hub && uv run --no-sync python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    sed -i "s|^HUB_SECRET_KEY=.*|HUB_SECRET_KEY=$key|" .env.dev
    echo "made .env.dev"
  fi
  [[ -f config.dev.yaml ]] || { cp config.example.yaml config.dev.yaml; echo "made config.dev.yaml"; }
  mkdir -p data/music/{library,staging,.trash,.backups} data/hub
}

case "${1:-}" in
  up)
    (cd hub && uv sync -q)
    init
    "${COMPOSE[@]}" up -d --wait
    echo "Navidrome: http://localhost:4533 (admin / admin). Next: scripts/dev.sh hub, scripts/dev.sh web"
    ;;
  hub)
    load_env
    cd hub
    uv sync -q
    exec uv run --no-sync uvicorn app.main:app --reload --port 8080
    ;;
  web)
    cd web
    [[ -d node_modules ]] || npm ci --no-audit --no-fund
    exec npm run dev
    ;;
  test)
    shift
    load_env
    cd hub
    uv sync -q
    exec uv run --no-sync pytest -q -n auto "$@"
    ;;
  scan)
    load_env
    curl -fsS "$NAVIDROME_URL/rest/startScan?u=$NAVIDROME_ADMIN_USER&p=$NAVIDROME_ADMIN_PASSWORD&v=1.16.1&c=dev&f=json"
    echo
    ;;
  down)
    shift
    "${COMPOSE[@]}" down "$@"
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
