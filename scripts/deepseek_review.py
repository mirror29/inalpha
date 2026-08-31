#!/usr/bin/env python3
"""DeepSeek V4 Pro PR review——claude-review.yml 调用，也可本地跑。

环境:
- ``DEEPSEEK_API_KEY``（必填）
- ``DEEPSEEK_BASE_URL``（默认 https://api.deepseek.com/v1）
- ``DEEPSEEK_MODEL``（默认 deepseek-v4-pro）

输入:
- ``/tmp/pr_diff.txt``   PR diff（claude-review.yml 前一步 ``gh pr diff`` 落盘）
- ``/tmp/pr_title.txt``  PR 标题

输出:
- ``/tmp/review_body.txt``  渲染好的 markdown（后一步 gh pr comment 贴出）

约定:任何失败都写 failure 说明后 exit 0，不让 review 挂 PR checks。
不截断 diff，完整交给模型评审。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.error
import urllib.request

DIFF_PATH = "/tmp/pr_diff.txt"
TITLE_PATH = "/tmp/pr_title.txt"
RULES_PATH = "CLAUDE.md"
OUT_PATH = "/tmp/review_body.txt"

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
TIMEOUT_S = 900  # 全量 diff 给足推理时间
MAX_ATTEMPTS = 2  # 正常结束但 content 为空时仅重试一次

SYSTEM_PROMPT = """\
你是这个仓库的资深 reviewer。目标：在合并前尽量拦住真正的 bug、设计缺陷、架构失误。
**不要只对着固定清单打勾**——清单覆盖不到新功能。要先理解再评审。

## Step 1 · 读项目规则（每次都重新注入，规则会随项目迭代而变）

- 用户消息中的“项目规则”就是仓库根 `CLAUDE.md` 的完整内容，把其中硬约束当作
  本次 review 的项目专属标准。
- 当前环境没有 Read / Grep / Glob 或其它工具。不要尝试调用工具，也不要输出工具调用协议；
  只能基于项目规则、PR 标题和完整 diff 评审。
- 规则更新后会由脚本自动重新注入，无需修改这个 workflow。

## Step 2 · 重建意图 + 圈定影响面

- 先一句话说清这个 PR 想做什么。
- 只基于给定完整 diff 重建调用关系和模块边界。看不到 diff 外实现时，不要假装已经读取，
  也不要输出工具请求；仅报告能由现有证据支持的问题。
- 只有理解了改动如何与系统其余部分交互，才提出架构 finding。

## Step 3 · 通用工程评审（适用任何功能，新增功能也自动覆盖）

逐维度想，命中才提：
1. **正确性**：边界条件、空值、off-by-one、错误假设、异常路径没处理
2. **设计 / 架构**：职责放错层、越过模块边界、重复造轮子（该复用的没复用）、抽象层级不当
3. **契约 / 兼容**：改了公共接口 / schema / config / API 是否破坏现有调用方；向后兼容与迁移
4. **状态 / 数据流**：状态归属是否清晰、有无单一真相源、并发下 id/counter/nonce 是否冲突、多步状态变更中途失败是否回滚
5. **错误处理**：失败是被静默吞掉还是显式处理；降级路径是否一致
6. **资源 / 性能**：HTTP / DB / 循环 / 回填跨度有无上限；有无 N+1、无界增长
7. **可测性**：新逻辑有无测试；关键边界 / 失败路径是否覆盖
8. **安全**：注入、越权、密钥泄漏、不可信输入直接进危险路径

## Step 4 · 本仓库历史踩过的坑（提示，不是全部）

顺手扫一眼，但**不要**因为只查这些就忽略 Step 3 的通用维度（权威定义见 Step 1 的 CLAUDE.md）：
- 漏 git add：新 import 的实现文件没出现在 diff
- 异常处理：子类 override 是否真生效
- 时间精度：float64 时间戳大数值丢精度
- LLM / prompt：硬编码语言 / 市场 / 品种、tool description 缺三段式、prompt 预设具体输入示例
- 金融时效性：要"现价 / 最新"却没传 fresh=True、判 freshness 看 bar 数量而非 bars[-1].ts 距 as_of 的间隔
- 多空：long-only 策略加了 SHORT/COVER，或用 SELL 表示做空（应 SHORT 开空、COVER 平空）

## review 行为

