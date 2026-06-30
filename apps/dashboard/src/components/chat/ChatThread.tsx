"use client";

import { useCopilotChatInternal } from "@copilotkit/react-core";
import {
  History,
  MapPin,
  SendHorizontal,
  SquarePen,
  Square,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

import { cn } from "@/lib/cn";
import {
  buildPageContextEnvelope,
  stripPageContext,
  usePageContext,
} from "@/lib/page-context";
import { DivinationCard } from "@/components/divination/DivinationCard";
import { isDivinationTool, parseDivination } from "@/components/divination/types";
import { ChatMarkdown } from "./ChatMarkdown";
import { ToolOutput } from "./ToolOutput";
import { resolveToolView } from "./tool-views";

/** AG-UI 消息(@ag-ui/core)的最小形态 —— 只取渲染需要的字段。 */
type AGMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool" | "reasoning" | string;
  content?: unknown;
  toolCalls?: { id: string; function?: { name?: string; arguments?: string } }[];
  toolCallId?: string;
};

/** AG-UI content 兼容 string / 多模态数组 —— 抽出可显示纯文本。 */
function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) =>
        p && typeof p === "object" && "text" in p ? String(p.text ?? "") : "",
      )
      .join("");
  }
  return "";
}

/** 历史会话摘要(来自 /api/chat/threads)。 */
interface ThreadSummary {
  id: string;
  title: string | null;
  updatedAt: string;
}

/**
 * 滑出对话栏(headless 自渲染)。
 *
 * CopilotKit 1.59 是 AG-UI「agent.messages」模型:用 `useCopilotChatInternal()`(非 cloud-gated)
 * 读 `messages` / 发 `sendMessage` / 中断 `stopGeneration` / 回填 `setMessages`。
 *
 * 会话管理:`threadId` 由父组件(ConsoleChat)持有并驱动 `<CopilotKit threadId>`;
 *  - 新建会话 → 父组件换 threadId → 本组件监听到变化拉空消息回填(清空 UI)。
 *  - 切历史会话 → 父组件换 threadId → 本组件拉 `/api/chat/threads/:id/messages` 回填。
 *
 * 完全套用「印章终端」主题:用户气泡(电光青右对齐)/ agent 气泡(左对齐)/ 工具调用
 * 内联 chip(金=调用、绿=结果),均 `.rise` 入场;流式时底部 `caret-blink` 光标。
 *
 * @param open 是否展开(驱动 translate-x 滑入)
 * @param width 当前栏宽(px),由左缘分隔条拖动调整
 * @param threadId 当前会话 ID(变化即触发回填)
 * @param freshThreads 本地「新建会话」刚生成的 threadId 集合 —— 必然无历史,跳过回填 fetch
 * @param onClose 收起回调
 * @param onWidthChange 拖动时上报新宽度(父组件 clamp + 持久化 + 驱动 main reflow)
 * @param onDragChange 拖动开始/结束(父组件打 data-chat-dragging 关 main 过渡)
 * @param onNewSession 新建会话
 * @param onSwitchThread 切到指定历史会话
 */
