# 04 · 当前状态与下一步

> 更新：2026-10-14。本页维护最新结论、证据边界和唯一优先级清单；历史采样与部署日志见 [截至本轮的存档](validation/current-state-history-through-2026-10-14.md) 和各项回执。

## 当前结论

Inalpha 是 AI agent 编排与多 Python kernel 的量化实验框架。D-12 与 E2 闭环代码已落地，**完整 E2 工程验收与真实正向生产验收都尚未完成**，不新增 Phase 完成结论。

- **E1 已生产跑通审批→生成→刷新读取**：真实候选与种子适应度相同，验证段零成交，未证明改善。见 [正常 owner 执行回执](validation/evolution-approval-execution-lineage-2026-10-10.json)。
- **E2 当前不选择启动资产**：最近可靠 owner 免费矩阵采样为 `2026-10-14T07:55:26.260Z`，BTC / ETH / SOL 独立事件上界为 **3/8、0/8、0/8**。未提交费用审批或付费搜索。匹配对照、FDR 和研究结果未评估。见 [owner 预检](validation/e2-owner-free-matrix-followup-2026-10-14.json)。
- **执行阻断已修复并合并，线上仍待发布**：PR #242 的 `a275f8be` 补 campaign 调度使用身份；357 项本地测试通过、1 跳过，精确 head/main CI 与七镜像成功。自动 CR 的1项MEDIUM已按完整消费者核对回应，独立复核无确认新增阻断。发布被活跃连接守卫拦截，原 Evolver `c55a5efe` / image `25874208` 保持 healthy、内部投影仍缺该字段；服务器仓库更新和镜像缓存不等于服务已替换。见 [发布与守卫回执](validation/e2-campaign-identity-release-2026-10-14.json)。
- **同镜像两项完整 Evolver 隔离检查已通过**：已保存响应在强杀后复用、未知响应在充足预算下拒绝自动重发。采用正式业务镜像、本地 AMD64 架构模拟和外围 HTTP/provider 夹具，不能替代正常 owner、完整外围服务栈或生产恢复证据。

下单仍必须经过机器审批与风控。演化不会自动 promote、启动 Runner 或下单；最终采用必须由用户明确选择。

## 输入与数据质量

| 资产 | 选择段事实 | 独立事件上界 | 当前动作 |
|---|---:|---:|---|
| BTC | 21 | 3 / 8 | 不启动搜索 |
| ETH | 0 | 0 / 8 | 不启动搜索 |
| SOL | 0 | 0 / 8 | 不启动搜索 |

上述数字只对应07:55采样的正常建议窗口；截止时点推进会移动自然60/20/20分段，不能直接与旧窗口作回归或因果比较。最新闭合1h末根开盘06:00、收盘07:00，与要求末根差值0；这不表示墙钟年龄为0。SOL选择段变为10/09 06:00至10/11 18:00，此次0/8不能归因于数据回退。

[04:05采样回执](validation/e2-owner-post-confirmation-2026-10-14.json)中BTC旧监管质量提示消失，07:55未展开质量面板复核；语义准确性仍未评估。原文数量、事实版本、24小时独立上界与实际匹配对照是不同口径；上界达到8也只具备必要条件。

- v3抽取器已修复ban误命中bank等缺陷；历史v2保持不可变。可信Research→Data审核已逐条实测一条跨类型纠正和两条BTC同类确认，原哈希、证据与有效时间保留，纠正按数据库当前可见时间追加。不是全部历史事实审核通过，普通owner无全局事实写权限。见 [单条纠正](validation/event-fact-single-production-2026-10-14.json) / [BTC确认](validation/event-fact-btc-confirmation-2026-10-14.json)。
- 运行版本曾有OCI标签与tag不一致；三Python业务包200源码文件的独立比对范围不含共享包、依赖或客户端bundle，不能据标签认定整个镜像可复现。见 [运行源码核对](validation/e2-runtime-source-parity-2026-10-14.json)。
- 旧冻结窗口不能靠未来事件补齐。新实验必须独立准备、重新预检并审批，不根据收益试窗，不回溯首次可见时间。

## 验收证据分层

