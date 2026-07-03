#!/usr/bin/env python3
"""GLM-5.2 PR review 脚本（glm-review.yml 调用,也可本地跑）。

读入:
- ``/tmp/pr_diff.txt``   PR 完整 diff(workflow 前一步 ``gh pr diff`` 落盘)
- ``/tmp/pr_title.txt``  PR 标题
- 环境变量 ``ZHIPUAI_API_KEY``(必填) / ``GLM_BASE_URL`` / ``GLM_MODEL``(可选覆盖)

输出:
- ``/tmp/review_body.txt``  渲染好的 markdown 评论正文(后一步 gh pr comment 贴出)

非阻塞约定:任何失败(网络/超时/解析)都写一条失败说明后 exit 0,
不让 review 挂掉 PR 的 checks——review 是锦上添花,不是门禁。

不做 diff 截断:GLM-5.2 与 Claude 同级 1M 上下文,全量 diff 直接喂。
只在 Python 里构造请求体,绕开 YAML/heredoc/shell 三层转义地狱
(2026-07-03 教训:workflow 内嵌 heredoc 顶格中文直接打断 YAML 块标量,
整个文件解析失败,每次 push 报一封失败邮件)。
"""
from __future__ import annotations

import json
import os
import re
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
你是一个资深代码审查者。请审查以下 PR diff。

## 审查要求
1. 先一句话总结这个 PR 的目的
2. 逐维度评估（命中才提，不硬凑）：
   - **正确性**：边界条件、空值、off-by-one、错误假设、异常路径没处理
   - **设计/架构**：职责放错层、越过模块边界、重复造轮子
   - **契约/兼容**：改了公共接口/schema/API 是否破坏现有调用方
   - **错误处理**：失败是被静默吞掉还是显式处理
   - **资源/性能**：无界增长、N+1、循环无上限
   - **安全**：注入、越权、密钥泄漏
3. **severity 阈值**：只提 >= medium 的问题；nit/风格跳过
4. **误报闸**：必须能说出具体失败场景，说不出就不提；file/line 必须
   来自 diff 里真实出现的文件路径，禁止编造
5. **不重复 lint**：ruff/tsc/mypy 已能抓的不要再提
6. **格式**：只输出 JSON（不要 markdown 围栏），schema:
   {"summary": "一句话总结",
    "findings": [{"severity": "critical|major|medium", "file": "路径",
                  "line": 42, "summary": "描述", "failure_scenario": "场景"}]}
   没有 medium 以上问题时 findings 给空数组。
"""

_SEV_ORDER = {"critical": 0, "major": 1, "medium": 2}
_SEV_ICON = {"critical": "🔴", "major": "🟠", "medium": "🟡"}


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
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _extract_json(content: str) -> dict | None:
    """从模型输出提取 JSON(容忍 ```json 围栏 / 前后废话)。"""
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def _render(result: dict, diff: str) -> str:
    lines = ["## 🤖 GLM-5.2 PR Review", "", result.get("summary", ""), ""]
    findings = result.get("findings") or []
    if not findings:
        lines.append("✅ 未发现 medium 以上问题。")
        return "\n".join(lines)

    findings.sort(key=lambda x: _SEV_ORDER.get(x.get("severity", "medium"), 99))
    for f in findings:
        sev = f.get("severity", "medium")
        loc = f.get("file", "?")
        if f.get("line"):
            loc += f":{f['line']}"
        # 幻觉标记:file 路径不在 diff 里出现 → 明示低可信,别让读者白查
        tag = "" if f.get("file", "") and f["file"] in diff else " ⚠️*路径不在 diff 中,可能是误报*"
        lines.append(f"{_SEV_ICON.get(sev, '🟡')} **[{sev.upper()}]** `{loc}`{tag}")
        lines.append(f"  - {f.get('summary', '')}")
        if f.get("failure_scenario"):
            lines.append(f"  - *失败场景：{f['failure_scenario']}*")
        lines.append("")
    return "\n".join(lines)


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
        _fail(f"GLM API HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:  # 网络/超时等,一律非阻塞
        _fail(f"GLM API 调用失败：{e}")

    result = _extract_json(content)
    body = _render(result, diff) if result else (
        "## 🤖 GLM-5.2 PR Review\n\n" + content  # 非 JSON 输出原样贴
    )
    with open(OUT_PATH, "w") as f:
        f.write(body)
    print(f"glm_review: done, {len(body)} chars → {OUT_PATH}")


if __name__ == "__main__":
    main()
