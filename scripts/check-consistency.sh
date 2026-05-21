#!/usr/bin/env bash
# check-consistency.sh —— 跨文件一致性检验
#
# 验证 README.md / CLAUDE.md / AGENTS.md / docs/brand/ 之间的事实声明同步，
# 避免文档漂移（如 README 写 "Phase D 待启动"、CLAUDE.md 写 "Phase D-7"）。
#
# 用法：
#   bash scripts/check-consistency.sh        # 跑全部检查
#   bash scripts/check-consistency.sh -v     # 详细输出（含 pass 行）
#
# Skip directive（行内豁免）：
#   在 markdown 文件的某一行末尾加 `<!-- check-consistency: skip -->`
#   该行不参与所有检查
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

# ---------- C1: 顶层文档引用的 docs/ 链接都存在 ----------
sect "C1 · 顶层文档引用的 docs/ 链接都存在"

# 从 README/CLAUDE/AGENTS 抓 markdown 链接 (text)(path) 中的 docs/ 路径
LINKS=$(
    for f in README.md README.zh-CN.md CLAUDE.md AGENTS.md; do
        [[ -f "$f" ]] || continue
        grep -vE 'check-consistency: skip' "$f"
    done | grep -oE '\((docs/[^)]+\.md)\)' | sed 's/[()]//g' | sort -u || true
)

if [[ -z "$LINKS" ]]; then
    ok "顶层文档未引用 docs/ md 链接（产品 README 不暴露内部文档结构）"
else
    while IFS= read -r link; do
        if [[ -f "$link" ]]; then
            ok "$link"
        else
            fail "$link 被引用但不存在"
        fi
    done <<< "$LINKS"
fi

# ---------- C2: docs/miro/ 不应被任何公开文件引用 ----------
sect "C2 · docs/miro/ 私有空间不被公开文件引用"

# 在所有公开文件中搜 docs/miro 路径或 ADR 字眼（私有信息泄漏检测）
LEAK_HITS=$(
    grep -EnI "docs/miro|\bADR\b|docs/decisions|docs/brand" \
        README.md README.zh-CN.md CLAUDE.md AGENTS.md \
        docs/00-context.md docs/01-architecture-overview.md docs/03-kernel-design.md \
        2>/dev/null \
    | grep -vE 'check-consistency: skip' \
    | grep -vE '\bdocs/miro/\b.*gitignored' \
    | grep -vE 'docs/miro/.*个人空间' \
    | grep -vE 'docs/miro/.*gitignored' \
    || true
)

if [[ -z "$LEAK_HITS" ]]; then
    ok "公开文件中未发现 docs/miro 路径或 ADR 字眼泄漏"
else
    echo "$LEAK_HITS" | while IFS= read -r line; do
        warn "可能泄漏私有信息：$line"
    done
fi

# ---------- C3: 品牌名 Inalpha 拼写 ----------
sect "C3 · 品牌名 Inalpha 拼写"

# 允许"作为标识符"的小写 inalpha：
#   inalpha_<service>     Python 包命名
#   @inalpha/<pkg>        npm scope
#   inalpha.<tld>         域名
#   .inalpha[/]           隐藏目录 / 路径
BAD_PATTERNS='inalpha|InAlpha|inAlpha|INALPHA'
BAD_HITS=$(
    find . -type f -name '*.md' \
        -not -path './node_modules/*' \
        -not -path './.git/*' \
        -not -path './.mastra/*' \
        -not -path './_refs/*' \
        -not -path './docs/miro/*' \
        -print0 2>/dev/null \
    | xargs -0 grep -EnH "$BAD_PATTERNS" 2>/dev/null \
    | grep -vE 'check-consistency: skip' \
    | grep -vE 'inalpha_[a-z_<]+' \
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
    while IFS= read -r line; do
        warn "品牌名疑似错误拼写：$line"
    done <<< "$BAD_HITS"
fi

# ---------- C4: Phase 状态在 CLAUDE.md / AGENTS.md 之间一致 ----------
sect "C4 · Phase 状态一致性"

phase_claude=""
phase_agents=""
for f in CLAUDE.md AGENTS.md; do
    [[ -f "$f" ]] || continue
    phases=$(grep -oE 'Phase [A-Z]-?[0-9]+' "$f" 2>/dev/null | sort -u || true)
    [[ -z "$phases" ]] && continue
    cur=$(echo "$phases" | tail -1)
    echo "  $f 提到的当前 Phase: $cur"
    case "$f" in
        CLAUDE.md) phase_claude=$cur ;;
        AGENTS.md) phase_agents=$cur ;;
    esac
done

if [[ -n "$phase_claude" && -n "$phase_agents" ]]; then
    if [[ "$phase_claude" == "$phase_agents" ]]; then
        ok "CLAUDE.md 与 AGENTS.md 的当前 Phase 一致 ($phase_claude)"
    else
        warn "CLAUDE.md ($phase_claude) vs AGENTS.md ($phase_agents) Phase 不一致"
    fi
fi

# ---------- C5: CLAUDE.md 字符上限 ----------
sect "C5 · CLAUDE.md 字符上限（4000）"

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

# ---------- C6: docs/miro/ 不应入 git ----------
sect "C6 · docs/miro/ 必须 gitignored"

if git ls-files --error-unmatch 'docs/miro/' 2>/dev/null | head -1 | grep -q .; then
    fail "docs/miro/ 内有文件被 git 追踪！请检查 .gitignore"
elif grep -q '^docs/miro/' .gitignore 2>/dev/null; then
    ok "docs/miro/ 在 .gitignore 内，git 未追踪"
else
    warn ".gitignore 没有 docs/miro/ 条目"
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
