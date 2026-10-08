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
    module.STATUS_PATH = os.devnull
    return module


def _review_json(summary="LGTM", findings=None, limitations=None):
    return json.dumps(
        {
            "summary": summary,
            "findings": findings or [],
            "limitations": limitations or [],
        }
    )


class _FakeResponse:
    def __init__(
        self, content: str = _review_json(), finish_reason: str = "stop"
    ) -> None:
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
        self.assertEqual(module.MODEL, "deepseek-flash")

    def test_request_uses_deepseek_model_and_bearer_key(self) -> None:
        module = _load_module()
        captured = {}

        def fake_urlopen(request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse()

        with patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        request = captured["request"]
        payload = json.loads(request.data)
        self.assertEqual(result, _review_json())
        self.assertEqual(
            request.full_url, "https://api.deepseek.com/v1/chat/completions"
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-value")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["max_tokens"], 32768)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertIn("Write the review in English", payload["messages"][0]["content"])
        self.assertIn(
            "## 项目规则（CLAUDE.md）\nproject rules", payload["messages"][1]["content"]
        )
        self.assertEqual(captured["timeout"], module.TIMEOUT_S)

    def test_truncated_content_retries_instead_of_publishing_partial_review(
        self,
    ) -> None:
        module = _load_module()
        responses = [
            _FakeResponse("未完成的 finding", finish_reason="length"),
            _FakeResponse(_review_json("完整 review")),
        ]

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, _review_json("完整 review"))
        self.assertEqual(urlopen.call_count, 2)
        retry_request = urlopen.call_args_list[1].args[0]
        retry_payload = json.loads(retry_request.data)
        self.assertEqual(retry_payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", retry_payload)
        self.assertIn(
            "return the final review in English now",
            retry_payload["messages"][-1]["content"],
        )

    def test_reasoning_exhaustion_retries_without_another_reasoning_budget(self):
        module = _load_module()
        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=[_FakeResponse("", finish_reason="length"), _FakeResponse()],
        ) as urlopen:
            result = module._call_deepseek("key", "title", "diff", "rules")
        self.assertEqual(result, _review_json())
        retry = json.loads(urlopen.call_args_list[1].args[0].data)
        self.assertEqual(retry["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", retry)

    def test_empty_content_retries_then_returns_review(self) -> None:
        module = _load_module()
        responses = [_FakeResponse(""), _FakeResponse(_review_json("最终 review"))]

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, _review_json("最终 review"))
        self.assertEqual(urlopen.call_count, 2)

    def test_incomplete_response_retries_then_returns_review(self) -> None:
        module = _load_module()

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=[
                _IncompleteResponse(),
                _FakeResponse(_review_json("最终 review")),
            ],
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, _review_json("最终 review"))
        self.assertEqual(urlopen.call_count, 2)

    def test_tool_protocol_retries_then_returns_review(self) -> None:
        module = _load_module()
        protocol = '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="Read">'
        responses = [
            _FakeResponse(protocol),
            _FakeResponse(_review_json("最终 review")),
        ]

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = module._call_deepseek(
                "secret-value", "PR title", "diff body", "project rules"
            )

        self.assertEqual(result, _review_json("最终 review"))
        self.assertEqual(urlopen.call_count, 2)

    def test_repeated_empty_content_fails_instead_of_publishing_blank_review(
        self,
    ) -> None:
        module = _load_module()

        with patch.object(
            module.urllib.request,
            "urlopen",
            side_effect=[_FakeResponse(""), _FakeResponse("   ")],
        ):
            with self.assertRaisesRegex(
                ValueError, "did not return a publishable review"
            ):
                module._call_deepseek(
                    "secret-value", "PR title", "diff body", "project rules"
                )

    def test_structured_render_sorts_findings_and_preserves_layout(self):
        module = _load_module()
        finding = {
            "severity": "medium",
            "title": "Catch-all meets coverage gate",
            "path": "scripts/check-e2-readiness.py",
            "line": 30,
            "evidence": "Eight other events satisfy the gate.",
            "impact": "A paid search starts without typed evidence.",
            "recommendation": "Exclude other from qualifying counts.",
        }
        critical = dict(finding, severity="critical", title="Critical issue")
        rendered = module._render_review(_review_json(findings=[finding, critical]))
        self.assertIn("| 1 | 0 | 1 |", rendered)
        self.assertLess(rendered.index("[CRITICAL]"), rendered.index("[MEDIUM]"))
        self.assertIn("- **Evidence:**", rendered)
        self.assertIn("- **Impact:**", rendered)
        self.assertIn("- **Suggested fix:**", rendered)
        self.assertIn("No tests were executed", rendered)

    def test_plain_prose_and_invalid_fields_are_not_publishable(self):
        module = _load_module()
        self.assertFalse(module._is_valid_review("LGTM"))
        self.assertFalse(module._is_valid_review('{"summary":"OK","findings":[]}'))
        self.assertFalse(module._is_valid_review(_review_json(summary="x" * 401)))
        for path, line, severity in [
            ("../secret", 1, "medium"),
            ("/tmp/a", 1, "medium"),
            ("a.py", True, "medium"),
            ("a.py", 0, "medium"),
            ("a.py", 1, "minor"),
        ]:
            finding = dict(
                path=path,
                line=line,
                severity=severity,
                title="Issue",
                evidence="Trigger",
                impact="Failure",
                recommendation="Fix",
            )
            self.assertFalse(module._is_valid_review(_review_json(findings=[finding])))

    def test_renderer_escapes_model_headings_and_html(self):
        module = _load_module()
        rendered = module._render_review(
            _review_json(
                summary="Summary\n## Forged heading <details>",
                limitations=[r"Literal \n remains text."],
            )
        )
        self.assertNotIn("\n## Forged", rendered)
        self.assertIn("&lt;details&gt;", rendered)
        self.assertIn(r"\\n", rendered)

    def test_successful_main_publishes_rendered_review(self):
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for attr in (
                "DIFF_PATH",
                "TITLE_PATH",
                "RULES_PATH",
                "OUT_PATH",
                "STATUS_PATH",
            ):
                setattr(module, attr, str(root / attr))
            for attr in ("DIFF_PATH", "TITLE_PATH", "RULES_PATH"):
                Path(getattr(module, attr)).write_text("input")
            with (
                patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-value"}),
                patch.object(module, "_call_deepseek", return_value=_review_json()),
            ):
                module.main()
            rendered = Path(module.OUT_PATH).read_text()
            self.assertEqual(Path(module.STATUS_PATH).read_text(), "completed")
        self.assertIn("No medium-or-higher issues", rendered)
        self.assertNotIn('"findings"', rendered)

    def test_missing_key_writes_non_blocking_failure(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            module.OUT_PATH = str(Path(tmpdir) / "review.md")
            module.STATUS_PATH = str(Path(tmpdir) / "status.txt")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()
            body = Path(module.OUT_PATH).read_text()

            self.assertEqual(Path(module.STATUS_PATH).read_text(), "incomplete")

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

            with patch.dict(
                os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True
            ):
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

            with patch.dict(
                os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True
            ):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()

            body = Path(module.OUT_PATH).read_text()

        self.assertIn(
            "Review could not be completed: Failed to read review input", body
        )

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
                patch.dict(
                    os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True
                ),
                patch.object(
                    module, "_call_deepseek", side_effect=RuntimeError("boom")
                ),
            ):
                with self.assertRaisesRegex(SystemExit, "0"):
                    module.main()

            body = Path(module.OUT_PATH).read_text()

        self.assertIn(
            "Review could not be completed: DeepSeek API request failed: boom", body
        )

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
                patch.dict(
                    os.environ, {"DEEPSEEK_API_KEY": "secret-value"}, clear=True
                ),
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
