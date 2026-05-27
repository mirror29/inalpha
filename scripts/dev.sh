#!/usr/bin/env bash
# dev.sh —— 一键起 Inalpha 本地 dev 环境
#
# 用法:
#   bash scripts/dev.sh [up]     # 起 data + paper + research + orchestration（默认）
#   bash scripts/dev.sh stop     # 停止所有由本脚本拉起的进程
#   bash scripts/dev.sh logs     # 跟随四个 service 的日志
#   bash scripts/dev.sh status   # 检查端口占用 + 已起进程的健康状态
#
# 选项:
#   --force / -f                 # 端口冲突时不报错，仍然尝试启动（让你看错误日志）
#   --no-wait                    # 启动后不等 healthz，直接返回
#
# 端口约定:
#   8001  services/data       (FastAPI + uvicorn)  GET /health
#   8002  services/paper      (FastAPI + uvicorn)  GET /health
#   8003  services/research   (FastAPI + uvicorn)  GET /health
#   4111  mastra dev          (默认端口)            TCP connect
#
# 前置条件:
#   - 已跑 `pnpm i` 和 `uv sync`
#   - services/data 需要可达的 Postgres + .env 配置 (DATABASE_URL / BINANCE_*)
#   - services/paper 在 D-8a 不强依赖 DB
#   - services/research 需要 LLM_API_KEY（默认 deepseek；LLM_PROVIDER=fake 时可空）

set -euo pipefail

# 切到仓库根
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
LOG_DIR="${ROOT}/.tmp/dev-logs"
PID_DIR="${ROOT}/.tmp/dev-pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

# 统一 .env：source 根 .env 让所有子进程（uvicorn / mastra）继承
# 子目录 .env（packages/orchestration/.env / services/*/.env）仍作为
# fallback 覆盖（pydantic-settings list / dotenv override）—— 迁移期友好
if [[ -f "${ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT}/.env"
    set +a
elif [[ "${1:-up}" == "up" ]]; then
    echo "⚠️  未找到根目录 .env —— 先 cp .env.example .env 并填入 LLM key" >&2
    echo "   见 README.md §Quick Start" >&2
    echo "   （仍尝试启动；如各 service 已有 .env 兜底则可继续，否则会 fail-fast）" >&2
    echo "" >&2
fi

# 解析参数
CMD=""
FORCE=0
NO_WAIT=0
for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=1 ;;
        --no-wait) NO_WAIT=1 ;;
        up|stop|down|logs|tail|status) CMD="$arg" ;;
        *)
            if [[ -z "$CMD" ]]; then
                echo "未知命令: $arg" >&2
                echo "用法: bash scripts/dev.sh [up|stop|logs|status] [--force] [--no-wait]" >&2
                exit 2
            fi
            ;;
    esac
done
CMD="${CMD:-up}"

# ---- helpers ----

port_owner() {
    # 返回监听 $1 端口的 PID（如多个则只返回第一个），无则空
    # 注意 lsof 无匹配时 exit=1，必须 || true 防止 set -e 中断
    { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true; } | head -1
}

