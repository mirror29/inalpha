#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SELFHOST_ENV_FILE="$ROOT/infra/.env.selfhost"
export ENV_FILE=.env.selfhost
EXAMPLE_FILE="$ROOT/infra/.env.selfhost.example"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.prod.yml" -f "$ROOT/infra/docker-compose.selfhost.yml" --env-file "$SELFHOST_ENV_FILE")

usage() {
  cat <<'EOF'
Usage: bash scripts/selfhost.sh <command> [args]

Commands:
  init                         Create infra/.env.selfhost with generated secrets
  up                           Build and start the self-host stack
  down                         Stop the self-host stack
  logs [service]               Follow service logs
  status                       Show service status
  create-user --email EMAIL    Create the initial dashboard user securely
EOF
}

require_env() {
  if [[ ! -f "$SELFHOST_ENV_FILE" ]]; then
    printf 'Missing %s. Run: bash scripts/selfhost.sh init\n' "$SELFHOST_ENV_FILE" >&2
    exit 1
  fi
}

generate_secret() {
  openssl rand -hex 32
}

init() {
  if [[ -e "$SELFHOST_ENV_FILE" ]]; then
    printf '%s already exists; refusing to overwrite it.\n' "$SELFHOST_ENV_FILE" >&2
    exit 1
  fi
  cp "$EXAMPLE_FILE" "$SELFHOST_ENV_FILE"
  local postgres_password redis_password jwt_secret encryption_key
  postgres_password="$(generate_secret)"
  redis_password="$(generate_secret)"
  jwt_secret="$(generate_secret)"
  encryption_key="$(generate_secret)"
  python3 - "$SELFHOST_ENV_FILE" "$postgres_password" "$redis_password" "$jwt_secret" "$encryption_key" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
postgres_password, redis_password, jwt_secret, encryption_key = sys.argv[2:]
content = path.read_text()
content = content.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={postgres_password}\n")
content = content.replace("REDIS_PASSWORD=\n", f"REDIS_PASSWORD={redis_password}\n")
content = content.replace("JWT_SECRET=\n", f"JWT_SECRET={jwt_secret}\n")
content = content.replace("LLM_CONFIG_ENCRYPTION_KEY=\n", f"LLM_CONFIG_ENCRYPTION_KEY={encryption_key}\n")
content = content.replace("__POSTGRES_PASSWORD__", postgres_password)
content = content.replace("__REDIS_PASSWORD__", redis_password)
path.write_text(content)
PY
  chmod 600 "$SELFHOST_ENV_FILE"
  printf 'Created infra/.env.selfhost with generated secrets.\n'
  printf 'Next: bash scripts/selfhost.sh up\n'
}

create_user() {
  require_env
  local email=""
  while (($#)); do
    case "$1" in
      --email)
        email="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        return
        ;;
      *)
        printf 'Unknown create-user argument: %s\n' "$1" >&2
        exit 2
        ;;
    esac
  done
  if [[ -z "$email" ]]; then
    printf 'create-user requires --email EMAIL\n' >&2
    exit 2
  fi
  local password
  read -r -s -p "Password: " password
  printf '\n'
  if [[ -z "$password" ]]; then
    printf 'Password cannot be empty.\n' >&2
    exit 2
  fi
  printf '%s' "$password" | "${COMPOSE[@]}" run --rm -T paper \
    uv run python scripts/create_user.py \
    --email "$email" --subject console:dev --password-stdin
}

command="${1:-}"
case "$command" in
  init)
    init
    ;;
  up)
    require_env
    "${COMPOSE[@]}" up -d --build
    "${COMPOSE[@]}" ps
    ;;
  down)
    require_env
    "${COMPOSE[@]}" down
    ;;
  logs)
    require_env
    shift
    "${COMPOSE[@]}" logs -f "$@"
    ;;
  status)
    require_env
    "${COMPOSE[@]}" ps
    ;;
  create-user)
    shift
    create_user "$@"
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    printf 'Unknown command: %s\n' "$command" >&2
    usage >&2
    exit 2
    ;;
esac
