"""``MessageBus`` 的单测：pub/sub + endpoint + wildcard。"""
from __future__ import annotations

import pytest

from inalpha_paper.kernel.msgbus import MessageBus

# ─── pub/sub ───


def test_publish_to_no_subscribers_returns_zero() -> None:
    bus = MessageBus()
    n = bus.publish("data.quotes.binance.BTC/USDT", {"price": 100})
    assert n == 0


def test_exact_topic_match() -> None:
    bus = MessageBus()
    received: list[object] = []
    bus.subscribe("data.quotes.binance.BTC/USDT", received.append)

    bus.publish("data.quotes.binance.BTC/USDT", {"p": 1})
    assert received == [{"p": 1}]


def test_wildcard_star_matches_multi_char() -> None:
    bus = MessageBus()
    received: list[str] = []
    bus.subscribe("data.quotes.binance.*", lambda m: received.append(m["sym"]))

    bus.publish("data.quotes.binance.BTC/USDT", {"sym": "BTC"})
    bus.publish("data.quotes.binance.ETH/USDT", {"sym": "ETH"})
    bus.publish("data.quotes.okx.BTC/USDT", {"sym": "BTC-OKX"})  # 不匹配

    assert received == ["BTC", "ETH"]


def test_wildcard_question_matches_single_char() -> None:
    bus = MessageBus()
    received: list[str] = []
    bus.subscribe("evt.?", lambda m: received.append(m))

    bus.publish("evt.a", "single-a")
    bus.publish("evt.ab", "multi-ab")  # 不匹配
    bus.publish("evt.b", "single-b")

    assert received == ["single-a", "single-b"]


def test_multiple_subscribers_to_same_pattern() -> None:
    bus = MessageBus()
    a: list[object] = []
    b: list[object] = []
    bus.subscribe("x.y", a.append)
    bus.subscribe("x.y", b.append)

    bus.publish("x.y", "hello")
    assert a == ["hello"]
    assert b == ["hello"]


def test_unsubscribe_specific_handler() -> None:
    bus = MessageBus()
    a: list[object] = []
    b: list[object] = []
    bus.subscribe("x", a.append)
    bus.subscribe("x", b.append)

    assert bus.unsubscribe("x", a.append) is True
    bus.publish("x", "msg")
    assert a == []
    assert b == ["msg"]


def test_unsubscribe_nonexistent_returns_false() -> None:
    bus = MessageBus()
    assert bus.unsubscribe("x", lambda m: None) is False


def test_publish_count_reflects_actual_handlers() -> None:
    bus = MessageBus()
    bus.subscribe("data.*", lambda m: None)
    bus.subscribe("data.quotes.*", lambda m: None)
    bus.subscribe("events.*", lambda m: None)

    n = bus.publish("data.quotes.binance.BTC", {})
    assert n == 2  # 前两个匹配


def test_handler_can_unsubscribe_during_iteration() -> None:
    """handler 在 publish 触发中调 unsubscribe 不应该崩。"""
    bus = MessageBus()
    captured: list[int] = []

    def h1(_: object) -> None:
        captured.append(1)
        bus.unsubscribe("x", h1)

    def h2(_: object) -> None:
        captured.append(2)

    bus.subscribe("x", h1)
    bus.subscribe("x", h2)

    bus.publish("x", None)
    bus.publish("x", None)

    # 第一次：h1 + h2 都触发，h1 之后 unsub
    # 第二次：只剩 h2
    assert captured == [1, 2, 2]


# ─── endpoint ───


def test_register_and_send_to_endpoint() -> None:
    bus = MessageBus()
    received: list[object] = []
    bus.register_endpoint("Risk.execute", received.append)

    bus.send("Risk.execute", {"cmd": "submit"})
    assert received == [{"cmd": "submit"}]


def test_duplicate_endpoint_registration_raises() -> None:
    bus = MessageBus()
    bus.register_endpoint("X", lambda m: None)
    with pytest.raises(ValueError, match="already registered"):
        bus.register_endpoint("X", lambda m: None)


def test_send_to_unknown_endpoint_raises() -> None:
    bus = MessageBus()
    with pytest.raises(KeyError, match="not registered"):
        bus.send("Nope", {})


def test_deregister_endpoint() -> None:
    bus = MessageBus()
    bus.register_endpoint("X", lambda m: None)
    assert bus.deregister_endpoint("X") is True
    assert bus.deregister_endpoint("X") is False  # 第二次返回 False


# ─── inspection ───


def test_inspection_methods() -> None:
    bus = MessageBus()
    bus.subscribe("a", lambda m: None)
    bus.subscribe("b", lambda m: None)
    bus.register_endpoint("E1", lambda m: None)
    bus.register_endpoint("E2", lambda m: None)

    assert bus.subscription_count() == 2
    assert set(bus.endpoint_names()) == {"E1", "E2"}
