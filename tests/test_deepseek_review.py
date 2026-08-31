"""DeepSeek PR review 脚本回归测试。"""
from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "deepseek_review.py"


def _load_module():
    """从脚本路径加载独立模块实例。"""
    spec = importlib.util.spec_from_file_location("deepseek_review", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, content: str = "LGTM", finish_reason: str = "stop") -> None:
        self._content = content
        self._finish_reason = finish_reason

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": self._finish_reason,
                        "message": {"content": self._content},
                    }
                ]
            }
        ).encode()


class _IncompleteResponse(_FakeResponse):
    def read(self) -> bytes:
        raise http.client.IncompleteRead(b"{}")


class DeepSeekReviewTest(unittest.TestCase):
    def test_defaults_target_deepseek_v4_pro(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            module = _load_module()

        self.assertEqual(module.BASE_URL, "https://api.deepseek.com/v1")
        self.assertEqual(module.MODEL, "deepseek-v4-pro")

    def test_request_uses_deepseek_model_and_bearer_key(self) -> None:
        module = _load_module()
        captured = {}

        def fake_urlopen(request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse()

        with patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module._call_deepseek("secret-value", "PR title", "diff body", "project rules")

        request = captured["request"]
        payload = json.loads(request.data)
        self.assertEqual(result, "LGTM")
        self.assertEqual(request.full_url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-value")
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["max_tokens"], 32768)
        self.assertIn("Write the review in English", payload["messages"][0]["content"])
        self.assertIn("## 项目规则（CLAUDE.md）\nproject rules", payload["messages"][1]["content"])
        self.assertEqual(captured["timeout"], module.TIMEOUT_S)

    def test_truncated_content_retries_instead_of_publishing_partial_review(self) -> None:
        module = _load_module()
        responses = [
            _FakeResponse("未完成的 finding", finish_reason="length"),
            _FakeResponse("完整 review"),
        ]

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, "完整 review")
        self.assertEqual(urlopen.call_count, 2)
        retry_request = urlopen.call_args_list[1].args[0]
        retry_payload = json.loads(retry_request.data)
        self.assertIn(
            "return the final review in English now",
            retry_payload["messages"][-1]["content"],
        )

    def test_empty_content_retries_then_returns_review(self) -> None:
        module = _load_module()
        responses = [_FakeResponse(""), _FakeResponse("最终 review")]

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = module._call_deepseek("secret-value", "PR title", "diff body", "project rules")

        self.assertEqual(result, "最终 review")
        self.assertEqual(urlopen.call_count, 2)

    def test_incomplete_response_retries_then_returns_review(self) -> None:
        module = _load_module()

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=[_IncompleteResponse(), _FakeResponse("最终 review")],
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, "最终 review")
        self.assertEqual(urlopen.call_count, 2)

    def test_tool_protocol_retries_then_returns_review(self) -> None:
        module = _load_module()
        protocol = '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="Read">'
        responses = [_FakeResponse(protocol), _FakeResponse("最终 review")]

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, "最终 review")
        self.assertEqual(urlopen.call_count, 2)

    def test_repeated_empty_content_fails_instead_of_publishing_blank_review(self) -> None:
        module = _load_module()

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=[_FakeResponse(""), _FakeResponse("   ")],
        ):
            with self.assertRaisesRegex(ValueError, "did not return a publishable review"):
                module._call_deepseek(
                    "secret-value", "PR title", "diff body", "project rules"
                )

    def test_missing_key_writes_non_blocking_failure(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            module.OUT_PATH = str(Path(tmpdir) / "review.md")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()
            body = Path(module.OUT_PATH).read_text()

        self.assertIn("DeepSeek V4 Pro PR Review", body)
        self.assertIn("DEEPSEEK_API_KEY is not configured", body)
        self.assertNotIn("GLM-5.2", body)

    def test_empty_diff_writes_english_failure(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module.DIFF_PATH = str(root / "diff.txt")
            module.TITLE_PATH = str(root / "title.txt")
            module.RULES_PATH = str(root / "rules.md")
            module.OUT_PATH = str(root / "review.md")
            Path(module.DIFF_PATH).write_text("")
            Path(module.TITLE_PATH).write_text("PR title")
            Path(module.RULES_PATH).write_text("project rules")

            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()

            body = Path(module.OUT_PATH).read_text()

        self.assertIn("Review could not be completed: The PR diff is empty", body)

    def test_input_read_error_writes_english_failure(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module.DIFF_PATH = str(root / "missing-diff.txt")
            module.OUT_PATH = str(root / "review.md")

            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()

            body = Path(module.OUT_PATH).read_text()

        self.assertIn("Review could not be completed: Failed to read review input", body)

    def test_api_error_writes_english_failure(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module.DIFF_PATH = str(root / "diff.txt")
            module.TITLE_PATH = str(root / "title.txt")
            module.RULES_PATH = str(root / "rules.md")
            module.OUT_PATH = str(root / "review.md")
            Path(module.DIFF_PATH).write_text("diff body")
            Path(module.TITLE_PATH).write_text("PR title")
            Path(module.RULES_PATH).write_text("project rules")

            with (
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True),
                patch.object(module, "_call_deepseek", side_effect=RuntimeError("boom")),
            ):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()

            body = Path(module.OUT_PATH).read_text()

        self.assertIn("Review could not be completed: DeepSeek API request failed: boom", body)

    def test_http_error_writes_english_failure(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module.DIFF_PATH = str(root / "diff.txt")
            module.TITLE_PATH = str(root / "title.txt")
            module.RULES_PATH = str(root / "rules.md")
            module.OUT_PATH = str(root / "review.md")
            Path(module.DIFF_PATH).write_text("diff body")
            Path(module.TITLE_PATH).write_text("PR title")
            Path(module.RULES_PATH).write_text("project rules")
            error = module.urllib.error.HTTPError(
                "https://api.deepseek.com/v1/chat/completions",
                429,
                "Too Many Requests",
                {},
                io.BytesIO(b"rate limited"),
            )

            with (
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True),
                patch.object(module, "_call_deepseek", side_effect=error),
            ):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()

            body = Path(module.OUT_PATH).read_text()

        self.assertIn(
            "Review could not be completed: DeepSeek API returned HTTP 429: rate limited",
            body,
        )


if __name__ == "__main__":
    unittest.main()
