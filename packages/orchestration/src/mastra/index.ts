/**
 * Mastra 实例 —— ``mastra dev`` 自动发现的入口。
 *
 * 启动：
 *
 *   pnpm dev           # http://localhost:4111 playground
 *
 * 需要：
 *
 * - services/data + services/paper 都在跑（D-3 / D-6）
 * - DEEPSEEK_API_KEY 在 .env
 * - JWT_SECRET 跟 services 一致
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

import { resolveMastraDbDir, resolveOrchestrationRoot } from "./paths.js";

// dev 启动时显式加载 package 根 .env。注意不能按 cwd 解析：mastra dev 的 server
// 子进程 cwd 是 src/mastra/public/（此前靠 CLI 父进程 env 继承碰巧生效）。
const envPath = resolve(resolveOrchestrationRoot(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { Mastra } from "@mastra/core/mastra";
import { LibSQLStore } from "@mastra/libsql";
import type { MiddlewareHandler } from "hono";
import { PinoLogger } from "@mastra/loggers";
import {
  ConsoleExporter,
  MastraStorageExporter,
  Observability,
  SamplingStrategyType,
} from "@mastra/observability";

import { verifyToken } from "../auth.js";
import { getSettings } from "../config.js";
import { AUTH_SUB_KEY } from "../hooks/with-hooks.js";
import { divinationApiRoutes } from "../divination/api.js";
import { closePool as closeDivinationPool } from "../divination/repo.js";
import { permissionsApiRoutes } from "../permissions/api.js";
import { pendingApprovals } from "../permissions/pending.js";
import {
  closePool as closeApprovalsPool,
  sweepStalePending,
} from "../permissions/repo.js";
import { schedulerApiRoutes } from "../scheduler/api.js";
import { bootstrapScheduler } from "../scheduler/index.js";
import { orchestrator } from "./agents/orchestrator.js";
import { helloSpikeWorkflow } from "./workflows/_hello.js";
import { backtestGridWorkflow } from "./workflows/backtest-grid.js";
import { factorDiscoveryWorkflow } from "./workflows/factor-discovery.js";

// D-9：observability storage —— traces UI tab 必需。LibSQL file DB 零运维。
// 路径与 memory 库同目录（package 根 .data/，cwd 无关，见 paths.ts）。
const observabilityStore = new LibSQLStore({
  id: "inalpha-traces",
  url: `file:${resolveMastraDbDir()}/inalpha-traces.db`,
});

// D-9：observability —— 追踪 agent / tool / workflow 全链路。
// 双 exporter：ConsoleExporter（stdout 实时打印 span）+ MastraStorageExporter（落 storage
// 让 playground "Traces" tab 能加载历史）。prod 可换 OTLP exporter → Jaeger / Tempo。
const observability = new Observability({
  configs: {
    default: {
      serviceName: "inalpha-orchestration",
      sampling: { type: SamplingStrategyType.ALWAYS },
      exporters: [new ConsoleExporter(), new MastraStorageExporter()],
    },
  },
});

/**
 * 身份注入 middleware（#91 多租户审批隔离根治）。
 *
 * 从 ``Authorization: Bearer <jwt>`` 解出 ``sub`` → 写进 Mastra ``requestContext`` 的
 * **自定义 key ``AUTH_SUB_KEY``** → ``withHooks.defaultGetSessionId`` 最高优先读
 * ``requestContext[AUTH_SUB_KEY]`` → askCache 按**已认证主体** scope，替代不稳定的
 * ``__global__`` fallback（promote 审批不再跨用户越权）。
 *
 * **为何用自定义 key 而非 ``MASTRA_RESOURCE_ID_KEY``**：后者会牵动 Mastra Memory
 * （要求 resourceId+threadId 成对，单设 resourceId 会让无 threadId 的 generate 直接 500，
 * spike 实测踩到）。自定义 key 只供 askCache scope，与 Memory 的 resourceId 机制**完全解耦**。
 *
 * - 单租户 dev：sub 恒为 console subject → scope 稳定且唯一（行为等价 __global__，但显式）。
 * - 多租户：dashboard 给每用户发各自 JWT → 自动按用户隔离，无需再改 askCache。
 * - 无 / 非法 / 过期 token：不注入，沿用既有 fallback，绝不阻断请求（审批门有后端硬校验兜底）。
 */
/** 进程内仅 warn 一次 requestContext 缺失 / Bearer 签名失败（避免每请求刷屏），见 identityMiddleware。 */
let _warnedNoRequestContext = false;
let _warnedAuthSignature = false;