| 环节 | 代码 / 自动化 | 完整隔离服务证据 | 正常 owner / 生产证据 |
|---|---|---|---|
| 新实验准备、覆盖矩阵、配置失效 | 已实现并有测试 | 相关数据库与路由测试 | 中文矩阵、英文桌面准备、目标参数保留和UI修改使旧结果隐藏；移动和服务端旧审批拒绝仍待核验 |
| 冻结审批与执行身份 | 已实现；内部身份遗漏已修复 | 镜像导入、公开schema排除字段及真实Evolver调用路径检查 | E1已通过；新E2审批/执行尚未通过，修复部署待安全窗口 |
| 模型响应已存、整代提案未存时退出 | 已有维护中的回归 | 同镜像完整Evolver五代恢复通过，离线调用2+8，120实现，合成已知费用0.000780、预留0；FDR不足拒绝 | 未取得真实模型响应恢复或owner五代证据 |
| 在途响应未知时退出 | 已有费用/响应回归 | 同镜像重启及再次重启均停在`LOOP_PROPOSAL_RESPONSE_UNCONFIRMED`；调用总数2，未知预留0.0340608保持，额度1充足 | 未取得真实供应商对账与生产未知费用恢复证据 |
| 其他故障、交接与一次性holdout | 已有相关状态机与自动化 | 部分源码进程/受控数据库证据；完整矩阵待补 | 生产恢复、冠军与长期正向链路未完成 |

已将双进程探索整理为 [固定镜像接手工具](../services/evolver/scripts/validate_campaign_takeover.py)，新增镜像/连接边界与清理超时、已退出竞争和夹具契约9项回归。最终同镜像运行先以相同reservation/参数在有效租约下完成并回滚写入作为对照，再在新租约仍为`replaying`且未过期时，直接旧token调用`proposal_responses.complete()`收到`LOOP_AUTHORIZATION_UNAVAILABLE`；预留0.0340608、非空响应0和阶段保持不变。两容器退出0，env删除及独立Docker清理确认；9项测试、Ruff/格式、一致性19/0/12通过。见 [维护工具回执](validation/e2-maintained-takeover-validator-2026-10-14.json)。这是实际存储调用拒绝证据，不是旧SDK回调收包、完整外围授权栈或生产fencing通过；其他故障场景尚未整理为维护套件。

同镜像双进程补充：旧应用SIGSTOP后租约自然过期，新应用接手并拒绝未知响应重发；恢复旧应用、释放离线响应后连续20秒观察费用预留、零非空响应、零提案及阶段均未变化。未直接采样旧SDK成功收包或旧complete()调用，不能区分心跳取消与写入fencing；仍不视为整个旧worker矩阵完成。见 [暂停worker接手回执](validation/e2-paused-worker-takeover-2026-10-14.json)。

同镜像回执：[已知响应恢复](validation/e2-same-image-response-recovery-2026-10-14.json) / [未知响应阻断](validation/e2-same-image-unknown-response-2026-10-14.json)。无效DSL使用既有回退，成功实现数不代表有效变异或改善；两项都是临时探索脚本，尚非CI维护套件。前期失败与观察端超时保留在回执，不删除审计库。

已有中文/英文桌面UI证据见 [owner语言与参数检查](validation/e2-owner-matrix-language-2026-10-14.json)。工具读取超时和Mac锁定只限定观察范围，不能记为产品通过或失败。

## 未完成 / 下一步

### P0 · 发布与正常用户入口

1. 在自然空闲窗口仅部署固定业务`a275f8be`的Evolver：复核数据库目标/schema、活动任务和实际HTTP连接；有连接即停止，不关闭用户连接或中断在途任务。保留600配置/数据库备份及精确旧image ID回滚；不因文档SHA重建。最近只读连接归属观察为Dashboard，不能将TCP空队列视为空闲。
2. 继续核对BTC/ETH/SOL正常建议窗口、来源失败、资产映射、抽取与去重。只据实际缺口增加来源，按真实首次可见时间积累；不重复搜索已知不足的输入。
3. 补中文/英文移动交互、普通owner隔离与配置修改后的服务端审批失效；确认旧实验不被改写。输入具备必要条件后才冻结费用审批。

### P0 · 可靠性矩阵与真实五代

