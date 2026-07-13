"""测试 baostock 是否支持批量查询（多股票逗号分隔）"""
from datetime import datetime, timedelta

import baostock as bs

# 登录
lg = bs.login()
if lg.error_code != "0":
    print(f"登录失败: {lg.error_msg}")
    exit(1)

print("登录成功")

# 测试单股票查询
print("\n=== 测试单股票查询 ===")
rs = bs.query_history_k_data_plus(
    "sh.600519",  # 贵州茅台
    "date,code,open,high,low,close,volume",
    start_date=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
    end_date=datetime.now().strftime("%Y-%m-%d"),
    frequency="d",
)

print(f"单股票查询结果: error_code={rs.error_code}")
count = 0
while (rs.error_code == "0") & rs.next():
    count += 1
    if count <= 3:
        print(f"  {rs.get_row_data()}")
print(f"  共 {count} 条数据")

# 测试多股票查询（逗号分隔）
print("\n=== 测试多股票查询（逗号分隔）===")
rs = bs.query_history_k_data_plus(
    "sh.600519,sh.600036,sh.601318",  # 茅台 + 招行 + 平安
    "date,code,open,high,low,close,volume",
    start_date=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
    end_date=datetime.now().strftime("%Y-%m-%d"),
    frequency="d",
)

print(f"多股票查询结果: error_code={rs.error_code}")
count = 0
stocks = set()
while (rs.error_code == "0") & rs.next():
    count += 1
    row = rs.get_row_data()
    stocks.add(row[1])  # code 字段
    if count <= 5:
        print(f"  {row}")
print(f"  共 {count} 条数据，涉及 {len(stocks)} 只股票: {stocks}")

# 测试分钟 K 线批量查询
print("\n=== 测试分钟 K 线批量查询 ===")
rs = bs.query_history_k_data_plus(
    "sh.600519,sh.600036",  # 茅台 + 招行
    "date,time,code,open,high,low,close,volume",
    start_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
    end_date=datetime.now().strftime("%Y-%m-%d"),
    frequency="5",  # 5 分钟
)

print(f"分钟 K 线查询结果: error_code={rs.error_code}")
count = 0
stocks = set()
while (rs.error_code == "0") & rs.next():
    count += 1
    row = rs.get_row_data()
    stocks.add(row[2])  # code 字段（分钟 K 线是第 3 列）
    if count <= 5:
        print(f"  {row}")
print(f"  共 {count} 条数据，涉及 {len(stocks)} 只股票: {stocks}")

# 登出
bs.logout()
print("\n登出成功")

# 结论
print("\n=== 结论 ===")
if count > 0 and len(stocks) > 1:
    print("✅ baostock 支持批量查询（多股票逗号分隔）")
    print("   可以在 1 次请求中获取多只股票数据，节省配额")
else:
    print("❌ baostock 不支持批量查询，需要单独查询每只股票")