const identityMiddleware: MiddlewareHandler = async (c, next) => {
  try {
    const authz = c.req.header("Authorization");
    const token = authz?.startsWith("Bearer ") ? authz.slice(7).trim() : undefined;
    if (token) {
      const payload = await verifyToken(token);
      const sub = typeof payload.sub === "string" && payload.sub ? payload.sub : undefined;
      if (sub) {
        const rc = c.get("requestContext") as { set?: (k: string, v: unknown) => void } | undefined;
        if (typeof rc?.set === "function") {
          rc.set(AUTH_SUB_KEY, sub);
        } else if (!_warnedNoRequestContext) {
          // 防御性可观测：rc 缺失则已验证的 sub 被丢、askCache 静默落回 __global__、#91 隔离
          // 悄然复现。Mastra 升级若改了中间件初始化顺序最可能触发——进程内 warn 一次即够定位。
          _warnedNoRequestContext = true;
          console.warn(
            "[identity-mw] requestContext 不可用（无 .set）——authSub 未注入，askCache 落回 " +
              "__global__；Mastra 升级后请复查中间件与 requestContext 初始化顺序（#91 隔离失效）。",
          );
        }
      }
    }
  } catch (err) {
    // 过期 / 格式错 token 是正常用户行为（高频）→ 静默沿用 fallback，不阻断、不刷屏。
    // **只对签名验证失败**（ERR_JWS_SIGNATURE_VERIFICATION_FAILED）进程内 warn 一次：受信
    // dashboard→mastra 链路下签名失败基本=JWT_SECRET 配错，若系统性配错则每请求都触发、
    // 第一笔即告警，#91 隔离静默失效可见。把告警配额留给"配错"信号、不被高频过期 token
    // 提前耗掉（CR #96 round3：旧 _warnedAuthFailure 会被过期 token 先消耗）。
    const code = (err as { code?: unknown } | null)?.code;
    if (code === "ERR_JWS_SIGNATURE_VERIFICATION_FAILED" && !_warnedAuthSignature) {
      _warnedAuthSignature = true;
      console.warn(
        "[identity-mw] Bearer 签名验证失败（多半 JWT_SECRET 配错）——authSub 未注入、" +
          "askCache 落回 __global__、多租户 #91 隔离静默失效，请排查 JWT_SECRET。",
      );
    }
  }
  await next();
};

export const mastra = new Mastra({
  storage: observabilityStore,
  // D-8a'：只剩 orchestrator 一个 agent；trader/risk subagent 已废弃
  // 安全护栏从"agent prompt + tool 集隔离"下沉到"plan store + permissions deny"
  agents: { orchestrator },
  // ADR-0025 spike：hello_spike 验证 Mastra 1.36 workflow API（保留作为活的 API 参考）
  // ADR-0025 §D3：backtest_grid Swarm S1
  workflows: {
    hello_spike: helloSpikeWorkflow,
    backtest_grid: backtestGridWorkflow,
    // D-12 · 因子发现 L1：批量验证表达式（强制 BH 校正 + 冗余剪枝 + 故事门）
    factor_discovery: factorDiscoveryWorkflow,
  },
  logger: new PinoLogger({ name: "inalpha", level: "info" }),
  observability,
  // D-9：scheduler HTTP 管理面 + D-9.1b：permissions ask 审批通道
  // 两套路由共用 4111 端口
  //
  // timeout：Mastra 网关请求超时（ms）。默认 180s 会在 research.deep_dive 长任务
  // （多 analyst + persona 大师团 + 辩论，单次可达 3-5min）未返回前就 504 掉，agent
  // 白等。设 600s 让网关 ≥ ResearchClient 自身 300s 超时 —— 网关不会比工具更早掐断
  // （ADR-0037 调试记录）。
  server: {
    timeout: 600_000,
    middleware: identityMiddleware,
    apiRoutes: [...schedulerApiRoutes, ...permissionsApiRoutes, ...divinationApiRoutes],
  },
});

// D-9：类 Hermes 定时 agent 模式。默认关闭，需在 .env 设 `SCHEDULER_ENABLED=true` 才启动。
// 避免本地 dev 反复触发污染 paper 账户；进程退出时 SIGTERM/SIGINT hook 自动释放 advisory lock。
if (getSettings().schedulerEnabled) {
  bootstrapScheduler(mastra);
}

// D-12：审批审计扫尾 —— 上一进程遗留的 pending 行置 expired_restart（等待方已随
// 进程死亡，不可恢复，落终态只为 dashboard 可见）。DB 不可用时静默跳过，不阻断启动。
void sweepStalePending().then((n) => {
  if (n > 0) console.log(`[approvals] 启动扫尾：${n} 条遗留挂起审批置 expired_restart`);
});

// 进程优雅退出(SIGTERM/SIGINT)时,把在途 ask 审批 fail-closed 地 deny 掉:agent 的
// await 拿到干净的 deny + telemetry 留痕,而不是被进程终止静默切断。
// 注意:这些挂起本就随进程消失不可恢复(等待方在内存里),deny 只让关停语义干净,
// 不是「持久化待审批」——后者无意义(重启后没有 await 方可被 resolve)。
let _pendingShutdownHooked = false;
function hookPendingApprovalsShutdown(): void {
  if (_pendingShutdownHooked) return;
  _pendingShutdownHooked = true;
  const drain = (): void => {
    pendingApprovals.clearAll("deny");
    // divination/repo 懒建的独立 pg.Pool(max:4)也一并释放,否则高频重启时
    // Postgres 端会留一批 idle 连接到 idle_in_transaction 超时,易顶满 max_connections。
    void closeDivinationPool().catch(() => {});
    void closeApprovalsPool().catch(() => {});
  };
  process.once("SIGTERM", drain);
  process.once("SIGINT", drain);
}
hookPendingApprovalsShutdown();
