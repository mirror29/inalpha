#!/usr/bin/env bash
# E1 闭环 · 跨语言端到端 demo —— "真把 LLM 生成的 generate_signals 当 Strategy 跑回测"。
#
# 链路：
#   1. orchestration · sandbox.run_code → AST 审计 → LocalSubprocess 跑 → strategy_v1 校验
#   2. paper · SignalReplayStrategy + BacktestEngine → BacktestReport
#
# 用法：
#   bash scripts/smoke-e1-loop.sh
#
# 退出码：任一步失败 = 非 0。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORCH="$ROOT/packages/orchestration"
PAPER="$ROOT/services/paper"

echo "═══════════════════════════════════════════════════════════════"
echo " E1 闭环端到端 · ADR-0020 三道沙盒 → SignalReplayStrategy → 回测"
echo "═══════════════════════════════════════════════════════════════"
echo

# pipefail 让 tsx 失败时 pipeline 整体 fail；不用 mktemp 中转
( cd "$ORCH" && pnpm -s tsx scripts/smoke-e1-extract.ts ) \
    | ( cd "$PAPER" && uv run --quiet python scripts/smoke_e1_replay.py )
