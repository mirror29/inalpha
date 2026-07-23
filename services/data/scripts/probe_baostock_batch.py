"""手工探测 baostock 是否支持批量查询。

本文件不是 pytest 测试。需要访问真实 Baostock 网络时显式运行：

``uv run python scripts/probe_baostock_batch.py``
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import baostock as bs


def main() -> int:
    """运行单标的、多标的和分钟线探测，返回进程退出码。"""
    login = bs.login()
    if login.error_code != "0":
        print(f"登录失败: {login.error_msg}")
        return 1

    try:
        print("登录成功")
        _probe_single_symbol()
        count, stocks = _probe_multiple_symbols()
        minute_count, minute_stocks = _probe_minute_symbols()
    finally:
        bs.logout()
        print("\n登出成功")

    print("\n=== 结论 ===")
    if count > 0 and len(stocks) > 1 and minute_count > 0 and len(minute_stocks) > 1:
        print("✅ baostock 支持批量查询（多股票逗号分隔）")
        print("   可以在 1 次请求中获取多只股票数据，节省配额")
        return 0

    print("❌ baostock 不支持批量查询，需要单独查询每只股票")
    return 1


def _date_range(days: int) -> tuple[str, str]:
    """返回 Baostock 所需的起止日期字符串。"""
    now = datetime.now()
    return (now - timedelta(days=days)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")


def _probe_single_symbol() -> None:
    """探测单标的日线查询。"""
    print("\n=== 测试单股票查询 ===")
    start_date, end_date = _date_range(7)
    result = bs.query_history_k_data_plus(
        "sh.600519",
        "date,code,open,high,low,close,volume",
        start_date=start_date,
        end_date=end_date,
        frequency="d",
    )
    print(f"单股票查询结果: error_code={result.error_code}")
    count = 0
    while result.error_code == "0" and result.next():
        count += 1
        if count <= 3:
            print(f"  {result.get_row_data()}")
    print(f"  共 {count} 条数据")


def _probe_multiple_symbols() -> tuple[int, set[str]]:
    """探测多标的日线查询。"""
    print("\n=== 测试多股票查询（逗号分隔）===")
    start_date, end_date = _date_range(7)
    result = bs.query_history_k_data_plus(
        "sh.600519,sh.600036,sh.601318",
        "date,code,open,high,low,close,volume",
        start_date=start_date,
        end_date=end_date,
        frequency="d",
    )
    print(f"多股票查询结果: error_code={result.error_code}")
    return _consume_rows(result, code_index=1)


def _probe_minute_symbols() -> tuple[int, set[str]]:
    """探测多标的五分钟线查询。"""
    print("\n=== 测试分钟 K 线批量查询 ===")
    start_date, end_date = _date_range(1)
    result = bs.query_history_k_data_plus(
        "sh.600519,sh.600036",
        "date,time,code,open,high,low,close,volume",
        start_date=start_date,
        end_date=end_date,
        frequency="5",
    )
    print(f"分钟 K 线查询结果: error_code={result.error_code}")
    return _consume_rows(result, code_index=2)


def _consume_rows(result: Any, *, code_index: int) -> tuple[int, set[str]]:
    """消费 Baostock ResultData，打印样本并返回行数和标的集合。"""
    count = 0
    stocks: set[str] = set()
    while result.error_code == "0" and result.next():
        count += 1
        row = result.get_row_data()
        stocks.add(row[code_index])
        if count <= 5:
            print(f"  {row}")
    print(f"  共 {count} 条数据，涉及 {len(stocks)} 只股票: {stocks}")
    return count, stocks


if __name__ == "__main__":
    raise SystemExit(main())
