"""multi-venue connector 路由 + baostock symbol 解析单测（不打网络）。"""

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
from inalpha_data.connectors.baostock import _fetch_sync, _parse_date, _parse_symbol

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
# baostock symbol 解析
# ────────────────────────────────────────────────────────────────────


def test_parse_symbol_sh_a_share() -> None:
    prefix, code = _parse_symbol("sh.600519")
    assert prefix == "sh"
    assert code == "600519"


def test_parse_symbol_sz_a_share() -> None:
    prefix, code = _parse_symbol("sz.000001")
    assert prefix == "sz"
    assert code == "000001"


def test_parse_symbol_non_a_share_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unknown prefix 'hk'"):
        _parse_symbol("hk.00700")


@pytest.mark.parametrize("raw", ["jp.6758", "uk.BARC", "de.SAP"])
def test_parse_symbol_global_prefixes_raise(raw: str) -> None:
    """全球市场由 yfinance venue 承载，baostock 只接受沪深 A 股。"""
    with pytest.raises(ValueError, match="unknown prefix"):
        _parse_symbol(raw)


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
    with pytest.raises(ValueError, match="exactly 6 digits"):
        _parse_symbol("sh.")


@pytest.mark.parametrize("raw", ["sh.600.519", "sh.60051", "600.519.SH"])
def test_parse_symbol_rejects_noncanonical_a_share_code(raw: str) -> None:
    """A 股代码必须严格为 6 位数字，不能让点号被腾讯 compact symbol 吞掉。"""
    with pytest.raises(ValueError, match=r"exactly 6 digits|prefix"):
        _parse_symbol(raw)


def test_baostock_intraday_timestamp_converts_shanghai_time_to_utc() -> None:
    """腾讯分钟 K 线时间是北京时间，12/14 位格式都必须转换为 UTC。"""
    expected = datetime(2026, 7, 22, 1, 35, tzinfo=UTC)
    assert _parse_date("202607220935") == expected
    assert _parse_date("20260722093500") == expected


