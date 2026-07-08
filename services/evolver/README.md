# Inalpha 策略演化引擎 —— E1 单代闭环

## 架构

```
services/evolver/
├── pyproject.toml
├── src/inalpha_evolver/
│   ├── __init__.py          # 版本
│   ├── main.py              # FastAPI 入口（port 8003）
│   ├── config.py            # pydantic-settings 配置
│   ├── exceptions.py        # 统一异常定义
│   ├── api/
│   │   ├── routes.py        # POST /runs / GET /runs/{id} / GET /candidates/{id}
│   │   └── schemas.py       # Pydantic 请求/响应模型
│   ├── governor/
│   │   ├── loop.py          # run_one_generation 主循环
│   │   ├── seed.py          # SMACrossStrategy 种子策略源码
│   │   └── hint_generator.py # 4 条硬编码 hint 轮流
│   ├── mutator/
│   │   ├── diff_applier.py  # unified diff 应用（带 fuzz match）
│   │   ├── llm_client.py    # LLM 变异算子（包装 _shared/llm）
│   │   ├── mock_client.py   # Mock 变异客户端（测试用）
│   │   └── prompt_templates.py # ~5KB 静态 system prompt + user prompt 构建
│   ├── evaluator/
│   │   ├── runner.py        # 子进程回测评估器
│   │   └── fitness.py       # fitness 合成（薄封装 paper.compose_fitness）
│   ├── population/
│   │   ├── candidate.py     # 数据类（Candidate, EvolutionRun, EvaluationResult）
│   │   └── store.py         # DB 持久化（E1 占位，E2 实现）
│   └── sandbox/
│       ├── ast_audit.py     # 薄封装 paper.audit_strategy_code
│       └── contract_check.py # 薄封装 paper.verify_strategy_contract
└── tests/
    ├── test_mutator.py
    ├── test_evaluator.py
    ├── test_sandbox.py
    ├── test_population.py
    ├── test_governor.py
    └── test_e2e.py
```

## 依赖关系

```
services/_shared/llm/       (零项目内依赖)
    └── services/evolver/   (依赖 _shared + _shared/llm + paper)
```

## 复用 paper 模块

| 复用模块 | paper 路径 | evolver 中位置 |
|----------|-----------|----------------|
| audit_strategy_code | paper.strategy_authoring.ast_audit | sandbox/ast_audit.py |
| verify_strategy_contract | paper.strategy_authoring.contract_check | sandbox/contract_check.py |
| load_strategy_class | paper.strategy_authoring.dynamic_loader | sandbox/contract_check.py |
| compose_fitness | paper.strategy_authoring.fitness | evaluator/fitness.py |
| run_engine_in_subprocess | paper.runner | evaluator/runner.py |
| BacktestReport | paper.engine.report | evaluator/fitness.py |
| periods_per_year | paper.engine.metrics | evaluator/fitness.py |

## E1 验收标准

1. **闭环**：MockMutator + MockEvaluator → budget=4 → >=1 fitness>0 candidate
2. **沙盒有效**：5 种不安全场景全被拒
3. **fitness 多目标**：公式与手算一致
4. **回撤 veto**：DD>30% → -1e9
5. **FastAPI 可达**：3 端点返回正确状态码