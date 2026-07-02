"""``/accounts/me`` 多币种 cash + base 折算集成测试（D-11）。

走本地 FX 路径（USD base + USDT 桶，USDT→USD=1.0 本地解析），不需要 data 服务的
``/fx`` 网络调用——验证多币种桶记账 + base 折算 equity + 无 fx_warnings。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import fresh_account_token

pytestmark = pytest.mark.integration


def test_accounts_me_multicurrency_after_crypto_buy(client: TestClient) -> None:
    """crypto BUY 后：cash 进 USDT 桶，base(USD) 折算正确，无 FX 网络。"""
    _, token = fresh_account_token("mc")
    headers = {"Authorization": f"Bearer {token}"}

    # BUY 0.01 BTC @ 50000，fee_rate 默认 0.001 → notional 500, fee 0.5
    r = client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "BTC/USDT",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": 50_000.0,
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "FILLED"

    acct = client.get("/accounts/me", headers=headers)
    assert acct.status_code == 200, acct.json()
    body = acct.json()

    assert body["base_currency"] == "USD"
    # 初始资金在 USD 桶；crypto 买单扣 USDT 桶（可为负）
    assert body["cash_balances"]["USD"] == pytest.approx(10_000.0)
    assert body["cash_balances"]["USDT"] == pytest.approx(-500.5)
    # base(USD) 折算总现金 = 10000 + (-500.5)×1.0
    assert body["cash"] == pytest.approx(9_499.5)
    # 持仓 mark-to-market:测试环境 data 不可达 → 最新价拿不到,fallback 开仓均价
    # (0.01×50000×1.0)并出估值告警(不静默,金融时效硬约束)
    assert body["positions_value"] == pytest.approx(500.0)
    # 总权益 = 现金 + 持仓 = 10000 - fee(0.5)
    assert body["total_equity"] == pytest.approx(9_999.5)
    # USD / USDT 都本地可解析 → 无 FX 折算告警;唯一告警是最新价不可用的降级说明
    assert len(body["fx_warnings"]) == 1
    assert "最新价不可用" in body["fx_warnings"][0]


def test_realized_pnl_converted_to_base(client: TestClient) -> None:
    """部分平仓后 realized_pnl 按计价货币折算到 base（USDT→USD 1.0）汇总，不裸相加。"""
    _, token = fresh_account_token("mc")
    headers = {"Authorization": f"Bearer {token}"}

    # BUY 0.02 @ 50000，再 SELL 0.01 @ 60000 → 平掉 0.01，realized = (60000-50000)*0.01 = 100 USDT
    client.post("/orders/submit", headers=headers, json={
        "symbol": "BTC/USDT", "side": "BUY", "type": "MARKET",
        "quantity": 0.02, "ref_price": 50_000.0,
    })
    client.post("/orders/submit", headers=headers, json={
        "symbol": "BTC/USDT", "side": "SELL", "type": "MARKET",
        "quantity": 0.01, "ref_price": 60_000.0,
    })

    body = client.get("/accounts/me", headers=headers).json()
    # realized_pnl 经 USDT→USD(1.0) 折算 = 100；走的是分桶折算路径而非裸相加
    assert body["realized_pnl"] == pytest.approx(100.0)
    # 剩余 0.01 未平仓 → 测试环境拿不到最新价,只有 mark 降级告警,无 FX 折算告警
    assert all("FX" not in w for w in body["fx_warnings"])


def test_realized_pnl_includes_fully_closed_positions(client: TestClient) -> None:
    """完全平仓（quantity=0）的持仓 realized_pnl 仍计入快照（include_flat=True 修复）。"""
    _, token = fresh_account_token("mc")
    headers = {"Authorization": f"Bearer {token}"}

    # BUY 0.01 @ 50000，再 SELL 0.01 @ 55000 → 全平，realized = 5000*0.01 = 50 USDT
    client.post("/orders/submit", headers=headers, json={
        "symbol": "BTC/USDT", "side": "BUY", "type": "MARKET",
        "quantity": 0.01, "ref_price": 50_000.0,
    })
    client.post("/orders/submit", headers=headers, json={
        "symbol": "BTC/USDT", "side": "SELL", "type": "MARKET",
        "quantity": 0.01, "ref_price": 55_000.0,
    })

    body = client.get("/accounts/me", headers=headers).json()
    # 持仓已全平 → positions_value 0；但 realized_pnl 仍应反映已平仓盈亏（折算后 50）
    assert body["positions_value"] == pytest.approx(0.0)
    assert body["realized_pnl"] == pytest.approx(50.0)


def test_positions_value_marks_to_market(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最新价可用时持仓按市价估值(含浮盈),总权益随行情浮动。

    stub 掉 DataClient:买 0.01 @ 50000 后 mark 涨到 60000 →
    positions_value = 0.01×60000 = 600,总权益 = 9499.5 + 600 = 10099.5(浮盈计入)。
    """

    class _StubDataClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def get_ticker(
            self, *, venue: str, symbol: str, fresh: bool | None = None
        ) -> dict[str, object]:
            return {"venue": venue, "symbol": symbol, "price": 60_000.0, "is_stale": False}

        async def close(self) -> None:
            pass

    monkeypatch.setattr("inalpha_paper.api.orders.DataClient", _StubDataClient)

    _, token = fresh_account_token("mc")
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "BTC/USDT", "side": "BUY", "type": "MARKET",
            "quantity": 0.01, "ref_price": 50_000.0,
        },
    )
    body = client.get("/accounts/me", headers=headers).json()
    assert body["positions_value"] == pytest.approx(600.0)
    assert body["total_equity"] == pytest.approx(10_099.5)
    assert body["fx_warnings"] == []


def test_positions_carry_currency(client: TestClient) -> None:
    """/positions 行带 currency（crypto → USDT）。"""
    _, token = fresh_account_token("mc")
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "BTC/USDT", "side": "BUY", "type": "MARKET",
            "quantity": 0.01, "ref_price": 50_000.0,
        },
    )
    r = client.get("/positions", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"
    assert rows[0]["currency"] == "USDT"
    # 派生模式字段:现货仓显式标 spot(前端徽标判定依据)
    assert rows[0]["trading_mode"] == "spot"
