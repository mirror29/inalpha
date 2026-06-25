"""multi-venue connector 路由 + akshare symbol 解析单测（不打网络）。"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from inalpha_data.connectors import (
    Connector,
    _base,
    get_connector_for_venue,
    list_registered_venues,
    register_connector,
    unregister_connector,
)
from inalpha_data.connectors.akshare import _parse_symbol

# ────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────


def _clean_registry() -> None:
    """每个测试前清掉自己注册的 venue，避免 fixture 间污染。"""
    for v in list(_base._REGISTRY.keys()):
        # 留 lifespan 启动期间注册的真 binance（其它测试需要）
        if v not in ("binance", "test-venue"):
            _base._REGISTRY.pop(v, None)


class _StubConnector:
    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        limit: int = 1000,
    ) -> list[tuple[datetime, float, float, float, float, float]]:
        return []

    async def close(self) -> None:
        return None


def test_register_then_lookup() -> None:
    _clean_registry()
    stub: Connector = _StubConnector()
    register_connector("fakevenue", stub)
    try:
        got = get_connector_for_venue("fakevenue")
        assert got is stub
        assert "fakevenue" in list_registered_venues()
    finally:
        unregister_connector("fakevenue")


def test_get_unknown_venue_raises() -> None:
    _clean_registry()
    with pytest.raises(KeyError, match="no connector registered for venue 'gibberish'"):
        get_connector_for_venue("gibberish")


def test_register_same_venue_twice_raises() -> None:
    _clean_registry()
    stub: Connector = _StubConnector()
    register_connector("dupvenue", stub)
    try:
        with pytest.raises(RuntimeError, match="already registered"):
            register_connector("dupvenue", _StubConnector())
    finally:
        unregister_connector("dupvenue")


def test_unregister_is_idempotent() -> None:
    _clean_registry()
    # 注册过的 venue 移除一次后再 unregister 不抛
    register_connector("oncevenue", _StubConnector())
    unregister_connector("oncevenue")
    unregister_connector("oncevenue")
    assert "oncevenue" not in list_registered_venues()


# ────────────────────────────────────────────────────────────────────
# akshare symbol 解析
# ────────────────────────────────────────────────────────────────────


def test_parse_symbol_sh_a_share() -> None:
    prefix, code = _parse_symbol("sh.600519")
    assert prefix == "sh"
    assert code == "600519"


def test_parse_symbol_sz_a_share() -> None:
    prefix, code = _parse_symbol("sz.000001")
    assert prefix == "sz"
    assert code == "000001"


def test_parse_symbol_hk_stock() -> None:
    prefix, code = _parse_symbol("hk.00700")
    assert prefix == "hk"
    assert code == "00700"


@pytest.mark.parametrize(
    "raw,want_prefix,want_code",
    [
        ("jp.6758", "jp", "6758"),
        ("uk.BARC", "uk", "BARC"),
        ("de.SAP", "de", "SAP"),
    ],
)
def test_parse_symbol_global_prefixes(raw: str, want_prefix: str, want_code: str) -> None:
    """G1: akshare 扩 jp/uk/de 三个新前缀，覆盖日股 / 英股 / 德股。"""
    prefix, code = _parse_symbol(raw)
    assert prefix == want_prefix
    assert code == want_code


def test_parse_symbol_case_insensitive_prefix() -> None:
    prefix, _code = _parse_symbol("SH.600519")
    assert prefix == "sh"


def test_parse_symbol_missing_dot_raises() -> None:
    with pytest.raises(ValueError, match="prefix in"):
        _parse_symbol("600519")


def test_parse_symbol_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unknown prefix 'us'"):
        _parse_symbol("us.AAPL")


def test_parse_symbol_empty_code_raises() -> None:
    with pytest.raises(ValueError, match="code is empty"):
        _parse_symbol("sh.")


# ────────────────────────────────────────────────────────────────────
# alpaca connector skipped when keys missing
# ────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────
# yfinance connector：注册 + ts 归一化 + timeframe 映射
# ────────────────────────────────────────────────────────────────────


def test_yfinance_init_registers_venue() -> None:
    """yfinance 无 key，init_connector 总会成功注册。"""
    from inalpha_data.connectors import yfinance_conn

    if yfinance_conn._connector is not None:  # type: ignore[attr-defined]
        yfinance_conn._connector = None  # type: ignore[attr-defined]
        unregister_connector("yfinance")

    conn = yfinance_conn.init_connector()
    try:
        assert conn is not None
        assert "yfinance" in list_registered_venues()
        assert get_connector_for_venue("yfinance") is conn
    finally:
        # 还原状态
        yfinance_conn._connector = None  # type: ignore[attr-defined]
        unregister_connector("yfinance")


def test_yfinance_normalize_ts_naive_to_utc() -> None:
    """yfinance 的 ts（pd.Timestamp / datetime / 字符串）统一到 UTC aware。"""
    from inalpha_data.connectors.yfinance_conn import _normalize_ts

    naive = datetime(2026, 5, 21, 12, 0)
    assert _normalize_ts(naive).tzinfo is UTC

    aware = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    assert _normalize_ts(aware) == aware

    iso_str = "2026-05-21T12:00:00"
    out = _normalize_ts(iso_str)
    assert out.tzinfo is UTC
    assert out.year == 2026 and out.month == 5


def test_yfinance_unsupported_timeframe_raises() -> None:
    """fetch_bars 给不在 map 里的 timeframe 抛 ValueError。"""
    import asyncio

    from inalpha_data.connectors.yfinance_conn import YfinanceConnector

    conn = YfinanceConnector()

    async def _run() -> None:
        await conn.fetch_bars(
            symbol="AAPL",
            timeframe="42m",  # 不存在
            since=datetime(2026, 5, 1, tzinfo=UTC),
            limit=10,
        )

    with pytest.raises(ValueError, match="unsupported timeframe"):
        asyncio.run(_run())


async def test_yfinance_fetch_bars_serialized(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """并发 fetch_bars 必须串行打 Yahoo，防 429 返残缺数据。

    根因：5 标的并发 gather 时 Yahoo 限速，部分标的只回几根 bar（实测串行全 24 根、
    并发 MSFT/META 只回 4 根）。回归探针：并发跑 5 个 fetch_bars，断言同时在飞的
    _fetch_sync 恒为 1（串行）。无锁 → max 会到 5。
    """
    import threading

    from inalpha_data.connectors import yfinance_conn
    from inalpha_data.connectors.yfinance_conn import YfinanceConnector

    in_flight = 0
    max_in_flight = 0
    counter_lock = threading.Lock()

    def _fake_fetch_sync(*, symbol: str, interval: str, since: datetime):  # type: ignore[no-untyped-def]
        nonlocal in_flight, max_in_flight
        with counter_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.03)  # 制造重叠窗口（无锁时并发会重叠）
        with counter_lock:
            in_flight -= 1
        return [(since, 1.0, 1.0, 1.0, 1.0, 100.0)]

    monkeypatch.setattr(yfinance_conn, "_fetch_sync", _fake_fetch_sync)
    monkeypatch.setattr(yfinance_conn, "_last_fetch_mono", 0.0)  # 重置节流时间戳

    conn = YfinanceConnector()
    syms = ["AAPL", "MSFT", "META", "GOOGL", "AMZN"]
    await asyncio.gather(
        *[conn.fetch_bars(s, "1d", datetime(2026, 5, 20, tzinfo=UTC)) for s in syms]
    )
    assert max_in_flight == 1, f"yfinance fetch 未串行（max_in_flight={max_in_flight}）"


def test_yfinance_fetch_ticker_sync_returns_real_bar_ts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """_fetch_ticker_sync 返最后一根 1m bar 的真实成交时间，不再 now() 兜底（issue #62）。"""
    import pandas as pd

    last_ts = pd.Timestamp("2026-06-11 15:59:00", tz="America/New_York")
    df = pd.DataFrame(
        {"Close": [101.0, 102.5]},
        index=pd.DatetimeIndex([last_ts - pd.Timedelta(minutes=1), last_ts]),
    )

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            pass

        def history(self, **_kwargs):  # type: ignore[no-untyped-def]
            return df

    monkeypatch.setattr("yfinance.Ticker", _FakeTicker)

    from inalpha_data.connectors.yfinance_conn import _fetch_ticker_sync

    result = _fetch_ticker_sync("AAPL")
    assert result is not None
    ts, price = result
    assert price == 102.5
    assert ts == last_ts.to_pydatetime().astimezone(UTC)


