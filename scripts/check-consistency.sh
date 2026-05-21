#!/usr/bin/env bash
# check-consistency.sh —— 跨文件一致性检验
#
# 验证 README.md / CLAUDE.md / AGENTS.md / docs/ 之间的事实声明同步，
# 避免文档漂移（如 README 写 "Phase D 待启动"、CLAUDE.md 写 "Phase D-7"）。
#
# 借鉴 https://github.com/deusyu/harness-engineering 的 check-consistency 范式。
#
# 用法：
#   bash scripts/check-consistency.sh        # 跑全部检查
#   bash scripts/check-consistency.sh -v     # 详细输出（含 pass 行）
#
# Skip directive（行内豁免）：
#   在 markdown 文件的某一行末尾加 `<!-- check-consistency: skip -->`
#   该行不参与所有检查（C1 / C2 / C3 等）
#
# 退出码：
#   0 = 全部通过（warn 允许）
#   1 = 至少一项失败（fail）

set -euo pipefail

# 切到仓库根
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VERBOSE=0
[[ "${1:-}" == "-v" || "${1:-}" == "--verbose" ]] && VERBOSE=1

PASS=0
FAIL=0
WARN=0

red()   { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }
yellow(){ printf '\033[33m%s\033[0m' "$*"; }
bold()  { printf '\033[1m%s\033[0m' "$*"; }

ok()    { [[ $VERBOSE -eq 1 ]] && echo "  $(green ✓) $1"; PASS=$((PASS+1)); }
fail()  { echo "  $(red ✗) $1" >&2; FAIL=$((FAIL+1)); }
warn()  { echo "  $(yellow ⚠) $1"; WARN=$((WARN+1)); }
sect()  { echo; echo "$(bold "## $1")"; }

# 排除带 skip directive 的行（C2 / C3 用）
exclude_skip() {
    grep -v 'check-consistency: skip' || true
}

# ---------- C1: ADR 文件数量 vs README/CLAUDE 引用 ----------
sect "C1 · ADR 文件数量与索引同步"

ACTUAL_ADRS=$(ls docs/decisions/[0-9][0-9][0-9][0-9]-*.md 2>/dev/null | wc -l | tr -d ' ')
echo "  实际 ADR 文件数: $ACTUAL_ADRS"

# 找出 README/CLAUDE/AGENTS 里引用的最大 ADR 编号
MAX_REFERENCED=0
for f in README.md CLAUDE.md AGENTS.md; do
    [[ -f "$f" ]] || continue
    nums=$(grep -oE '\b0[0-9]{3}\b' "$f" 2>/dev/null || true)
    [[ -z "$nums" ]] && continue
    local_max=$(echo "$nums" | sort -u | tail -1)
    # 强制十进制比较（避免 bash 把 0019 当八进制）
    if (( 10#$local_max > 10#$MAX_REFERENCED )); then
        MAX_REFERENCED=$local_max
    fi
    echo "  $f 最大 ADR 引用: $local_max"
done

# 实际最大编号
ACTUAL_MAX=$(ls docs/decisions/[0-9][0-9][0-9][0-9]-*.md 2>/dev/null \
    | sed -E 's|.*/([0-9]{4})-.*|\1|' | sort -u | tail -1)
echo "  实际最大 ADR 编号: $ACTUAL_MAX"

if [[ "$MAX_REFERENCED" == "$ACTUAL_MAX" ]]; then
    ok "顶层文档引用到了最新 ADR ($ACTUAL_MAX)"
else
    warn "顶层文档最大引用 ADR-$MAX_REFERENCED 落后于实际 ADR-$ACTUAL_MAX，索引可能过时"
fi

# ---------- C2: 每个 ADR 文件都在 README / AGENTS / docs/04 索引 ----------
sect "C2 · 每个 ADR 都被索引"

for f in docs/decisions/[0-9][0-9][0-9][0-9]-*.md; do
    num=$(basename "$f" | grep -oE '^[0-9]{4}')
    indexed=0
    for idx in README.md AGENTS.md CLAUDE.md docs/04-claude-code-borrowed-patterns.md; do
        [[ -f "$idx" ]] || continue
        if grep -E "(ADR-)?$num\b" "$idx" >/dev/null 2>&1; then
            indexed=1
            break
        fi
    done
    if [[ $indexed -eq 1 ]]; then
        ok "ADR-$num 已索引"
    else
        warn "ADR-$num ($(basename "$f")) 没出现在任何顶层索引（README/AGENTS/CLAUDE/docs/04）"
    fi
done

# ---------- C3: docs/ 链接有效性 ----------
sect "C3 · 顶层文档引用的 docs/ 链接都存在"

# 从 README.md / CLAUDE.md / AGENTS.md 抓 markdown 链接 (text)(path) 中的 path
# 排除带 skip directive 的行
LINKS=$(
    for f in README.md CLAUDE.md AGENTS.md; do
        [[ -f "$f" ]] || continue
        grep -vE 'check-consistency: skip' "$f"
    done | grep -oE '\((docs/[^)]+\.md)\)' | sed 's/[()]//g' | sort -u || true
)

if [[ -z "$LINKS" ]]; then
    warn "未发现任何 docs/ 链接（检查文件是否存在）"
else
    while IFS= read -r link; do
        if [[ -f "$link" ]]; then
            ok "$link"
        else
            fail "$link 被引用但不存在"
        fi
    done <<< "$LINKS"
fi

# ---------- C4: 品牌名拼写 ----------
sect "C4 · 品牌名 Inalpha 拼写"

# 在 markdown 文档里搜错误拼写。允许"作为标识符"的小写 inalpha（详见 docs/brand）：
#   inalpha_<service>     Python 包命名
#   @inalpha/<pkg>        npm scope
#   inalpha.<tld>         域名
#   .inalpha[/]           隐藏目录 / 路径
#   inalpha/              目录字面量
#   `inalpha` / "inalpha" 引用本身（讨论标识符时的元用法）
BAD_PATTERNS='inalpha|InAlpha|inAlpha|INALPHA'
BAD_HITS=$(
    find . -type f -name '*.md' \
        -not -path './node_modules/*' \
        -not -path './.git/*' \
        -not -path './.mastra/*' \
        -not -path './_refs/*' \
        -print0 2>/dev/null \
    | xargs -0 grep -EnH "$BAD_PATTERNS" 2>/dev/null \
    | grep -vE 'check-consistency: skip' \
    | grep -vE 'inalpha_[a-z_]+' \
    | grep -vE '@inalpha/' \
    | grep -vE 'inalpha\.[a-z]+' \
    | grep -vE '\.inalpha[/]' \
    | grep -vE '`\.inalpha`|\.inalpha[/\b]' \
    | grep -vE '`inalpha`|"inalpha"|/inalpha[/$]' \
    | grep -vE 'inalpha-' \
    | grep -vE '/ ?inalpha / ?' \
    || true
)

if [[ -z "$BAD_HITS" ]]; then
    ok "未发现错误拼写"
else
    # 用 here-string 而非管道，避免子 shell 吞掉 WARN 计数
    while IFS= read -r line; do
        warn "品牌名疑似错误拼写：$line"
    done <<< "$BAD_HITS"
fi

# ---------- C5: Phase 状态在多份文档之间一致 ----------
sect "C5 · Phase 状态一致性"

# 从 CLAUDE.md / AGENTS.md / README.md 抓 "Phase D-N" 字样
# 注：macOS 默认 bash 3.2 不支持 declare -A，用普通变量代替
phase_claude=""
phase_agents=""
phase_readme=""
for f in CLAUDE.md AGENTS.md README.md; do
    [[ -f "$f" ]] || continue
    phases=$(grep -oE 'Phase [A-Z]-?[0-9]+' "$f" 2>/dev/null | sort -u || true)
    [[ -z "$phases" ]] && continue
    cur=$(echo "$phases" | tail -1)
    echo "  $f 提到的当前 Phase: $cur"
    case "$f" in
        CLAUDE.md) phase_claude=$cur ;;
        AGENTS.md) phase_agents=$cur ;;
        README.md) phase_readme=$cur ;;
    esac