wait_http_ok() {
    # 在 $2 秒内反复 curl $1，2xx 即返回 0；超时返回 1
    local url="$1" timeout="$2" elapsed=0
    while (( elapsed < timeout )); do
        if curl -fsS -o /dev/null --max-time 1 "$url" 2>/dev/null; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

wait_tcp_open() {
    # 在 $3 秒内反复检查 $1:$2 TCP 是否可连，成功返回 0
    local host="$1" port="$2" timeout="$3" elapsed=0
    while (( elapsed < timeout )); do
        if (echo > "/dev/tcp/${host}/${port}") 2>/dev/null; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

precheck_ports() {
    local conflict=0
    for entry in "data:8001" "paper:8002" "research:8003" "orchestration:4111"; do
        local name="${entry%%:*}"
        local port="${entry##*:}"
        local owner
        owner="$(port_owner "$port")"
        if [[ -n "$owner" ]]; then
            # 如果占用者是本脚本之前起的进程，不算冲突
            local pid_file="${PID_DIR}/${name}.pid"
            if [[ -f "$pid_file" ]] && [[ "$(cat "$pid_file")" == "$owner" ]]; then
                echo "[skip] $name 已在跑 (pid=$owner, port=$port)"
                continue
            fi
            local cmdline
            cmdline="$(ps -p "$owner" -o command= 2>/dev/null | head -c 80 || echo '?')"
            echo "✗ 端口 $port ($name) 已被占用: pid=$owner — $cmdline"
            conflict=1
        fi
    done
    if (( conflict == 1 )); then
        if (( FORCE == 1 )); then
            echo "  (--force 已设置, 继续尝试启动, 失败日志见 ${LOG_DIR}/)"
            return 0
        fi
        echo ""
        echo "提示: 先停掉占用进程, 或加 --force 强制启动。"
        return 1
    fi
}

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

verify_ready() {
    # 等待所有 service 真正就绪。返回 0 = 全好；1 = 至少一个没起来
    # 超时各自不同: data 60s (Postgres + Binance lifespan 慢), paper 30s, research 30s, mastra 90s
    local all_ok=1
    echo ""
    echo "[wait] 验证 service 就绪 (data 60s · paper 30s · research 30s · mastra 90s)..."

    if wait_http_ok "http://127.0.0.1:8001/health" 60; then
        echo "  ✓ data        http://127.0.0.1:8001/health"
    else
        echo "  ✗ data        未就绪 — 看 ${LOG_DIR}/data.log"
        all_ok=0
    fi

    if wait_http_ok "http://127.0.0.1:8002/health" 30; then
        echo "  ✓ paper       http://127.0.0.1:8002/health"
    else
        echo "  ✗ paper       未就绪 — 看 ${LOG_DIR}/paper.log"
        all_ok=0
    fi

    if wait_http_ok "http://127.0.0.1:8003/health" 30; then
        echo "  ✓ research    http://127.0.0.1:8003/health"
    else
        echo "  ✗ research    未就绪 — 看 ${LOG_DIR}/research.log"
        all_ok=0
    fi

    if wait_tcp_open "127.0.0.1" 4111 90; then
        echo "  ✓ mastra      http://127.0.0.1:4111"
    else
        echo "  ✗ mastra      未就绪 — 看 ${LOG_DIR}/orchestration.log"
        all_ok=0
    fi

    if (( all_ok == 1 )); then
        return 0
    fi
    return 1
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
    if ! compgen -G "${LOG_DIR}/*.log" >/dev/null; then
        echo "无日志文件,先跑 \`bash scripts/dev.sh\` 起服务。"
        exit 1
    fi
    tail -F "${LOG_DIR}"/*.log
}

status_report() {
    echo "=== 端口占用 ==="
    for entry in "data:8001" "paper:8002" "research:8003" "orchestration:4111"; do
        local name="${entry%%:*}"
        local port="${entry##*:}"
        local owner
        owner="$(port_owner "$port")"
        if [[ -n "$owner" ]]; then
            local cmdline
            cmdline="$(ps -p "$owner" -o command= 2>/dev/null | head -c 80 || echo '?')"
            echo "  $name (port $port) → pid=$owner — $cmdline"
        else
            echo "  $name (port $port) → 空"
        fi
    done
    echo ""
    echo "=== healthz ==="
    if curl -fsS -o /dev/null --max-time 1 "http://127.0.0.1:8001/health" 2>/dev/null; then
        echo "  ✓ data      http://127.0.0.1:8001/health"
    else
        echo "  ✗ data      not ready"
    fi
    if curl -fsS -o /dev/null --max-time 1 "http://127.0.0.1:8002/health" 2>/dev/null; then
        echo "  ✓ paper     http://127.0.0.1:8002/health"
    else
        echo "  ✗ paper     not ready"
    fi
    if curl -fsS -o /dev/null --max-time 1 "http://127.0.0.1:8003/health" 2>/dev/null; then
        echo "  ✓ research  http://127.0.0.1:8003/health"
    else
        echo "  ✗ research  not ready"
    fi
    if (echo > "/dev/tcp/127.0.0.1/4111") 2>/dev/null; then
        echo "  ✓ mastra    http://127.0.0.1:4111 (TCP open)"
    else
        echo "  ✗ mastra    not ready"
    fi
}

case "$CMD" in
    up)
        if ! precheck_ports; then
            exit 1
        fi
        start_service "data" \
            "${ROOT}/services/data" \
            "uv run uvicorn inalpha_data.main:app --host 127.0.0.1 --port 8001 --reload"
        start_service "paper" \
            "${ROOT}/services/paper" \
            "uv run uvicorn inalpha_paper.main:app --host 127.0.0.1 --port 8002 --reload"
        start_service "research" \
            "${ROOT}/services/research" \
            "uv run uvicorn inalpha_research.main:app --host 127.0.0.1 --port 8003 --reload"
        start_service "orchestration" \
            "${ROOT}/packages/orchestration" \
            "pnpm dev"

        if (( NO_WAIT == 1 )); then
            echo ""
            echo "进程已 fork (--no-wait, 不验证就绪)。"
            echo "   状态:    bash scripts/dev.sh status"
        elif verify_ready; then
            echo ""
            echo "✅ 全部就绪。"
            echo "   日志:    bash scripts/dev.sh logs"
            echo "   状态:    bash scripts/dev.sh status"
            echo "   停止:    bash scripts/dev.sh stop"
            echo "   端点:    data=http://127.0.0.1:8001  paper=http://127.0.0.1:8002  research=http://127.0.0.1:8003  mastra=http://127.0.0.1:4111"
        else
            echo ""
            echo "⚠️  至少一个 service 没起来。查日志: bash scripts/dev.sh logs" >&2
            exit 1
        fi
        ;;
    stop|down)
        stop_all
        ;;
    logs|tail)
        follow_logs
        ;;
    status)
        status_report
        ;;
    *)
        echo "未知命令: $CMD" >&2
        echo "用法: bash scripts/dev.sh [up|stop|logs|status] [--force] [--no-wait]" >&2
        exit 2
        ;;
esac