def test_yfinance_fetch_ticker_sync_empty_history_returns_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """无 1m 数据（退市 / OTC）→ None，上层翻 ValueError（语义与原 last_price 缺失一致）。"""
    import pandas as pd

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            pass

        def history(self, **_kwargs):  # type: ignore[no-untyped-def]
            return pd.DataFrame()

    monkeypatch.setattr("yfinance.Ticker", _FakeTicker)

    from inalpha_data.connectors.yfinance_conn import _fetch_ticker_sync

    assert _fetch_ticker_sync("DELISTED") is None


# ────────────────────────────────────────────────────────────────────
# FRED connector：key 缺失跳过 + ts 归一化
# ────────────────────────────────────────────────────────────────────


def test_fred_init_skipped_without_key() -> None:
    """无 key 时 init_connector 返 None 不注册——同 alpaca 行为。"""
    from inalpha_data.connectors import fred as fred_conn

    if fred_conn._connector is not None:  # type: ignore[attr-defined]
        fred_conn._connector = None  # type: ignore[attr-defined]
        unregister_connector("fred")

    result = fred_conn.init_connector(api_key="")
    assert result is None
    assert "fred" not in list_registered_venues()


def test_fred_normalize_ts_naive_to_utc() -> None:
    from inalpha_data.connectors.fred import _normalize_ts

    naive = datetime(2026, 5, 21)
    assert _normalize_ts(naive).tzinfo is UTC

    aware = datetime(2026, 5, 21, tzinfo=UTC)
    assert _normalize_ts(aware) == aware


def test_fred_unsupported_timeframe_raises() -> None:
    import asyncio

    from inalpha_data.connectors.fred import FredConnector

    # 用一个 dummy fredapi.Fred；不会真发请求
    conn = FredConnector.__new__(FredConnector)  # 跳过 __init__ 的 key 校验
    conn._client = object()  # type: ignore[attr-defined]

    async def _run() -> None:
        await conn.fetch_bars(
            symbol="DFF",
            timeframe="1h",  # FRED 不支持小时级
            since=datetime(2026, 1, 1, tzinfo=UTC),
            limit=10,
        )

    with pytest.raises(ValueError, match="unsupported timeframe"):
        asyncio.run(_run())


def test_alpaca_init_skipped_without_keys() -> None:
    """无 key 时 init_connector 返 None 且不注册—不阻塞 services/data 启动。"""
    from inalpha_data.connectors import alpaca as alpaca_conn

    # 已 init 过会抛，先 close 一下保证干净
    if alpaca_conn._connector is not None:  # type: ignore[attr-defined]
        # 用同步 close（async 函数的 sync drain）：直接清状态
        alpaca_conn._connector = None  # type: ignore[attr-defined]
        unregister_connector("alpaca")

    result = alpaca_conn.init_connector(api_key="", api_secret="")
    try:
        assert result is None
        assert "alpaca" not in list_registered_venues()
    finally:
        # 还原（其它测试不依赖 alpaca）
        pass
