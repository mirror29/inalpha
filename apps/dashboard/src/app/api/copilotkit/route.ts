import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
  type AgentsConfig,
} from "@copilotkit/runtime";
import { getRemoteAgents } from "@ag-ui/mastra";
import { MastraClient } from "@mastra/client-js";
import { NextResponse } from "next/server";

import { BACKENDS, getServiceToken, getSessionSubject } from "@/lib/backend";
import {
  decryptActiveUserApiKey,
  validateUserLLMConfig,
} from "@/lib/user-preferences";

/**
 * 静音 @ag-ui/mastra 1.0.3 的良性日志噪音:它不认 mastra(v5 streamVNext)的
 * `text-start` / `text-end` 文本块标记,每条消息都 `console.warn` 一次
 * "[MastraAgent] Unrecognized stream chunk type: ..."。文本内容走 `text-delta`(它认),
 * 渲染不受影响 —— 纯噪音。模块加载时一次性包一层 console.warn 过滤掉,幂等。
 */
const _warn = console.warn.bind(console) as typeof console.warn;
if (!(console.warn as { __inalphaFiltered?: boolean }).__inalphaFiltered) {
  const filtered = ((...args: unknown[]) => {
    if (
      typeof args[0] === "string" &&
      args[0].includes("[MastraAgent] Unrecognized stream chunk type")
    ) {
      return;
    }
    _warn(...(args as Parameters<typeof console.warn>));
  }) as typeof console.warn & { __inalphaFiltered?: boolean };
  filtered.__inalphaFiltered = true;
  console.warn = filtered;
}

/**
 * CopilotKit ↔ Mastra 桥（AG-UI 协议）。
 *
 * 链路:dashboard `<CopilotKit runtimeUrl="/api/copilotkit">` → 本 route →
 *      `@ag-ui/mastra` remote agent → mastra orchestrator(4111,`agent.stream`)。
 *
 * 设计要点:
 *  1. **remote agent**(指向 4111)而非把 mastra 实例 import 进 Next.js —— 两个服务解耦,
 *     dashboard 不背 mastra 依赖树。
 *  2. **每请求**重建 runtime,从而每次拿新鲜 JWT(`getServiceToken` 进程内缓存,到期前 60s 续签),
 *     规避长连接里 token 过期。
 *  3. **隔离**:`getRemoteAgents` 强制 `resourceId`(= 登录用户 sub,经 getSessionSubject()
 *     从 session 派生;dev 未登录回落 console:dev),`threadId` 由前端 `<CopilotKit threadId>`
 *     传下并经 AG-UI 转发给 `agent.stream`,共同满足 memory.ts 的 `assertScopedRequest`。
 *  4. LLM 在 mastra 侧,本层不需要 model adapter —— 用 `ExperimentalEmptyAdapter` 占位。
 *
 * ⚠️ **已知版本约束**:`@ag-ui/mastra`(目前最新 1.0.3)的 peerDependencies 把
 *    `@copilotkit/runtime` 硬钉到一个 pre-release(`0.0.0-mme-ag-ui-0-0-46-*`),没有任何
 *    已发布版本对齐到稳定的 1.59.x —— 这是上游打包遗留,`pnpm install` 会有 unmet peer 告警。
 *    我们固定用**最新稳定** `@copilotkit/{runtime,react-core}@1.59.x` + `@ag-ui/mastra@1.0.3`:
 *    运行时只用到 `getRemoteAgents` / `AbstractAgent` 等稳定 API,实测发消息 / 切会话 / 历史回填
 *    / 停止生成均正常(停止生成的 v1/v2 路径差异已在 ChatThread `handleStop` 兜底)。升级前
 *    若 @ag-ui/mastra 发布了对齐 1.59.x 的版本,应优先换到官方兼容对。
 *
 * @returns CopilotKit runtime 的 POST handler
 */
export const POST = async (req: Request): Promise<Response> => {
  // token 的 sub = 登录用户(或 dev 下 console:dev)。mastra identityMiddleware 据此
  // 注入 authSub,tool 层再据此打给 Python(resolveRequestToken),保证 agent 写操作落登录用户账户。
  const token = await getServiceToken();
  const subject = await getSessionSubject();

  // 解析用户激活的 LLM 配置（多租户）。
  // 如果用户没有配置 API Key，返回特殊标记让前端弹窗。
  let userLLMConfig = null;
  try {
    userLLMConfig = await decryptActiveUserApiKey(subject);
  } catch (err) {
    console.error("[copilotkit] decryptActiveUserApiKey 失败:", err instanceof Error ? err.message : err);
  }

  // 如果用户没有配置 LLM，返回 428 让前端弹配置弹窗
  if (!userLLMConfig) {
    return NextResponse.json(
      { error: "NO_LLM_CONFIG", message: "请先配置 LLM API Key" },
      { status: 428 }
    );
  }

  const validation = await validateUserLLMConfig(userLLMConfig);
  if (!validation.valid) {
    console.warn("[copilotkit] 激活 LLM 配置鉴权失败:", validation.reason);
    return NextResponse.json(
      { error: "INVALID_LLM_CONFIG", message: "当前 LLM API Key 无效或暂时不可用，请检查激活配置" },
      { status: 401 },
    );
  }

  // 通过 custom header 传递用户 LLM 配置（含解密后的 API key）。
  // Mastra identity middleware 解析后写入 AsyncLocalStorage → agent 用 buildLLMForUser。
  // 容器内网传输，不经过公网（CF Tunnel 仅暴露 dashboard:3001，mastra:4111 不外露）。
  const llmConfigHeader = userLLMConfig
    ? JSON.stringify({
        id: userLLMConfig.id,
        provider: userLLMConfig.provider,
        model: userLLMConfig.model,
        api_key: userLLMConfig.api_key,
        custom_base_url: userLLMConfig.custom_base_url,
        custom_provider_name: userLLMConfig.custom_provider_name,
        label: userLLMConfig.label,
      })
    : "";

  console.log("[copilotkit] userLLMConfig:", userLLMConfig ? { id: userLLMConfig.id, provider: userLLMConfig.provider } : null);
  console.log("[copilotkit] llmConfigHeader length:", llmConfigHeader.length);

  const mastraClient = new MastraClient({
    baseUrl: BACKENDS.mastra,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(llmConfigHeader && { "X-LLM-Config": llmConfigHeader }),
    },
  });

  const agents = await getRemoteAgents({
    mastraClient,
    resourceId: subject, // resourceId = subject（用户隔离）
  });

  // @ag-ui/mastra 的远程 agent 会把 headers 塞进 modelSettings 传给 mastra
  // 我们直接在 agent 实例上设置 headers 属性，
  // 这样 remote agent 的 stream 方法调用时会把 headers 通过 modelSettings 传给 mastra
  if (llmConfigHeader) {
    for (const agent of Object.values(agents)) {
      const agentWithHeaders = agent as unknown as { headers?: Record<string, string> };
      if (agentWithHeaders) {
        agentWithHeaders.headers = {
          ...(agentWithHeaders.headers || {}),
          "X-LLM-Config": llmConfigHeader,
        };
      }
    }
  }

  // 打印 agent 的 headers 确认
  console.log("[copilotkit] agent headers set:", Object.values(agents).length, "agents");

  // CopilotKit 1.59.5 起 `agents` 收紧为 NonEmptyRecord | Promise | factory;
  // getRemoteAgents 返回的是普通 Record(类型上可能为空,运行期非空),按 AgentsConfig 收口。
  const runtime = new CopilotRuntime({ agents: agents as unknown as AgentsConfig });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: "/api/copilotkit",
  });

  return handleRequest(req);
};
