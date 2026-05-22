#!/usr/bin/env bash
# dev.sh —— 一键起 Inalpha 本地 dev 环境
#
# 用法:
#   bash scripts/dev.sh          # 起 data + paper + orchestration
#   bash scripts/dev.sh stop     # 停止所有由本脚本拉起的进程
#   bash scripts/dev.sh logs     # 跟随三个 service 的日志
#
# 端口约定:
#   8001  services/data       (FastAPI + uvicorn)
#   8002  services/paper      (FastAPI + uvicorn)
#   4111  mastra dev          (默认端口)
#
# 前置条件:
#   - 已跑 `pnpm i` 和 `uv sync`
#   - services/data 需要可达的 Postgres + .env 配置 (DATABASE_URL / BINANCE_*)
#   - services/paper 在 D-8a 不强依赖 DB

set -euo pipefail

# 切到仓库根
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
LOG_DIR="${ROOT}/.tmp/dev-logs"
PID_DIR="${ROOT}/.tmp/dev-pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

CMD="${1:-up}"

start_service() {
    local name="$1"
    local cwd="$2"
    local cmd="$3"
    local log="${LOG_DIR}/${name}.log"
    local pid_file="${PID_DIR}/${name}.pid"

    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        echo "[skip] $name 已在跑 (pid=$(cat "$pid_file"))"
        return
    fi

    echo "[up]   $name → $log"
    (
        cd "$cwd"
        # shellcheck disable=SC2086
        nohup $cmd >"$log" 2>&1 &
        echo $! > "$pid_file"
    )
}

stop_all() {
    if [[ ! -d "$PID_DIR" ]]; then
        echo "无 PID 目录,没有需要停止的进程。"
        return
    fi
    local found=0
    for pid_file in "$PID_DIR"/*.pid; do
        [[ -f "$pid_file" ]] || continue
        found=1
        local name
        name="$(basename "$pid_file" .pid)"
        local pid
        pid="$(cat "$pid_file")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "[stop] $name (pid=$pid)"
            kill "$pid" 2>/dev/null || true
            # 给 5 秒优雅退出
            for _ in $(seq 1 5); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    done
    [[ $found -eq 0 ]] && echo "无运行中的 dev 进程。"
}

follow_logs() {
    if ! command -v tail >/dev/null 2>&1; then
        echo "需要 tail 命令" >&2
        exit 1
    fi
    if ! compgen -G "${LOG_DIR}/*.log" >/dev/null; then
        echo "无日志文件,先跑 \`bash scripts/dev.sh\` 起服务。"
        exit 1
    fi
    tail -F "${LOG_DIR}"/*.log
}

case "$CMD" in
    up|"")
        start_service "data" \
            "${ROOT}/services/data" \
            "uv run uvicorn inalpha_data.main:app --host 127.0.0.1 --port 8001 --reload"
        start_service "paper" \
            "${ROOT}/services/paper" \
            "uv run uvicorn inalpha_paper.main:app --host 127.0.0.1 --port 8002 --reload"
        start_service "orchestration" \
            "${ROOT}/packages/orchestration" \
            "pnpm dev"
        echo ""
        echo "✅ 全部启动。"
        echo "   日志:    bash scripts/dev.sh logs"
        echo "   停止:    bash scripts/dev.sh stop"
        echo "   端点:    data=http://127.0.0.1:8001  paper=http://127.0.0.1:8002  mastra=http://127.0.0.1:4111"
        ;;
    stop|down)
        stop_all
        ;;
    logs|tail)
        follow_logs
        ;;
    *)
        echo "未知命令: $CMD" >&2
        echo "用法: bash scripts/dev.sh [up|stop|logs]" >&2
        exit 2
        ;;
esac
