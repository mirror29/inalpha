#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
credentials_file="${ROOT}/.tmp/evolution-credentials.env"

if [[ -f "$credentials_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$credentials_file"
    set +a
elif [[ -z "${EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64:-}" ]]; then
    echo "✗ 缺少本地演化公钥；请先运行 bash scripts/dev.sh up" >&2
    exit 1
fi

cd "${ROOT}/apps/dashboard"
exec pnpm exec next dev -p 3001
