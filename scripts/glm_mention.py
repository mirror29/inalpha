"""GLM @mention 互动脚本（glm.yml 调用）。
读环境变量 CONTEXT，调 GLM-5.2 API，写 /tmp/glm_reply.txt。
失败不抛异常——workflow 非阻塞。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

API_KEY = os.environ.get("ZHIPUAI_API_KEY", "")
CONTEXT = os.environ.get("CONTEXT", "")
BASE_URL = os.environ.get("GLM_BASE_URL", "https://yuanyuaicloud.cn/v1")
MODEL = os.environ.get("GLM_MODEL", "glm-5.2")

if not API_KEY or not CONTEXT:
    print("glm_mention: 缺 ZHIPUAI_API_KEY 或 CONTEXT", file=sys.stderr)
    sys.exit(0)

payload = {
    "model": MODEL,
    "messages": [
        {
            "role": "system",
            "content": "你是 Inalpha 仓库的 AI 助手。用户通过 @glm 触发你的回复。请用中文回答。",
        },
        {"role": "user", "content": CONTEXT},
    ],
    "temperature": 0.7,
    "max_tokens": 2048,
}

try:
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    with open("/tmp/glm_reply.txt", "w") as f:
        f.write(content)
    print("glm_mention: ok")
except Exception as e:
    print(f"glm_mention: failed — {e}", file=sys.stderr)