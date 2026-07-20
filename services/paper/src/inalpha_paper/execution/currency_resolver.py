"""``(venue, symbol)`` → 计价货币（quote currency）解析。

D-11 跨币种 cash model：一个账户可同时持有不同市场的标的（BTC/USDT、AAPL、
sh.600519），各自计价货币不同（USDT / USD / CNY）。要把账户总权益折算到 base
currency，先得知道每个 cash 桶 / 持仓的计价货币。

实现复用 ``risk_rules.exchange_resolver``：

- **crypto**（binance / coinbase / …）：取 symbol 的 quote（``BTC/USDT`` → ``USDT``；
  无 ``/`` 默认 ``USDT``）——交易所现金以 quote 计价
- **非 crypto**：复用 :func:`resolve_calendar_code` 把 ``(venue, symbol)`` 解析成
  交易所 calendar code，再映射到该交易所的本币（``XNYS`` → USD、``XSHG`` → CNY …）
- **fred / 未识别 / 未知标的**：fail-open 返 ``default``（base currency），不折算

注：``USDT`` 视作 ``USD`` 的近似（FX 端点把 ``USDT/USD`` 静态记 1.0）；模拟盘可接受
稳定币脱锚风险忽略。calendar code → 货币的映射来源同 ``exchange_resolver`` 的市场约定。
"""
from __future__ import annotations

from .risk_rules.exchange_resolver import is_crypto_venue, resolve_calendar_code

# exchange_calendars calendar code → 该交易所本币
# XSHG 复用于沪深（沪深均 CNY）；XBOM 复用于印度 NSE/BSE（均 INR）
_CALENDAR_CODE_TO_CURRENCY: dict[str, str] = {
    "XNYS": "USD",
    "XSHG": "CNY",
    "XHKG": "HKD",
    "XTKS": "JPY",
    "XLON": "GBP",
    "XFRA": "EUR",
    "XPAR": "EUR",
    "XKRX": "KRW",
    "XASX": "AUD",
    "XBOM": "INR",
    "XTSE": "CAD",
    "BVMF": "BRL",
}

# crypto symbol 无 quote 时的默认计价货币
_DEFAULT_CRYPTO_QUOTE = "USDT"

# 账户现金桶允许的币种全集:各市场本币 + USD 稳定币(与 fx._STABLE_USD 一致)。
# 充值端点用它做白名单——任意字符串建桶会造出 FX 永远折算不了的垃圾桶,
# 常驻 fx_warnings 且无法删除(无 withdraw 端点)。
KNOWN_CASH_CURRENCIES: frozenset[str] = (
    frozenset(_CALENDAR_CODE_TO_CURRENCY.values())
    | frozenset({"USD", "USDT", "USDC", "BUSD", "DAI"})
)


def _crypto_quote(symbol: str) -> str:
    """从 crypto symbol 取 quote 货币：``BTC/USDT`` → ``USDT``；无 ``/`` 兜底 USDT。

    兼容 ccxt 永续记法 ``BTC/USDT:USDT``：``/`` 后是 ``USDT:USDT``,再剥 ``:`` 后的结算币
    取计价币 → ``USDT``（否则会误得 ``USDT:USDT`` 桶,与现货 USDT 桶割裂）。
    """
    s = symbol.strip().upper()
    if "/" in s:
        quote = s.split("/", 1)[1].strip().split(":", 1)[0].strip()
        return quote or _DEFAULT_CRYPTO_QUOTE
    return _DEFAULT_CRYPTO_QUOTE


def resolve_currency(venue: str, symbol: str, *, default: str = "USD") -> str:
    """把 ``(venue, symbol)`` 解析成计价货币（ISO 4217 code，crypto 为 quote 资产）。

    Args:
        venue: ``InstrumentId.venue``（数据源标识，如 ``binance`` / ``yfinance`` / ``baostock``）。
        symbol: 标的代码（``BTC/USDT`` / ``AAPL`` / ``sh.600519`` / ``005930.KS`` / ``^N225``）。
        default: 解析不出时的兜底货币（通常传账户 base currency）。

    Returns:
        计价货币 code（``USD`` / ``CNY`` / ``HKD`` / ``USDT`` …）；fred / 未识别 → ``default``。
    """
    if is_crypto_venue(venue):
        return _crypto_quote(symbol)
    code = resolve_calendar_code(venue, symbol)
    if code is not None:
        return _CALENDAR_CODE_TO_CURRENCY.get(code, default)
    return default  # fred / 未识别 venue / 未知标的 → fail-open


__all__ = ["KNOWN_CASH_CURRENCIES", "resolve_currency"]
