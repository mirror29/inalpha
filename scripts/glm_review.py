#!/usr/bin/env python3
"""GLM-5.2 PR review——glm-review.yml 调用,也可本地跑。

环境:
- ``ZHIPUAI_API_KEY``(必填)
- ``GLM_BASE_URL``(默认 https://yuanyuaicloud.cn/v1)
- ``GLM_MODEL``(默认 glm-5.2)

输入:
- ``/tmp/pr_diff.txt``   PR diff(glm-review.yml 前一步 ``gh pr diff`` 落盘)
- ``/tmp/pr_title.txt``  PR 标题

输出:
- ``/tmp/review_body.txt``  渲染好的 markdown(后一步 gh pr comment 贴出)

约定:任何失败都写 failure 说明后 exit 0,不让 review 挂 PR checks。
不截断 diff——GLM-5.2 1M 上下文,全量喂。
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

SYSTEM_PROMPT = """\
你是一个资深代码审查者。审查以下 PR diff,只提 >= medium 的问题。

审查维度(命中才提,不硬凑):
- 正确性:边界/空值/off-by-one/异常路径
- 设计/架构:跨层/越界/重复轮子
- 契约/兼容:改公共接口/schema/API 是否破坏调用方
- 错误处理:吞掉还是显式处理
- 资源/性能:N+1/无界增长
- 安全:注入/越权/密钥泄漏

规则:
- nit/风格跳过
- 必须能说出失败场景,说不出就不提
- ruff/tsc/mypy 已抓的跳过
- file/line 必须是 diff 里真实出现的路径,禁止编造

输出只 JSON(不要 markdown 围栏):
{"summary":"一句话","findings":[
  {"severity":"critical|major|medium","file":"路径","line":42,
   "summary":"描述","failure_scenario":"场景"}
]}
无问题时 findings=[]."""

_SEV_ORDER = {"critical": 0, "major": 1, "medium": 2}
_SEV_ICON = {"critical": "🔴", "major": "🟠", "medium": "🟡"}


def _fail(msg: str) -> None:
    print(f"glm_review: {msg}", file=sys.stderr)
    with open(OUT_PATH, "w") as f:
        f.write(f"## 🤖 GLM-5.2 PR Review\n\n⚠️ review 未完成：{msg}\n")
    sys.exit(0)


def _call(api_key: str, title: str, diff: str) -> str:
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
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def _extract_json(content: str) -> dict | None:
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
        tag = ""
        if f.get("file", "") and f["file"] not in diff:
            tag = " ⚠️ 路径不在 diff 中,可能是误报"
        lines.append(f"{_SEV_ICON.get(sev, '🟡')} **[{sev.upper()}]** `{loc}`{tag}")
        lines.append(f"  - {f.get('summary', '')}")
        if f.get("failure_scenario"):
            lines.append(f"  - *失败场景：{f['failure_scenario']}*")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    api_key = os.environ.get("ZHIPUAI_API_KEY", "")
    if not api_key:
        _fail("ZHIPUAI_API_KEY 未配置")

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
        content = _call(api_key, title, diff)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        _fail(f"GLM API HTTP {e.code}：{body}")
    except Exception as e:
        _fail(f"GLM API 调用失败：{e}")

    result = _extract_json(content)
    body = _render(result, diff) if result else (
        "## 🤖 GLM-5.2 PR Review\n\n" + content
    )
    with open(OUT_PATH, "w") as f:
        f.write(body)
    print(f"glm_review: {len(body)} chars → {OUT_PATH}")


if __name__ == "__main__":
    main()
