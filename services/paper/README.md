# services/paper

回测 / 模拟盘 / 实盘三合一引擎。

## D-4 范围（本轮）：纯内存内核

本轮**只**有内核抽象，**没有** Gateway / Engine / HTTP / 数据库接入。可单元测试，
无外部依赖。

```
src/quant_lab_paper/
├── kernel/
│   ├── clock.py         Clock (ABC) + LiveClock + TestClock
│   ├── msgbus.py        MessageBus (pub/sub + endpoint，wildcard 匹配)
│   └── identifiers.py   InstrumentId / ClientOrderId / VenueOrderId / StrategyId
├── model/
│   ├── data.py          QuoteTick / TradeTick / Bar （含 data_epoch，ADR-0013）
│   ├── orders.py        Order + 7 状态机 (NEW → ... → FILLED/CANCELED/REJECTED)
│   ├── positions.py     Position （含 generation，ADR-0013 CAS）
│   ├── events.py        OrderEvent / PositionEvent 不可变事件
│   └── commands.py      SubmitOrderCommand / CancelOrderCommand
└── strategy/
    ├── actor.py         数据订阅 + 生命周期回调
    └── base.py          Strategy (extends Actor) 加下单接口
```

## 设计来源

- **借鉴 Nautilus**：Clock 抽象 + MessageBus pub/sub + endpoint 双形态 +
  `ts_event` / `ts_init` 双时间戳（见 [refs/nautilus.md §3 §4 §5](../../docs/refs/nautilus.md)）
- **借鉴 vnpy**：Gateway 抽象（后续 D-5 起）+ 全局拼接 ID 约定
- **ADR-0013 落地**：`QuoteTick`/`Bar` 带 `data_epoch`，`Position` 带 `generation`

## 后续 D-5 / D-6

- **D-5**：Gateway 抽象 + SimulatedExchange + Engine (Backtest/Live) + FastAPI 入口
- **D-6**：第一个 SMA cross 策略 + 端到端：data → backtest → fill → position

## 开发

```bash
cd services/paper
uv sync --group dev
uv run pytest         # 应全部通过（纯内存，不需要 DB）
uv run ruff check src tests
uv run mypy src
```