done

# 简单校验：CLAUDE.md / AGENTS.md 的当前 phase 要相等
if [[ -n "$phase_claude" && -n "$phase_agents" ]]; then
    if [[ "$phase_claude" == "$phase_agents" ]]; then
        ok "CLAUDE.md 与 AGENTS.md 的当前 Phase 一致 ($phase_claude)"
    else
        warn "CLAUDE.md ($phase_claude) vs AGENTS.md ($phase_agents) Phase 不一致"
    fi
fi

# README 可能列了多个 Phase（A/B/C/D 状态表），跟 CLAUDE 的"当前 Phase"语义不同，仅提示
if [[ -n "$phase_claude" && -n "$phase_readme" && "$phase_claude" != "$phase_readme" ]]; then
    warn "README.md 提到的最大 Phase ($phase_readme) ≠ CLAUDE.md ($phase_claude)。README 状态表可能过时"
fi

# ---------- C6: CLAUDE.md 字符上限 ----------
sect "C6 · CLAUDE.md 字符上限（claw-code 实证 4000）"

if [[ -f CLAUDE.md ]]; then
    chars=$(wc -c < CLAUDE.md | tr -d ' ')
    echo "  当前字符数: $chars"
    if [[ $chars -le 4000 ]]; then
        ok "CLAUDE.md ≤ 4000 字符"
    elif [[ $chars -le 4500 ]]; then
        warn "CLAUDE.md 超 4000（$chars），接近硬上限，考虑精简"
    else
        fail "CLAUDE.md 严重超长（$chars > 4500），必须精简"
    fi
fi

# ---------- 总结 ----------
echo
echo "===================="
echo "通过: $(green $PASS)  警告: $(yellow $WARN)  失败: $(red $FAIL)"
echo "===================="

if [[ $FAIL -gt 0 ]]; then
    echo "$(red '❌ 一致性检验失败')"
    echo "在不便修复的行末加 \`<!-- check-consistency: skip -->\` 可豁免单行检查。"
    exit 1
fi

if [[ $WARN -gt 0 ]]; then
    echo "$(yellow '✓ 一致性检验通过（含告警）')"
else
    echo "$(green '✓ 一致性检验全部通过')"
fi
exit 0