export function ChatThread({
  open,
  width,
  threadId,
  freshThreads,
  onClose,
  onWidthChange,
  onDragChange,
  onNewSession,
  onSwitchThread,
}: {
  open: boolean;
  width: number;
  threadId: string;
  freshThreads?: Set<string>;
  onClose: () => void;
  onWidthChange: (px: number) => void;
  onDragChange: (dragging: boolean) => void;
  onNewSession: () => void;
  onSwitchThread: (id: string) => void;
}) {
  const t = useTranslations("chat");
  // ⚠️ `useCopilotChatInternal` 是 CopilotKit 内部 hook(名字含 Internal),跨大版本无稳定性保证。
  // 升级 CopilotKit(>1.59.x)时必须验证此 hook 仍存在、且 messages / sendMessage /
  // stopGeneration / setMessages / agent 字段签名一致,否则会静默变 undefined 致对话栏失效。
  // CI 已固定 @copilotkit/* 到 1.59.5 + override @ag-ui/client 0.0.53(见 pnpm-workspace.yaml)。
  // DivinationClient 也用了同一 hook,升级时一并验。
  const hook = useCopilotChatInternal();
  const messages = (hook.messages ?? []) as unknown as AGMessage[];
  const { sendMessage, setMessages, isLoading, stopGeneration } = hook;
  const [draft, setDraft] = useState("");
  // 当前页面上下文(随路由更新)+ 用户是否临时摘掉本页上下文。
  // 摘掉只对「当前这页」生效 —— 一旦导航到别的页(kind/id 变化)即恢复默认带上。
  const page = usePageContext();
  const [contextDismissed, setContextDismissed] = useState(false);
  useEffect(() => {
    setContextDismissed(false);
  }, [page.kind, page.id]);
  const contextAttached = !contextDismissed;
  // 切会话回填历史消息的在途态 —— 与「思考中」(agent 生成中)区分:切 thread 时 CopilotKit
  // 会重连 agent(connectAgent → isRunning=true),若此时只看 isLoading 会把「正在拉历史」
  // 误显示成「思考中」,且历史还没回填 → 满屏只有「思考中」。见下方回填 effect 与消息区渲染。
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const loadedThreadRef = useRef<string | null>(null);
  // 持最新 setMessages —— 回填 effect 只依赖 threadId,不把 setMessages 进依赖:它来自
  // useCopilotChatInternal,agent 重连/流式时身份会变,若进依赖会让回填 effect 反复 cleanup
  // 重跑;而重跑因 loadedThreadRef 去重提前 return,**原 fetch 的 finally 被 cancelled 跳过
  // → historyLoading 永远卡 true、栏内一直显示「加载历史会话…」**。用 ref 断开这条链。
  const setMessagesRef = useRef(setMessages);
  setMessagesRef.current = setMessages;
  // 本轮 run 期间所有在途 `/api/copilotkit` 请求的中止器 + "正在停止"标志(见 handleStop)。
  const inflightAborts = useRef<Set<AbortController>>(new Set());
  const stoppingRef = useRef(false);

  // 外部(占卜台「去对话栏深聊此卦」)请求把某卦交给 agent 解读 —— 注入一条用户消息。
  // 用 ref 持最新 sendMessage,监听器只挂一次,避免每次渲染重绑。开栏由 ConsoleChat 监听同一事件。
  const sendRef = useRef(sendMessage);
  sendRef.current = sendMessage;
  useEffect(() => {
    const handler = (e: Event) => {
      const prompt = (e as CustomEvent<{ prompt?: string }>).detail?.prompt;
      if (!prompt) return;
      void sendRef.current({
        id: crypto.randomUUID(),
        role: "user",
        content: prompt,
      } as Parameters<typeof sendMessage>[0]);
    };
    window.addEventListener("inalpha:divination-consult", handler);
    return () => window.removeEventListener("inalpha:divination-consult", handler);
  }, []);

  /**
   * 停止生成兜底 —— 修复"点暂停没反应,回复继续输出"。
   *
   * 当前 runtime 是 v1 GraphQL endpoint(`/api/copilotkit`):流式走 base 路径,但
   * CopilotKit 的 `stopGeneration → agent.abortRun` 会 POST 到
   * `/api/copilotkit/agent/<id>/stop/<thread>`(本 route 是固定路径,子路径 **404**),
   * 既没掐断本地流,也没让 run 收尾 → `agent.isRunning` 卡 true、`isLoading` 不复位、回复继续刷。
   *
   * 这里在 fetch 层登记本轮所有 copilotkit 请求的 AbortController(`isLoading` 落回 false 即清空),
   * 点"停止"时由 handleStop 一并 abort + 强制收尾。`stoppingRef` 期间到来的后续 tool 段请求立即掐掉。
   */
  useEffect(() => {
    const orig = window.fetch;
    // strict-mode(dev)双 mount:mount1 patch→unmount1 cleanup 还原 orig→mount2 见 orig 未被
    // patch、重新 patch,捕获的是 mount2 的 ref(= 当前活跃组件的 ref),stop 正常。下方 cleanup
    // 「仅自己仍是最外层时还原」保证这条还原链成立。若哪天 patch 无 cleanup,二次 mount 会因
    // __inalphaPatched 早返、旧闭包捕获旧 ref 致 dev 下 stop 失效 —— 故 cleanup 不可删。
    if ((orig as { __inalphaPatched?: boolean }).__inalphaPatched) return;
    const patched: typeof window.fetch = (input, init) => {
      let url = "";
      try {
        url =
          typeof input === "string"
            ? input
            : input instanceof URL
              ? input.href
              : (input as Request).url;
      } catch {
        url = "";
      }
      if (url.includes("/api/copilotkit")) {
        const ctrl = new AbortController();
        if (stoppingRef.current) ctrl.abort(); // 停止后到来的后续段直接掐掉
        inflightAborts.current.add(ctrl);
        const drop = () => inflightAborts.current.delete(ctrl);
        const signal =
          init?.signal && typeof AbortSignal.any === "function"
            ? AbortSignal.any([init.signal, ctrl.signal])
            : ctrl.signal;
        // 请求一结束就移出在途集合,防长 run(多工具段)内 Set 累积已完成的 ctrl。
        // **不能**在 fetch promise resolve(仅收到 header)时删 —— 流式响应 body 还在传,
        // 删早了 handleStop 就掐不断它;用 TransformStream.flush 探 body 真正读完。
        return orig(input, { ...init, signal }).then(
          (res) => {
            if (!res.body) {
              drop();
              return res;
            }
            const monitored = res.body.pipeThrough(
              new TransformStream({
                flush() {
                  drop();
                },
              }),
            );
            return new Response(monitored, {
              status: res.status,
              statusText: res.statusText,
              headers: res.headers,
            });
          },
          (err) => {
            drop();
            throw err;
          },
        );
      }
      return orig(input, init);
    };
    (patched as { __inalphaPatched?: boolean }).__inalphaPatched = true;
    window.fetch = patched;
    return () => {
      // 只在自己仍是最外层 fetch 时还原,避免撤掉之后别的模块叠加的 fetch 替换。
      if (window.fetch === patched) window.fetch = orig;
    };
  }, []);

  // 流式结束(isLoading 落回 false)→ 解除"正在停止"并清空在途集合,下一条消息正常发。
  useEffect(() => {
    if (!isLoading) {
      stoppingRef.current = false;
      inflightAborts.current.clear();
    }
  }, [isLoading]);

  // 订阅 agent run 错误(上游 LLM 报错 / 流中断)→ 在对话栏顶条红字提示,
  // 而不是只剩一个空助手气泡让人误以为是 dashboard 坏了(典型:LLM 余额不足 / 限流)。
  useEffect(() => {
    const agent = hook.agent;
    if (!agent) return;
    const sub = agent.subscribe({
      onRunErrorEvent: ({
        event,
      }: {
        event?: { message?: string; code?: string };
      }) => {
        const raw = event?.message;
        const code = event?.code;
        // 用户点"停止"或任何 abort 触发的报错不算错(掐断在途 fetch 必然抛
        // "BodyStreamBuffer was aborted"/AbortError)—— 别顶错误条。
        if (
          stoppingRef.current ||
          /abort|BodyStreamBuffer|signal is aborted/i.test(`${raw ?? ""} ${code ?? ""}`)
        ) {
          return;
        }
        // @ag-ui/mastra 把上游错误对象塞进 Error() 会变成 "[object Object]" —— 当无效信息丢弃。
        const human = raw && raw !== "[object Object]" ? raw : null;
        setChatError(
          human
            ? `${human}${code ? ` (${code})` : ""}`
            : code
              ? `${t("errorGeneric")} (${code})`
              : t("errorGeneric"),
        );
      },
    } as Parameters<typeof agent.subscribe>[0]);
    return () => sub.unsubscribe();
  }, [hook.agent, t]);

  /** 点"停止":掐断所有在途流式请求,并强制 run 收尾让 UI 立刻复位。 */
  const handleStop = () => {
    stoppingRef.current = true;
    setChatError(null); // 主动停止不是错误,清掉可能残留的错误条
    stopGeneration();
    inflightAborts.current.forEach((c) => c.abort());
    inflightAborts.current.clear();
    // 兜底:HTTP 已返回但 run 卡在 isRunning=true 时,直接收尾 + 触发订阅重渲染让 UI 复位。
    const agent = hook.agent as
      | { isRunning?: boolean; messages?: unknown[]; setMessages?: (m: unknown[]) => void }
      | undefined;
    if (agent) {
      agent.isRunning = false;
      agent.setMessages?.([...(agent.messages ?? [])]);
    }
    // 兜底:`stoppingRef` 平时靠 useEffect([isLoading]) 在 isLoading→false 时复位,而那条
    // 依赖上面 `agent.isRunning = false` 能触发 CopilotKit 重渲染让 isLoading 变 false。
    // 万一某版本 isLoading 来自别的内部信号、这条 mutation 不生效,stoppingRef 会永久卡 true,
    // 之后每条新消息都被 fetch patch 立即 abort。3s 后强制复位,不依赖 isLoading 这条链路。
    window.setTimeout(() => {
      stoppingRef.current = false;
    }, 3000);
  };

  // threadId 变化(新建 / 切换 / 刷新恢复)→ 回填该会话历史消息;新会话返回空即清空。
  // 回填期间打 historyLoading:消息区显示「加载历史会话…」而非误显示的「思考中」/ 上个会话残影。
  useEffect(() => {
    if (!threadId || loadedThreadRef.current === threadId) return;
    loadedThreadRef.current = threadId;
    // 「新建会话」刚生成的 thread 后端必然为空 —— 同步清空即可,不打 loading 不发请求
    // (否则点新建要等一次网络往返,后端慢时面板会闪「加载历史会话…」)。
    if (freshThreads?.has(threadId)) {
      setMessagesRef.current([] as never);
      return;
    }
    let cancelled = false;
    setHistoryLoading(true);
    fetch(`/api/chat/threads/${threadId}/messages`)
      .then((r) => (r.ok ? r.json() : { messages: [] }))
      .then((d: { messages?: { id: string; role: string; content: string }[] }) => {
        if (!cancelled) setMessagesRef.current((d.messages ?? []) as never);
      })
      .catch(() => {
        if (!cancelled) setMessagesRef.current([] as never);
      })
      .finally(() => {
        // 不加 cancelled 守卫:即便本 effect 被 cleanup,该 threadId 的回填确已结束,
        // loading 态必须落回 false,否则一旦 cleanup 抢在 finally 前就永久卡 true。
        setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, freshThreads]);

  // 重载当前会话历史。用于「在历史列表里点了已经激活的那条会话」：此时父组件 setThreadId
  // 值不变，上面的回填 effect（依赖 threadId）不会重跑 → 过去表现为「点第一条（恰是当前
  // 会话、被高亮）没反应」。这里手动再拉一次，给出可见反馈并复原到最新持久化状态。
  const reloadCurrentThread = useCallback(() => {
    const id = loadedThreadRef.current;
    if (!id) return;
    setHistoryLoading(true);
    fetch(`/api/chat/threads/${id}/messages`)
      // 非 2xx 返 null(不要 {messages:[]})——否则会把当前会话清空白(M-1)。
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { messages?: AGMessage[] } | null) => {
        // null(请求失败)或飞行途中已切到别的会话 → 不覆写,避免清空 / 旧数据串台(M-2)。
        if (!d || loadedThreadRef.current !== id) return;
        setMessagesRef.current((d.messages ?? []) as never);
      })
      .catch(() => {})
      .finally(() => setHistoryLoading(false));
  }, []);

  // 点开历史下拉:**保留上次列表立即展示**(不再清空回 loading 态),后台 no-store 重新拉、
  // 拿到再替换 —— 重开瞬间出内容,避免每次「思考中」白屏 +（标题持久化后）后端只剩一次
  // listMemoryThreads 调用。仅首次(threads===null)才显加载态。
  useEffect(() => {
    if (!historyOpen) return;
    let cancelled = false;
    setHistoryError(false);
    fetch("/api/chat/threads", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { threads?: ThreadSummary[]; sourceDown?: boolean }) => {
        if (cancelled) return;
        setThreads(d.threads ?? []);
        if (d.sourceDown) setHistoryError(true);
      })
      .catch(() => {
        if (!cancelled) setHistoryError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [historyOpen]);

  // toolCallId → tool 名(tool-result 消息只带 id,名字在前面的 assistant.toolCalls 里)。
  const toolNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of messages) {
      if (m.toolCalls)
        for (const c of m.toolCalls) map.set(c.id, c.function?.name ?? "tool");
    }
    return map;
  }, [messages]);

  // 已有结果(tool-result 消息)的 toolCallId —— 这些「调用中」chip 不再展示,只留「已完成」。
  const resolvedToolCallIds = useMemo(() => {
    const s = new Set<string>();
    for (const m of messages) {
      if (m.role === "tool" && m.toolCallId) s.add(m.toolCallId);
    }
    return s;
  }, [messages]);

  const visible = messages.filter(
    (m) => m.role === "user" || m.role === "assistant" || m.role === "tool",
  );

  // 新消息 / 流式增量到达时贴底。
  useLayoutEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, isLoading]);

  // 打开时聚焦输入框。
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || isLoading) return;
    const isFirst = messages.length === 0; // 该会话首条 → 用它当会话标题
    setChatError(null); // 重发即清掉上一条错误
    // 解除"正在停止":用户主动发新消息 = 不再是停止态,否则停止后 3s 窗口内这条会被
    // fetch patch 当作"停止后的后续段"静默 abort 掉(消息凭空消失、无任何反馈)。
    stoppingRef.current = false;
    setDraft("");
    // 带上页面上下文(若用户没摘掉):拼在 content 开头,让 agent 知道用户此刻在看哪个页面。
    // 标题仍用原始 text(见下),不被 envelope 污染;用户气泡渲染时由 stripPageContext 剥回原话。
    const content = contextAttached
      ? buildPageContextEnvelope(page) + text
      : text;
    // 首条消息:先等待标题落库,再发消息。setChatThreadTitle 内置 create 兜底
    // (线程不存在时创建带标题的线程),确保 8s 活动流轮询和页面切换时标题已就位。
    // 同机 loopback 通常 <100ms,用户无感;失败静默放弃不阻塞发送。
    if (isFirst && threadId) {
      await fetch(`/api/chat/threads/${threadId}/title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: text }),
      }).catch(() => {});
    }
    void sendMessage({
      id: crypto.randomUUID(),
      role: "user",
      content,
    } as Parameters<typeof sendMessage>[0]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  // 左缘分隔条拖动:窗口右侧到指针的距离即为栏宽。
  const startResize = (e: ReactPointerEvent) => {
    e.preventDefault();
    onDragChange(true);
    const move = (ev: PointerEvent) =>
      onWidthChange(window.innerWidth - ev.clientX);
    const up = () => {
      onDragChange(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <aside
      aria-hidden={!open}
      style={{ width: `${width}px`, maxWidth: "100vw" }}
      className={cn(
        "fixed right-0 top-0 z-30 flex h-dvh flex-col border-l border-border-subtle bg-bg-elev/95 backdrop-blur-md",
        "transition-transform duration-300 ease-[cubic-bezier(0.22,0.7,0.22,1)] motion-reduce:transition-none",
        open ? "translate-x-0" : "translate-x-full",
      )}
    >
      {/* 左缘拖动条 —— 调整栏宽,主内容同步 reflow。移动端满宽,无拖宽语义,隐藏。 */}
      <div
        onPointerDown={startResize}
        role="separator"
        aria-orientation="vertical"
        aria-label={t("resize")}
        className="group absolute left-0 top-0 z-10 hidden h-full w-2 -translate-x-1/2 cursor-col-resize touch-none before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-transparent before:transition-colors before:content-[''] hover:before:bg-cyan/60 lg:block"
      />
      {/* 标题条 —— PageHeader 同款:朱红刻度 + 编辑体标题 + 在线点。 */}
      <header className="flex items-center gap-3 border-b border-border-subtle px-4 py-3.5">
        <span className="h-5 w-1 shrink-0 rounded-full bg-seal" />
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <h2 className="font-display text-lg text-fg">{t("title")}</h2>
        </div>
        {/* 页面上下文胶囊 —— 透明地告诉用户「agent 此刻看到了哪个页面」,✕ 可临时摘掉。 */}
        {contextAttached && (
          <div
            title={`${t("context.viewing")} ${t(`context.kind.${page.kind}`)}${
              page.id ? ` · ${page.id.slice(0, 8)}` : ""
            }`}
            className="flex min-w-0 items-center gap-1 rounded-full border border-border-subtle bg-bg/60 py-0.5 pl-2 pr-1 text-[11px] text-fg-muted"
          >
            <MapPin className="size-3 shrink-0 text-cyan" strokeWidth={2} />
            <span className="truncate text-fg">
              {t(`context.kind.${page.kind}`)}
            </span>
            {page.id && (
              <span className="shrink-0 font-mono text-fg-muted/70 tabular-nums">
                {page.id.slice(0, 8)}
              </span>
            )}
            <button
              type="button"
              onClick={() => setContextDismissed(true)}
              aria-label={t("context.dismiss")}
              title={t("context.dismiss")}
              className="shrink-0 rounded-full p-0.5 text-fg-muted/70 transition-colors hover:bg-bg-elev/60 hover:text-fg"
            >
              <X className="size-3" strokeWidth={2} />
            </button>
          </div>
        )}
        <button
          type="button"
          onClick={onNewSession}
          aria-label={t("newSession")}
          title={t("newSession")}
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-bg/60 hover:text-cyan"
        >
          <SquarePen className="size-4" strokeWidth={1.75} />
        </button>
        <button
          type="button"
          onClick={() => setHistoryOpen((v) => !v)}
          aria-label={t("history")}
          title={t("history")}
          aria-expanded={historyOpen}
          className={cn(
            "rounded-md p-1.5 transition-colors hover:bg-bg/60 hover:text-fg",
            historyOpen ? "text-cyan" : "text-fg-muted",
          )}
        >
          <History className="size-4" strokeWidth={1.75} />
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("close")}
          title={t("close")}
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-bg/60 hover:text-fg"
        >
          <X className="size-4" strokeWidth={1.75} />
        </button>
      </header>

      {/* 历史会话下拉 */}
      {historyOpen && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setHistoryOpen(false)}
            aria-hidden
          />
          <div className="absolute right-3 top-14 z-20 max-h-80 w-64 overflow-y-auto rounded-lg border border-border-subtle bg-bg-elev shadow-xl">
            {historyError ? (
              <p className="px-3 py-3 text-xs text-fox-red">
                {t("historyError")}
              </p>
            ) : threads === null ? (
              <p className="px-3 py-3 font-mono text-xs text-fg-muted">
                {t("loadingHistory")}
              </p>
            ) : threads.length === 0 ? (
              <p className="px-3 py-3 text-xs text-fg-muted">
                {t("historyEmpty")}
              </p>
            ) : (
              threads.map((th) => (
                <button
                  key={th.id}
                  type="button"
                  onClick={() => {
                    // 点已激活的会话：threadId 不变，回填 effect 不会触发 → 手动重载;
                    // 点别的会话：照常切 threadId，由回填 effect 拉取。
                    if (th.id === threadId) reloadCurrentThread();
                    else onSwitchThread(th.id);
                    setHistoryOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 border-b border-border-subtle/60 px-3 py-2 text-left text-xs transition-colors last:border-b-0 hover:bg-bg/60",
                    th.id === threadId ? "bg-cyan/10 text-fg" : "text-fg-muted",
                  )}
                >
                  <span className="truncate">
                    {th.title || t("untitled")}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-fg-muted/70 tabular-nums">
                    {th.updatedAt.slice(0, 10)}
                  </span>
                </button>
              ))
            )}
          </div>
        </>
      )}

      {/* 消息区 */}
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {historyLoading ? (
          // 切会话回填中:整屏显示加载态(盖住上个会话残影 + agent 重连产生的「思考中」误判)。
          <div className="flex items-center gap-2 px-1 font-mono text-xs text-fg-muted">
            <span className="size-1.5 rounded-full bg-cyan caret-blink" />
            {t("loadingHistory")}
          </div>
        ) : visible.length === 0 ? (
          <p className="mt-8 px-2 text-center text-sm leading-relaxed text-fg-muted">
            {t("empty")}
          </p>
        ) : (
          visible.map((m) => (
            <MessageRow
              key={m.id}
              message={m}
              toolNames={toolNames}
              resolvedToolCallIds={resolvedToolCallIds}
              toolRunning={t("toolRunning")}
              toolDone={t("toolDone")}
              toolResultLabel={t("toolResult")}
            />
          ))
        )}
        {/* 「思考中」仅用于 agent 真正生成回复时,回填历史期间不展示(那是 connectAgent 误置 isRunning)。 */}
        {isLoading && !historyLoading && (
          <div className="flex items-center gap-2 px-1 font-mono text-xs text-fg-muted">
            <span className="size-1.5 rounded-full bg-cyan caret-blink" />
            {t("thinking")}
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* 错误条 —— 上游 LLM 报错 / 流中断时顶出来,不再是空气泡 */}
      {chatError && (
        <div className="mx-3 mb-2 flex items-start gap-2 rounded-lg border border-fox-red/40 bg-fox-red/10 px-3 py-2 text-xs text-fox-red">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" strokeWidth={2} />
          <span className="min-w-0 flex-1 break-words">{chatError}</span>
          <button
            type="button"
            onClick={() => setChatError(null)}
            aria-label={t("errorDismiss")}
            title={t("errorDismiss")}
            className="shrink-0 text-fox-red/70 transition-colors hover:text-fox-red"
          >
            <X className="size-3.5" strokeWidth={2} />
          </button>
        </div>
      )}

      {/* 输入区 */}
      <div className="border-t border-border-subtle p-3">
        <div className="flex items-end gap-2 rounded-lg border border-border-subtle bg-bg/60 px-3 py-2 transition-colors focus-within:border-cyan/50">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={t("placeholder")}
            className="max-h-32 flex-1 resize-none bg-transparent py-1 text-sm text-fg outline-none placeholder:text-fg-muted/60"
          />
          {isLoading ? (
            // 生成中:暂停钮 —— 中断当前回复。
            <button
              type="button"
              onClick={handleStop}
              aria-label={t("stop")}
              title={t("stop")}
              className="mb-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-fox-red text-bg-deep transition-opacity hover:opacity-90"
            >
              <Square className="size-3.5" strokeWidth={2} fill="currentColor" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={!draft.trim()}
              aria-label={t("send")}
              title={t("send")}
              className="mb-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-seal text-bg-deep transition-opacity hover:opacity-90 disabled:opacity-30"
            >
              <SendHorizontal className="size-4" strokeWidth={2} />
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}

/** 单条消息:用户 / agent 文本气泡 + 内联工具 chip。 */
function MessageRow({
  message,
  toolNames,
  resolvedToolCallIds,
  toolRunning,
  toolDone,
  toolResultLabel,
}: {
  message: AGMessage;
  toolNames: Map<string, string>;
  resolvedToolCallIds: Set<string>;
  toolRunning: string;
  toolDone: string;
  toolResultLabel: string;
}) {
  const text = textOf(message.content);

  if (message.role === "user") {
    // 剥掉随消息夹带的 <page_context> 块 —— 气泡只显示用户原话(新消息 + 历史回填同理)。
    return (
      <div className="rise flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-cyan/10 px-3 py-2 text-sm text-fg">
          {stripPageContext(text)}
        </div>
      </div>
    );
  }

  if (message.role === "tool") {
    const toolName = toolNames.get(message.toolCallId ?? "") ?? "tool";
    // 玄学 tool 结果渲染成卦象 / 塔罗卡片(解析失败回退普通 chip)。
    if (isDivinationTool(toolName)) {
      const reading = parseDivination(text);
      if (reading) {
        return (
          <div className="flex justify-start">
            <DivinationCard reading={reading} className="max-w-[95%]" />
          </div>
        );
      }
    }
    // 已完成 chip:展开只看**输出结果**(入参噪音大,按用户要求不展示)。
    return (
      <div className="rise flex justify-start">
        <ToolChip
          name={toolName}
          result={text}
          label={toolDone}
          resultLabel={toolResultLabel}
          done
        />
      </div>
    );
  }

  // assistant:可能同时带文本 + toolCalls。
  // 已出结果的 toolCall 不再展示「调用中」—— 由对应 tool-result 的「已完成」chip 接管。
  const calls = (message.toolCalls ?? []).filter(
    (c) => !resolvedToolCallIds.has(c.id),
  );
  if (!text && calls.length === 0) return null;

  return (
    <div className="rise flex flex-col items-start gap-1.5">
      {text && (
        <div className="max-w-[90%] break-words rounded-lg rounded-bl-sm bg-bg-deep/60 px-3 py-2 text-sm leading-relaxed text-fg">
          <ChatMarkdown>{text}</ChatMarkdown>
        </div>
      )}
      {calls.map((c) => (
        <ToolChip
          key={c.id}
          name={c.function?.name ?? "tool"}
          label={toolRunning}
          resultLabel={toolResultLabel}
        />
      ))}
    </div>
  );
}

/** JSON 字符串美化(两格缩进);不是合法 JSON 原样返回。 */
function pretty(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

/**
 * 工具调用 / 结果的紧凑 chip。
 * 调用中(金):仅 chip 标头;已完成(绿):展开看**输出结果**(入参不展示,按需看 raw)。
 * 输出按优先级渲染:工具专属视图(tool-views,行情卡/回测指标格等)→ 通用结构化
 * ({@link ToolOutput} 键值行/表格)→ raw 钮永远可切回原始 JSON。
 */
function ToolChip({
  name,
  label,
  resultLabel,
  result,
  done = false,
}: {
  name: string;
  label: string;
  resultLabel: string;
  result?: string;
  done?: boolean;
}) {
  const [showRaw, setShowRaw] = useState(false);
  // 工具专属视图:结果可解析且形态命中才有;否则 null 落回通用结构化视图。
  const view = useMemo(() => {
    if (!result) return null;
    try {
      return resolveToolView(name, JSON.parse(result));
    } catch {
      return null;
    }
  }, [name, result]);
  const sectionCaption =
    "px-2.5 pt-1.5 font-mono text-[9px] uppercase tracking-[0.18em] text-fg-muted/60";

  const head = (
    <>
      <Wrench
        className="size-3.5 shrink-0 transition-colors group-hover:text-cyan"
        strokeWidth={1.75}
      />
      <span className="truncate text-fg">{name}</span>
      <span className="ml-auto uppercase tracking-[0.12em] text-fg-muted/70 transition-colors group-hover:text-fg-muted">
        {label}
      </span>
    </>
  );

  // 调用中 / 无结果:没有可展开内容,渲染普通行,不给假的展开预期。
  if (!done || !result) {
    return (
      <div className="group flex w-full max-w-[90%] items-center gap-2 rounded-md border border-border-subtle bg-bg/40 px-2.5 py-1.5 font-mono text-xs text-gold">
        {head}
      </div>
    );
  }

  return (
    <details className="group w-full max-w-[90%] overflow-hidden rounded-md border border-border-subtle bg-bg/40 text-xs transition-colors hover:border-cyan/40 hover:bg-bg-elev/50">
      <summary
        className={cn(
          "flex items-center gap-2 px-2.5 py-1.5 font-mono transition-transform hover:translate-x-0.5 motion-reduce:transition-none",
          done ? "text-bull" : "text-gold",
        )}
      >
        {head}
      </summary>
      {done && result && (
        <div className="border-t border-border-subtle bg-bg-deep/50">
          <div className="flex items-baseline justify-between pr-2.5">
            <div className={sectionCaption}>{resultLabel}</div>
            <button
              type="button"
              onClick={() => setShowRaw((v) => !v)}
              className={cn(
                "rounded-sm px-1 font-mono text-[9px] uppercase tracking-[0.18em] transition-colors",
                showRaw
                  ? "text-cyan"
                  : "text-fg-muted/40 hover:text-fg-muted",
              )}
            >
              raw
            </button>
          </div>
          {showRaw ? (
            <pre className="max-h-64 overflow-auto px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-fg-muted">
              {pretty(result)}
            </pre>
          ) : view ? (
            <div className="max-h-72 overflow-auto px-2.5 py-1.5">{view}</div>
          ) : (
            <ToolOutput raw={result} />
          )}
        </div>
      )}
    </details>
  );
}