- **severity 阈值**：只提 >= medium 的问题；nit / 风格 -> 跳过
- **不重复 lint**：ruff / tsc / mypy 已能抓的不要再提
- **误报闸**：设计 / 架构类意见必须能说出"在什么输入 / 时序下会真的出问题"的具体失败场景，
  说不出就降级或不提——宁可漏报一条主观的，不要用噪音淹没真问题
- Write the review in English. Format each finding as `[critical|major|medium] file:line — concise description — evidence (CLAUDE.md §X or general principle: <dimension>)`
- If there are no findings, return a concise English LGTM; do not invent issues
- 只维护一条 sticky 评论，不要逐行贴 inline 评论"""

_SEV_ORDER = {"critical": 0, "major": 1, "medium": 2}
_SEV_ICON = {"critical": "🔴", "major": "🟠", "medium": "🟡"}  # unused, kept for reference


def _fail(msg: str) -> None:
    """写失败说明后正常退出（非阻塞）。"""
    print(f"deepseek_review: {msg}", file=sys.stderr)
    with open(OUT_PATH, "w") as f:
        f.write(f"## 🤖 DeepSeek V4 Pro PR Review\n\n⚠️ Review could not be completed: {msg}\n")
    sys.exit(0)


def _is_valid_review(content: object) -> bool:
    """只接受非空自然语言正文，拒绝模型泄漏的工具调用协议。"""
    if not isinstance(content, str) or not content.strip():
        return False
    protocol_markers = ("<｜｜DSML｜｜tool_calls>", "<tool_call>", '"tool_calls"')
    return not any(marker in content for marker in protocol_markers)


def _call_deepseek(api_key: str, title: str, diff: str, rules: str) -> str:
    """调用 DeepSeek；空正文或工具协议泄漏时重试一次。"""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"## 项目规则（CLAUDE.md）\n{rules}\n\n"
                    f"## PR 标题\n{title}\n\n## Diff\n{diff}"
                ),
            },
        ],
        "temperature": 0.1,
        # V4 Pro 会把 reasoning tokens 计入 completion；大 diff 下 16384 也可能在正文前耗尽。
        "max_tokens": 32768,
    }
    for attempt in range(MAX_ATTEMPTS):
        req = urllib.request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                body = json.loads(resp.read())
        except (http.client.IncompleteRead, TimeoutError):
            if attempt + 1 < MAX_ATTEMPTS:
                continue
            raise
        choice = body["choices"][0]
        content = choice["message"].get("content")
        if choice.get("finish_reason") == "stop" and _is_valid_review(content):
            return content.strip()
        usage = body.get("usage", {})
        reasoning_tokens = usage.get("completion_tokens_details", {}).get(
            "reasoning_tokens", "unknown"
        )
        print(
            "deepseek_review: invalid response "
            f"attempt={attempt + 1} finish_reason={choice.get('finish_reason')} "
            f"content_chars={len(content) if isinstance(content, str) else 0} "
            f"reasoning_tokens={reasoning_tokens}",
            file=sys.stderr,
        )
        if attempt + 1 < MAX_ATTEMPTS:
            payload["messages"].append(
                {
                    "role": "user",
                    "content": (
                        "The previous response did not contain a publishable review. "
                        "No tools are available. Do not emit a tool-call protocol; "
                        "return the final review in English now."
                    ),
                }
            )
    raise ValueError("DeepSeek did not return a publishable review")


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        _fail("DEEPSEEK_API_KEY is not configured (repository Settings → Secrets → Actions)")

    try:
        with open(DIFF_PATH) as f:
            diff = f.read()
        with open(TITLE_PATH) as f:
            title = f.read().strip()
        with open(RULES_PATH) as f:
            rules = f.read()
    except OSError as e:
        _fail(f"Failed to read review input: {e}")

    if not diff.strip():
        _fail("The PR diff is empty")

    try:
        content = _call_deepseek(api_key, title, diff, rules)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        _fail(f"DeepSeek API returned HTTP {e.code}: {body}")
    except Exception as e:
        _fail(f"DeepSeek API request failed: {e}")

    body = "## 🤖 DeepSeek V4 Pro PR Review\n\n" + content
    with open(OUT_PATH, "w") as f:
        f.write(body)
    print(f"deepseek_review: done, {len(body)} chars → {OUT_PATH}")


if __name__ == "__main__":
    main()
