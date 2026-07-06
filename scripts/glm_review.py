#!/usr/bin/env python3
"""GLM-5.2 PR review——claude-review.yml 调用,也可本地跑。

环境:
- ``ZHIPUAI_API_KEY``(必填)
- ``GLM_BASE_URL``(默认 https://yuanyuaicloud.cn/v1)
- ``GLM_MODEL``(默认 glm-5.2)

输入:
- ``/tmp/pr_diff.txt``   PR diff(claude-review.yml 前一步 ``gh pr diff`` 落盘)
- ``/tmp/pr_title.txt``  PR 标题

输出:
- ``/tmp/review_body.txt``  渲染好的 markdown(后一步 gh pr comment 贴出)

约定:任何失败都写 failure 说明后 exit 0,不让 review 挂 PR checks。
不截断 diff——GLM-5.2 1M 上下文,全量喂。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DIFF_PATH = "/tmp/pr_diff.txt"
TITLE_PATH = "/tmp/pr_title.txt"
OUT_PATH = "/tmp/review_body.txt"

BASE_URL = os.environ.get("GLM_BASE_URL", "https://yuanyuaicloud.cn/v1")
MODEL = os.environ.get("GLM_MODEL", "glm-5.2")
TIMEOUT_S = 900  # 全量 diff + 1M 上下文,给足推理时间

SYSTEM_PROMPT = """\
你是这个仓库的资深 reviewer。目标：在合并前尽量拦住真正的 bug、设计缺陷、架构失误。
**不要只对着固定清单打勾**——清单覆盖不到新功能。要先理解再评审。

## Step 1 · 读项目规则（每次都重新读，规则会随项目迭代而变）

- 用 Read 读仓库根 `CLAUDE.md`；改动目录附近若有 `AGENTS.md` / 相关 `docs/` / ADR 也读。
- 把里面的硬约束当成本次 review 的**项目专属规则**——
  CLAUDE.md 更新了，你的评审标准就自动跟着更新，**无需改这个 workflow**。
- 这是项目规则的唯一权威来源；下面 Step 4 的清单只是提示，以你读到的为准。

## Step 2 · 重建意图 + 圈定影响面

- 先一句话说清这个 PR 想做什么。
- 用 Read / Grep / Glob 看 diff **以外**的代码：改动的函数 / 接口 / 契约有哪些调用方？
  碰了哪些模块边界（Inalpha 是 Next.js → Mastra(TS) → Python services 三层）？
- 只有理解了"改动如何与系统其余部分交互"，才谈得上架构评审。

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
- 用中文写 review，每条 finding 用 `[critical|major|medium] file:line — 一句描述 — 依据(CLAUDE.md §X 或 通用原则:<维度>)` 格式
- 没问题 -> 一段中文 LGTM，不硬挑刺
- 只维护一条 sticky 评论，不要逐行贴 inline 评论"""

_SEV_ORDER = {"critical": 0, "major": 1, "medium": 2}
_SEV_ICON = {"critical": "🔴", "major": "🟠", "medium": "🟡"}  # unused, kept for reference


def _fail(msg: str) -> None:
    """写失败说明后正常退出(非阻塞)。"""
    print(f"glm_review: {msg}", file=sys.stderr)
    with open(OUT_PATH, "w") as f:
        f.write(f"## 🤖 GLM-5.2 PR Review\n\n⚠️ review 未完成：{msg}\n")
    sys.exit(0)


def _call_glm(api_key: str, title: str, diff: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"## PR 标题\n{title}\n\n## Diff\n{diff}"},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


def main() -> None:
    api_key = os.environ.get("ZHIPUAI_API_KEY", "")
    if not api_key:
        _fail("ZHIPUAI_API_KEY 未配置(repo Settings → Secrets → Actions)")

    try:
        with open(DIFF_PATH) as f:
            diff = f.read()
        with open(TITLE_PATH) as f:
            title = f.read().strip()
    except OSError as e:
        _fail(f"读输入失败：{e}")

    if not diff.strip():
        _fail("diff 为空")

    try:
        content = _call_glm(api_key, title, diff)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        _fail(f"GLM API HTTP {e.code}：{body}")
    except Exception as e:
        _fail(f"GLM API 调用失败：{e}")

    body = "## 🤖 GLM-5.2 PR Review\n\n" + content
    with open(OUT_PATH, "w") as f:
        f.write(body)
    print(f"glm_review: done, {len(body)} chars → {OUT_PATH}")


if __name__ == "__main__":
    main()