def test_baostock_connector_uses_intraday_time_field(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """分钟 K 线不能误用仅含日期的 date 字段把整天压成同一主键。"""
    from inalpha_data.connectors.baostock import BaostockConnector

    async def _fake_fetch(_self: object, **_kwargs: object) -> list[dict[str, str]]:
        return [
            {
                "date": "20260722",
                "time": "20260722093500",
                "open": "10.00",
                "high": "10.10",
                "low": "9.90",
                "close": "10.05",
                "volume": "100",
            }
        ]

    monkeypatch.setattr(BaostockConnector, "_throttled_fetch_sync", _fake_fetch)
    rows = asyncio.run(
        BaostockConnector().fetch_bars(
            "sh.600519",
            "5m",
            datetime(2026, 7, 22, tzinfo=UTC),
            limit=5,
        )
    )

    assert rows[0][0] == datetime(2026, 7, 22, 1, 35, tzinfo=UTC)


def test_baostock_fetch_ticker_parses_tencent_quote(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A 股 fresh ticker 返回腾讯报价的真实时间与价格。"""
    from inalpha_data.connectors.baostock import BaostockConnector

    fields = [""] * 31
    fields[1] = "上证指数"
    fields[3] = "3867.03"
    fields[30] = "20260722150000"

    class _Response:
        content = f'v_sh000001="{"~".join(fields)}";'.encode("gbk")

        def raise_for_status(self) -> None:
            return None

    def _fake_get(url: str, **kwargs: object) -> _Response:
        assert url == "https://qt.gtimg.cn/q=sh000001"
        assert kwargs["trust_env"] is False
        return _Response()

    monkeypatch.setattr("httpx.get", _fake_get)
    ts, price = asyncio.run(BaostockConnector().fetch_ticker("sh.000001"))
    assert ts == datetime(2026, 7, 22, 7, 0, tzinfo=UTC)
    assert price == 3867.03


def test_baostock_fetch_ticker_rejects_missing_quote_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """腾讯实时行情缺字段时必须上抛，不得伪造价格或时间。"""
    from inalpha_data.connectors.baostock import BaostockConnector

    class _Response:
        content = b'v_sh000001="1~index";'

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: _Response())
    with pytest.raises(RuntimeError, match="A-share ticker unavailable"):
        asyncio.run(BaostockConnector().fetch_ticker("sh.000001"))


@pytest.mark.parametrize(
    ("timeframe", "expected_url", "expected_period", "expected_end"),
    [
        ("1wk", "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", "week", "2026-07-23"),
        ("1mo", "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", "month", "2026-07-23"),
        ("5m", "https://ifzq.gtimg.cn/appstock/app/kline/mkline", "m5", None),
        ("15m", "https://ifzq.gtimg.cn/appstock/app/kline/mkline", "m15", None),
        ("30m", "https://ifzq.gtimg.cn/appstock/app/kline/mkline", "m30", None),
        ("1h", "https://ifzq.gtimg.cn/appstock/app/kline/mkline", "m60", None),
    ],
)
def test_baostock_bars_map_all_tencent_periods(
    monkeypatch,
    timeframe: str,
    expected_url: str,
    expected_period: str,
    expected_end: str | None,
) -> None:  # type: ignore[no-untyped-def]
    """周/月/分钟周期必须映射到腾讯对应参数。"""
    from inalpha_data.connectors.baostock import BaostockConnector

    captured: dict[str, object] = {}
    intraday = timeframe not in {"1wk", "1mo"}
    raw_time = "202607220935" if intraday else "2026-07-22"
    row = [raw_time, "10.00", "10.05", "10.10", "9.90", "100"]

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"code": 0, "data": {"sh600519": {expected_period: [row]}}}

    def _fake_get(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr("httpx.get", _fake_get)
    bars = asyncio.run(
        BaostockConnector().fetch_bars(
            "sh.600519", timeframe, datetime(2026, 7, 1, tzinfo=UTC), limit=5
        )
    )
    assert captured["url"] == expected_url
    assert expected_period in str(captured["params"])
    if expected_end is not None:
        assert "2026-07-01" in str(captured["params"])
        assert expected_end in str(captured["params"])
        assert str(captured["params"]).endswith(",5,'}")
    assert len(bars) == 1
    assert bars[0][-1] == 10_000.0


def test_baostock_bars_reject_invalid_tencent_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """腾讯 K 线错误响应必须转换为显式上游异常。"""
    from inalpha_data.connectors.baostock import BaostockConnector

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"code": 1, "data": {}}

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: _Response())
    with pytest.raises(RuntimeError, match="A-share bars unavailable"):
        asyncio.run(
            BaostockConnector().fetch_bars(
                "sh.600519", "1d", datetime(2026, 7, 1, tzinfo=UTC), limit=5
            )
        )


def test_baostock_bars_use_https_feed_when_binary_service_is_unreachable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A 股 K 线不能依赖生产环境不可达的 Baostock TCP 10030 服务。"""
    from inalpha_data.connectors import baostock

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "data": {
                    "sh000001": {
                        "day": [
                            ["2026-07-21", "3835.68", "3864.37", "3864.60", "3834.72", "119330136"],
                            ["2026-07-22", "3839.67", "3861.65", "3884.44", "3839.67", "589368135"],
                        ]
                    }
                },
            }

    def _fake_get(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    def _binary_feed_must_not_run(**_kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("Baostock TCP feed must not be used for bars")

    monkeypatch.setattr("httpx.get", _fake_get)
    monkeypatch.setattr(baostock, "_fetch_baostock_sync", _binary_feed_must_not_run)

    rows = _fetch_sync(
        prefix="sh",
        code="000001",
        period="daily",
        start_str="20260715",
        end_str="20260722",
        limit=1000,
    )

    assert captured["url"] == "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    assert captured["trust_env"] is False
    assert captured["params"] == {"param": "sh000001,day,2026-07-15,2026-07-22,1000,"}
    assert rows[-1] == {
        "date": "2026-07-22",
        "open": "3839.67",
        "close": "3861.65",
        "high": "3884.44",
        "low": "3839.67",
        "volume": 58936813500.0,
    }


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


async def test_yfinance_fetch_bars_per_request_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """锁内单次 history TCP 挂起 → wait_for 超时快速放锁、fetch_bars 降级返 []，不拖死 panel。"""
    from inalpha_data.connectors import yfinance_conn
    from inalpha_data.connectors.yfinance_conn import YfinanceConnector

    monkeypatch.setattr(yfinance_conn, "_FETCH_TIMEOUT_S", 0.1)
    monkeypatch.setattr(yfinance_conn, "_last_fetch_mono", 0.0)

    def _hang(*, symbol: str, interval: str, since: datetime):  # type: ignore[no-untyped-def]
        time.sleep(2.0)  # 模拟 TCP 无响应挂起
        return []

    monkeypatch.setattr(yfinance_conn, "_fetch_sync", _hang)
    conn = YfinanceConnector()
    t = time.monotonic()
    out = await conn.fetch_bars("AAPL", "1d", datetime(2026, 5, 20, tzinfo=UTC))
    elapsed = time.monotonic() - t
    assert out == []  # 超时被 fetch_bars 的 except 吞 → 降级空
    assert elapsed < 1.0  # 0.1s 超时而非等满 2s → 快速放锁


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
# yfinance 代理（YFINANCE_PROXY_URL → CF Worker URL 改写）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,want",
    [
        (
            "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1m",
            "https://proxy.example/query1/v8/finance/chart/AAPL?range=1d&interval=1m",
        ),
        (
            "https://query2.finance.yahoo.com/v7/finance/options/AAPL",
            "https://proxy.example/query2/v7/finance/options/AAPL",
        ),
        (
            "https://finance.yahoo.com/quote/AAPL",
            "https://proxy.example/finance/quote/AAPL",
        ),
        (
            "https://fc.yahoo.com/",
            "https://proxy.example/fc/",
        ),
        # 非 Yahoo URL 原样透传
        ("https://api.example.com/x", "https://api.example.com/x"),
    ],
)
def test_yfinance_rewrite_yahoo_url(raw: str, want: str) -> None:
    """Yahoo 主机 → {proxy_base}/{host-key}，query string 原样保留；非 Yahoo 透传。"""
    from inalpha_data.connectors.yfinance_conn import _rewrite_yahoo_url

    assert _rewrite_yahoo_url(raw, "https://proxy.example") == want


def test_yfinance_proxy_patches_curl_cffi(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """回归锁：_install_yfinance_proxy 必须打中 curl_cffi（yfinance 1.x 实际用的库），
    不再只 patch 标准库 requests 而静默空转。"""
    import requests
    from curl_cffi.requests import Session as CurlSession

    from inalpha_data.connectors.yfinance_conn import _install_yfinance_proxy

    captured: dict[str, str] = {}

    def _fake_request(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        return "OK"

    # monkeypatch 记录 patch 前的真实 request，测试结束自动还原（防污染其它测试的全局态）
    monkeypatch.setattr(CurlSession, "request", _fake_request, raising=True)
    monkeypatch.setattr(requests.Session, "request", _fake_request, raising=True)

    _install_yfinance_proxy("https://proxy.example/")

    # curl_cffi 这条被改写才算修好（fetch_ticker / fetch_bars 走的就是它）
    result = CurlSession.request(
        object(), "GET", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?x=1"
    )
    assert result == "OK"
    assert captured["url"] == "https://proxy.example/query1/v8/finance/chart/AAPL?x=1"

    # 标准库兜底同样命中
    captured.clear()
    requests.Session.request(object(), "GET", "https://finance.yahoo.com/quote/MSFT")
    assert captured["url"] == "https://proxy.example/finance/quote/MSFT"


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
