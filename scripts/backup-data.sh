#!/usr/bin/env bash
# backup-data.sh —— 本地持久数据手动备份（ADR-0048 D2 手动入口）
#
# 何时用：对 gitignored 数据做任何迁移 / 删除 / 目录整理**之前**先跑一次
# （2026-06-11 事故教训：mv 前没备份，30MB 聊天历史不可恢复）。
#
# 产出：packages/orchestration/.data/backups/manual-<YYYYMMDD-HHMMSS>/
# manual-* 目录不参与启动时 7 天自动轮转清理，不需要了手动删。
#
# 用法：
#   bash scripts/backup-data.sh

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

DATA_DIR="packages/orchestration/.data"

if [[ ! -d "$DATA_DIR" ]]; then
    echo "✗ $DATA_DIR 不存在，无可备份数据" >&2
    exit 1
fi

shopt -s nullglob
files=("$DATA_DIR"/*.db "$DATA_DIR"/*.db-wal "$DATA_DIR"/*.db-shm)
if [[ ${#files[@]} -eq 0 ]]; then
    echo "✗ $DATA_DIR 下无 *.db 文件" >&2
    exit 1
fi

dest="$DATA_DIR/backups/manual-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dest"
cp "${files[@]}" "$dest/"

echo "✓ 已备份 ${#files[@]} 个文件 → $dest"
du -sh "$dest"
