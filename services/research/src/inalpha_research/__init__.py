"""Inalpha research service.

D-8b 起步：多 analyst（technical + fundamental）→ research manager → ResearchPlan。

POST /deep_dive 给 packages/orchestration 的 ``research.deep_dive`` tool 调。

LLM 走 DeepSeek（OpenAI 兼容），后续可换 Anthropic / OpenAI。测试用
``FakeLLMClient`` mock。
"""

__version__ = "0.1.0"