4. 把探索脚本整理为可维护验收工具，继续同版本完整服务验证：重复点击/请求重发、批准响应丢失、持久化批准命令恢复与授权过期、显式失败retry、新attempt关联、租约失效后的旧worker返回、超额度/超大小响应、冠军交接响应丢失、holdout重复消费。保留现有状态机，区分同镜像Evolver夹具与完整外围授权栈。
5. 输入真实充分且正常owner授权可用时，按独立上界降序、同分BTC→ETH→SOL选择一个资产，冻结种子、窗口、数据摘要、模型与预算，批准一次运行有界五代。记录有效变异、重复代码、匹配对照、FDR、拒绝原因、耗时与费用。无冠军是有效结果，不能为通过重复搜索相同输入。
6. 聊天、生成、修复与未知费用分列，失败不记零；未知响应不能证明未计费时禁止自动重发或释放预留。已取消旧自动跟进0.20美元上限，逐任务冻结预算与有界修复仍有效。独立单文件Mypy检查的既有错误不能称通过，正式CI与本地调用范围分开记录。

### P0 · 冠军后的真实长期验证

7. 正式评估通过后锁冠军并幂等交接同一Forward sandbox；至少30个真实日及3个独立事件后执行一次性holdout。保留通过或拒绝结论，不用测试时钟替代生产证据，不反向调整同一封存实验。
8. 证据具备采用资格后由用户明确决定；不自动promote、启动Runner或下单。现在尚无真实冠军或Forward起点。

### P1 · 以真实结果优化

- 校准事件覆盖、语义质量、消融、FDR、交易样本和费用；首屏明确“改进谁、当前阶段、阻塞和下一步”。
- 统一任务、审批、attempt、费用与阶段交接诊断，内部ID收进详情；设计安全维护流以解决页面轮询占用连接的运维阻塞，当前发布边界保持不变。
- MAP-Elites、Island Model和更多agent扩展后置，先取得闭环证据。

## 其他项目待办

- **delegation hop**（issue #5 · ADR-0012 补丁）：sub-strategy 派生计划的转授权链

> 已收口：paper live runner（#1，D-11）、PnL 净口径 / 运行时长 TTL / build 退避（#45 /
> #44 / #41，D-11.2）、RiskEngine 接入（#3）、权限 YAML 化（#4）、askUserChoice（#2）、
> trade_repo 默认化 + 全市场交易日历（#8）。

live runner follow-up（非阻塞，待开）：轮询不感知交易时段（#48，休市空轮询浪费）、
LIMIT 单不跨 bar 挂单（#47，当前即时 IOC 语义）；session 持仓 resume（#37.2）、
#38 Phase F（沙盒子进程强化隔离；Evolver owner-key 凭证已按用途、owner 与 `config_id` 收窄）。

多市场日历 follow-up（非阻塞）：盘前 / 盘后时段、指数映射表补全、深交所 XSHE /
印度 XNSE 精确化（当前分别复用 XSHG / XBOM）。

因子库 follow-up（非阻塞，D-12 显式不做）：衰减告警→自动反思回测（仍人工复盘）、
自动衰减剔除（仅 ic_null_benchmark 透出）、活动流 factor_decay event kind 前端高亮、
横截面因子 / Polars 迁移（ADR-0031 欠账不变）、因子发现 L2 多 agent 小组（**L2/L3 路线
经 2026-07 业界调研后已重新规划，见 ADR-0055**：原"多 agent 小组"推迟至演化闭环之后，
优先级调整为 P0 策略回测闭环 → P1 多样性控制 → P2 算子/数据扩展 → P3 因子演化闭环）。

防过拟合 / PIT follow-up（非阻塞，2026-06-18 显式不做）：**PIT 阶段 B/C**（bars 表加 PIT
元数据列 + 复权、指数成分股快照，ADR-0053）；**横截面 / 跨品种验证**（多标的 panel 地基 +
`run_panel_backtest` OOS 分布 + 解锁 `needs_universe` 横截面因子，ADR-0054 已写 ADR、代码未起）；
CPCV 的 PBO（grid 多配置层）+ forward-looking 玩具策略 graduation 验证（ADR-0028）。

perp follow-up（非阻塞）：跨仓保证金聚合（当前只校单笔 IM，#114）、回测接逐根真实历史
funding（当前常数，#115）、空头移动止损 / 止盈 + 穿仓保险基金（多头已有、空头仅硬止损 + 强平）。

---


## 相关文档

- [项目边界](00-context.md) · [架构](01-architecture-overview.md) · [内核设计](03-kernel-design.md)
- [Evolver API与授权](../services/evolver/README.md) · [AI协作入口](../AGENTS.md) · [CLAUDE.md](../CLAUDE.md)
- [本轮完整历史状态](validation/current-state-history-through-2026-10-14.md) · [10月10日前历史](validation/current-state-history-through-2026-10-10.md)
