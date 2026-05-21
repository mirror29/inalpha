"""进程内消息总线：pub/sub + endpoint 双形态。

设计依据 [refs/nautilus.md §5](../../../../docs/refs/nautilus.md)：

- **pub/sub**：广播多订阅者，topic 支持 wildcard 通配（``*`` 多字符，``?`` 单字符）
- **endpoint**：点对点，单一 handler；用于命令链（Strategy → Risk → Execution）

约束（也来自 Nautilus 实证）：

- **无 dead-letter queue**：``publish`` 找不到 subscriber 静默丢；``send`` 找不到 endpoint 抛 ``KeyError``
- handler **串行**调用，同一进程同步执行（D-4 不引入 asyncio）
- topic 命名约定：``<domain>.<sub>.<sub>...``，如 ``data.quotes.binance.BTC/USDT`` /
  ``events.order.my-strategy-1`` / ``events.position.my-strategy-1``
"""
from __future__ import annotations

import fnmatch
from collections.abc import Callable
from typing import Any

MessageHandler = Callable[[Any], None]


class MessageBus:
    """进程内同步 pub/sub + endpoint。"""

    def __init__(self) -> None:
        # list of (topic_pattern, handler) —— 顺序遍历匹配
        self._subscriptions: list[tuple[str, MessageHandler]] = []
        # endpoint -> handler （唯一）
        self._endpoints: dict[str, MessageHandler] = {}

    # ─── pub/sub ───

    def publish(self, topic: str, msg: Any) -> int:
        """发布消息到 topic，返回触发的 handler 数。

        ``msg`` 是 ``Any``，调用方负责保证类型；handler 签名 ``(msg) -> None``。
        """
        count = 0
        # 复制一份遍历，handler 内部 unsubscribe 不会扰动当前循环
        for pattern, handler in list(self._subscriptions):
            if _matches(pattern, topic):
                handler(msg)
                count += 1
        return count

    def subscribe(self, topic_pattern: str, handler: MessageHandler) -> None:
        """订阅匹配 pattern 的 topic。多次订阅同一 pattern + 同一 handler 会重复触发。"""
        self._subscriptions.append((topic_pattern, handler))

    def unsubscribe(self, topic_pattern: str, handler: MessageHandler) -> bool:
        """取消订阅，返回是否实际删除了一项。"""
        for i, (p, h) in enumerate(self._subscriptions):
            if p == topic_pattern and h == handler:
                del self._subscriptions[i]
                return True
        return False

    # ─── endpoint ───

    def register_endpoint(self, endpoint: str, handler: MessageHandler) -> None:
        """注册点对点 endpoint。同名重复注册抛 ``ValueError``。"""
        if endpoint in self._endpoints:
            raise ValueError(f"endpoint {endpoint!r} already registered")
        self._endpoints[endpoint] = handler

    def deregister_endpoint(self, endpoint: str) -> bool:
        """取消注册 endpoint，返回是否实际删除了。"""
        return self._endpoints.pop(endpoint, None) is not None

    def send(self, endpoint: str, msg: Any) -> None:
        """发送到 endpoint，未注册抛 ``KeyError`` （不静默吞）。"""
        handler = self._endpoints.get(endpoint)
        if handler is None:
            raise KeyError(f"endpoint {endpoint!r} not registered")
        handler(msg)

    # ─── inspection ───

    def subscription_count(self) -> int:
        return len(self._subscriptions)

    def endpoint_names(self) -> list[str]:
        return list(self._endpoints.keys())


def _matches(pattern: str, topic: str) -> bool:
    """Wildcard 匹配。

    ``*`` 多字符（含空），``?`` 单字符。完全匹配 = pattern 等于 topic。
    实现走 ``fnmatch.fnmatchcase``（大小写敏感）。
    """
    if pattern == topic:
        return True
    if "*" not in pattern and "?" not in pattern:
        return False
    return fnmatch.fnmatchcase(topic, pattern)
