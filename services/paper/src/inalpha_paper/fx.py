"""跨币种 → base currency 折算（D-11）。

``/accounts/me`` 把多币种 cash 桶 + 多市场持仓估值汇总到账户 ``base_currency`` 时用。

设计：

- **本地可解析的不打网络**：同币种（``CNY``→``CNY``）/ USD 等价稳定币
  （``USDT``→``USD``）直接 1.0。crypto-only 账户（base USD，币种 {USD, USDT}）
  全本地解析，零网络——既快又让单元测试 / data 服务不可用时不退化。
- **其余调 data ``/fx``**：缓存汇率（同一次快照内每币种只查一次）。
- **FX 拿不到不静默**：把该币种标 ``fx_warning`` 并**排除**出折算（宁可漏算不乱猜，
  金融时效硬约束）。stale 汇率仍用但附 warning。
"""
from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from .data_client import DataClient, DataServiceError

# USD 等价稳定币：互转视为 1.0（与 data 服务 fx.py 的 _STABLE_USD 保持一致）。
# BUSD：Binance/Paxos 已于 2024 年初下架，保留仅作向后兼容（模拟盘忽略脱锚 / 流通性风险）。
_STABLE_USD: frozenset[str] = frozenset({"USD", "USDT", "USDC", "BUSD", "DAI"})


def _local_rate(currency: str, base: str) -> Decimal | None:
    """本地可确定的汇率（无需网络）；不确定返 None。"""
    c, b = currency.strip().upper(), base.strip().upper()
    if c == b:
        return Decimal(1)
    if c in _STABLE_USD and b in _STABLE_USD:
        return Decimal(1)
    return None


def needs_network(currencies: Iterable[str], base_currency: str) -> bool:
    """是否存在本地解析不了、需调 data ``/fx`` 的币种。

    单币种 / crypto-USD 账户全本地可解 → False，``/accounts/me`` 无需开 DataClient
    （省一次 httpx 客户端构造，也让 data 服务不可用时这类账户不退化）。
    """
    return any(_local_rate(c, base_currency) is None for c in currencies)


class BaseCurrencyConverter:
    """把多币种金额折算到 ``base_currency``，缓存汇率 + 收集 fx_warnings。"""

    def __init__(self, base_currency: str, data_client: DataClient | None) -> None:
        self._base = base_currency.strip().upper()
        self._dc = data_client
        self._cache: dict[str, Decimal | None] = {}
        self._warnings: dict[str, str] = {}  # currency → reason（去重）

    async def rate(self, currency: str) -> Decimal | None:
        """1 单位 ``currency`` 折算成多少 ``base``；拿不到返 None（并记 warning）。"""
        c = currency.strip().upper()
        if c in self._cache:
            return self._cache[c]

        r = _local_rate(c, self._base)
        if r is None:
            if self._dc is None:
                self._warn(c, f"FX {c}/{self._base} 不可用（无 data 连接）")
            else:
                try:
                    resp = await self._dc.get_fx(base=c, quote=self._base)
                    r = Decimal(str(resp["rate"]))
                    if resp.get("is_stale"):
                        self._warn(
                            c,
                            f"FX {c}/{self._base} 数据偏旧（{resp.get('stale_seconds')}s 前），"
                            "估值可能不准",
                        )
                except DataServiceError as e:
                    self._warn(c, f"FX {c}/{self._base} 不可用（{e.code}），该币种已从估值排除")

        self._cache[c] = r
        return r

    async def convert(self, amount: Decimal, currency: str) -> Decimal | None:
        """折算 ``amount`` (``currency``) → base；汇率拿不到返 None（已记 warning）。"""
        r = await self.rate(currency)
        return None if r is None else amount * r

    @property
    def base(self) -> str:
        """折算目标货币(构造时定);调用方核对预取 converter 与锁内账户 base 一致用。"""
        return self._base

    def offline_copy(self) -> BaseCurrencyConverter:
        """复制一个**不打网络**的 converter(带走已缓存汇率)。

        购买力守门的"事务内权威复检"用:乐观预检阶段已把涉及币种的汇率预取进缓存,
        复检发生在 DB 行锁事务内——绝不能在持锁时发 HTTP。cache miss 的新币种(锁内
        才出现的桶,极端罕见)按 FX 不可用处理(排除 + warning),fail-closed 不猜。
        """
        c = BaseCurrencyConverter(self._base, None)
        c._cache = dict(self._cache)
        return c

    def _warn(self, currency: str, reason: str) -> None:
        self._warnings.setdefault(currency, reason)

    @property
    def warnings(self) -> list[str]:
        """fx_warnings 文案列表（每币种一条，去重）。"""
        return list(self._warnings.values())


async def convert_cash_balances(
    converter: BaseCurrencyConverter, cash_balances: dict[str, Decimal]
) -> Decimal:
    """把多币种现金桶折算到 base 求和(拿不到汇率的桶排除,warning 已记在 converter)。

    ``/accounts/me`` 快照与 spot BUY 购买力守门共用,保证两处"总可用现金"口径一致。
    """
    total = Decimal(0)
    for cur, amt in cash_balances.items():
        converted = await converter.convert(amt, cur)
        if converted is not None:
            total += converted
    return total
