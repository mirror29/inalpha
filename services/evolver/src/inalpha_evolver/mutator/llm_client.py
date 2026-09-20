"""LLM 变异客户端 —— 包装 ``_shared/llm`` 的 LLMClient，组装 prompt 模板。"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import unified_diff
from hashlib import sha256

from inalpha_shared_llm import LLMClient as SharedLLMClient  # type: ignore[import-untyped]
from inalpha_shared_llm.client import (  # type: ignore[import-untyped]
    MockLLMClient as SharedMockLLMClient,
)
from inalpha_shared_llm.types import CacheMetrics, MutationRequest  # type: ignore[import-untyped]

from ..exceptions import DiffApplyError, LLMError, LoopControlError
from .diff_applier import apply_diff, repair_hunk_counts
from .prompt_templates import SYSTEM_PROMPT, build_user_prompt


def _clean_llm_diff(content: str) -> str:
    """清洗 LLM 输出：剥 markdown fence + 提取 diff 块。

    DeepSeek / GLM-5.2 常把 diff 包在 ```diff ... ``` 里，甚至可能在 diff 前后
    加额外说明文字。
    """
    # 1) 如果内容以 ``` 开头，先剥掉外层 fence
    text = content.strip()
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() in ("```", "```diff"):
        lines = lines[:-1]
    text = "\n".join(lines).strip()

    # 2) 在内容中找 diff 块（以 --- a/ 开头）
    marker = "--- a/"
    idx = text.find(marker)
    if idx >= 0:
        text = text[idx:]

    # 3) 截断在 ``` 之前（如果还有内嵌的 fence）
    end = text.find("\n```")
    if end >= 0:
        text = text[:end]

    return text.strip()


def _full_source_fallback(content: str) -> str | None:
    """提取模型误返的完整 Python 文件，交给既有沙盒继续严格审计。"""
    text = content.strip()
    if "```python" in text:
        text = text.split("```python", 1)[1].split("```", 1)[0].strip()
    elif text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    if not text.startswith("class ") or "def on_bar(" not in text:
        return None
    return text + ("\n" if content.endswith("\n") else "")


def _source_diff(original: str, replacement: str) -> str:
    """把完整源码转换为单文件 canonical unified diff，保留统一血缘格式。"""
    return "".join(
        unified_diff(
            original.splitlines(keepends=True),
            replacement.splitlines(keepends=True),
            fromfile="a/strategy.py",
            tofile="b/strategy.py",
        )
    ).strip()


@dataclass(slots=True)
class MutationResult:
    """单次变异的结果。"""

    new_source: str
    """变异后的策略源码。"""
    unified_diff: str | None
    """LLM 返回的原始 unified diff（存 DB 供 lineage 追溯）。"""
    source_hash: str
    """变异后源码的 SHA256 摘要（防重复）。"""
    llm_cost_usd: float
    """本次 LLM 调用的估算费用（美元）。"""
    cache_hit_tokens: int
    """本次 LLM 调用的缓存命中 tokens（用于 cache 效率统计）。"""
    input_tokens: int = 0
    """本次 LLM 调用的输入 tokens。"""
    output_tokens: int = 0
    """本次 LLM 调用的输出 tokens。"""


@dataclass(slots=True)
class Mutator:
    """变异算子 —— 装箱 LLM 调用 + diff 应用 + 校验。

    E1 使用真实 LLM（通过 ``_shared/llm`` 的 LLMClient）。
    测试时可换 ``MockLLMClient``。
    """

    llm_client: SharedLLMClient | SharedMockLLMClient = field(default_factory=SharedLLMClient)
    max_fuzz: int = 3
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    max_input_utf8_bytes: int = 24_000
    max_output_tokens: int = 8192

    def __post_init__(self) -> None:
        rates = (self.input_usd_per_million, self.output_usd_per_million)
        if (rates[0] is None) != (rates[1] is None):
            raise ValueError("input and output pricing rates must be configured together")
        if any(rate is not None and rate <= 0 for rate in rates):
            raise ValueError("pricing rates must be positive")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if self.max_input_utf8_bytes <= 0:
            raise ValueError("max_input_utf8_bytes must be positive")

    async def mutate(
        self,
        current_source: str,
        report: dict | None = None,
        hint: str = "",
    ) -> MutationResult:
        """执行一次 LLM 变异。

        Args:
            current_source: 当前策略源码。
            report: 回测报告 dict（可选，用于指导变异方向）。
            hint: 变异方向提示。

        Returns:
            ``MutationResult`` 含变异后源码 + diff + 费用统计。

        Raises:
            LLMError: LLM 调用失败。
            DiffApplyError: diff 无法应用。
        """
        user_prompt = build_user_prompt(current_source, report, hint)
        prompt_bytes = len(SYSTEM_PROMPT.encode()) + len(user_prompt.encode())
        if prompt_bytes > self.max_input_utf8_bytes:
            raise LLMError("LLM 变异输入超过已审批上限（按 UTF-8 字节保守校验）；请缩短输入")
        request = MutationRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=self.max_output_tokens,
        )

        try:
            response = await self.llm_client.mutate(request)
        except LoopControlError:
            raise
        except Exception as exc:
            raise LLMError(f"LLM 变异调用失败：{exc}") from exc

        raw_diff = _clean_llm_diff(response.content)
        metrics = response.cache_metrics
        llm_cost_usd = self._cost_usd(metrics)

        # 部分模型违反格式约束返回完整源码；转成 canonical diff 后走同一审计链。
        if not raw_diff or not raw_diff.startswith("---"):
            full_source = _full_source_fallback(response.content)
            if full_source is not None and full_source != current_source:
                raw_diff = _source_diff(current_source, full_source)

        # 空 diff = LLM 认为无需改动
        if not raw_diff or not raw_diff.startswith("---"):
            return MutationResult(
                new_source=current_source,
                unified_diff=None,
                source_hash=sha256(current_source.encode()).hexdigest(),
                llm_cost_usd=llm_cost_usd,
                cache_hit_tokens=metrics.cache_read_tokens,
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
            )

        try:
            new_source = apply_diff(current_source, raw_diff, max_fuzz=self.max_fuzz)
        except DiffApplyError as exc:
            repaired_diff = repair_hunk_counts(raw_diff)
            try:
                new_source = apply_diff(current_source, repaired_diff, max_fuzz=self.max_fuzz)
                raw_diff = repaired_diff
            except DiffApplyError:
                # 带上 cost 信息，上层可决定是否计入成本
                raise DiffApplyError(
                    str(exc),
                    original=current_source,
                    failed_diff=raw_diff,
                    llm_cost_usd=llm_cost_usd,
                    cache_hit_tokens=metrics.cache_read_tokens,
                    input_tokens=metrics.input_tokens,
                    output_tokens=metrics.output_tokens,
                ) from exc

        return MutationResult(
            new_source=new_source,
            unified_diff=raw_diff,
            source_hash=sha256(new_source.encode()).hexdigest(),
            llm_cost_usd=llm_cost_usd,
            cache_hit_tokens=metrics.cache_read_tokens,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
        )

    async def close(self) -> None:
        """关闭该 run 独占的底层 LLM HTTP client。"""
        await self.llm_client.close()

    def _cost_usd(self, metrics: CacheMetrics) -> float:
        if self.input_usd_per_million is None or self.output_usd_per_million is None:
            return float(metrics.cost_usd)
        return float(
            (
                metrics.input_tokens * self.input_usd_per_million
                + metrics.output_tokens * self.output_usd_per_million
            )
            / 1_000_000
        )
