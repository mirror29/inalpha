"use client";

import { useCopilotChatInternal } from "@copilotkit/react-core";
import {
  History,
  SquarePen,
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
  usePageContext,
} from "@/lib/page-context";
import { ChatErrorBanner } from "./ChatErrorBanner";
import { ChatHistoryPanel, type ThreadSummary } from "./ChatHistoryPanel";
import { ChatInput } from "./ChatInput";
import { ChatMessageList } from "./ChatMessageList";
import type { AGMessage } from "./ChatMessage";

/**
 * 滑出对话栏（headless 自渲染）。
 *
 * CopilotKit 1.59 AG-UI「agent.messages」模型：用 `useCopilotChatInternal()` 读
 * `messages` / 发 `sendMessage` / 中断 `stopGeneration` / 回填 `setMessages`。
 *
 * 会话管理：`threadId` 由父组件(ConsoleChat)持有并驱动 `<CopilotKit threadId>`。
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

  const hook = useCopilotChatInternal();
  const messages = (hook.messages ?? []) as unknown as AGMessage[];
  const { sendMessage, setMessages, isLoading, stopGeneration } = hook;

  const [draft, setDraft] = useState("");
  const page = usePageContext();
  const [contextDismissed, setContextDismissed] = useState(false);
  useEffect(() => setContextDismissed(false), [page.kind, page.id]);
  const contextAttached = !contextDismissed;

  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const loadedThreadRef = useRef<string | null>(null);
  const setMessagesRef = useRef(setMessages);
  setMessagesRef.current = setMessages;

  const inflightAborts = useRef<Set<AbortController>>(new Set());
  const stoppingRef = useRef(false);

  // Divination event listener
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

  // Fetch patch: intercept /api/copilotkit requests for abort-on-stop
  useEffect(() => {
    const orig = window.fetch;
    if ((orig as { __inalphaPatched?: boolean }).__inalphaPatched) return;
    const patched: typeof window.fetch = (input, init) => {
      let url = "";
      try {
        url = typeof input === "string" ? input
          : input instanceof URL ? input.href
          : (input as Request).url;
      } catch { /* ignore */ }
      if (url.includes("/api/copilotkit")) {
        const ctrl = new AbortController();
        const signal = init?.signal;
        if (signal) signal.addEventListener("abort", () => ctrl.abort());
        // 停止后到来的后续 tool 段：用**已 abort 的 signal**发请求，
        // 浏览器立即以 AbortError 拒绝——这才真正掐断"点暂停后回复继续输出"。
        // （ctrl 必须挂到 init.signal 上，否则 abort 形同虚设。）
        if (stoppingRef.current) {
          ctrl.abort();
          return orig(input, { ...init, signal: ctrl.signal });
        }
        inflightAborts.current.add(ctrl);
        const drop = () => inflightAborts.current.delete(ctrl);
        return orig(input, { ...init, signal: ctrl.signal }).then((res) => {
          if (!res.ok || !res.body) { drop(); return res; }
          const monitored = res.body.pipeThrough(new TransformStream({ flush() { drop(); } }));
          return new Response(monitored, { status: res.status, statusText: res.statusText, headers: res.headers });
        }, (err) => { drop(); throw err; });
      }
      return orig(input, init);
    };
    (patched as { __inalphaPatched?: boolean }).__inalphaPatched = true;
    window.fetch = patched;
    return () => { if (window.fetch === patched) window.fetch = orig; };
  }, []);

  // Reset stopping state when loading ends
  useEffect(() => {
    if (!isLoading) { stoppingRef.current = false; inflightAborts.current.clear(); }
  }, [isLoading]);

  // Agent run error subscription
  useEffect(() => {
    const agent = hook.agent;
    if (!agent) return;
    const sub = agent.subscribe({
      onRunErrorEvent: ({ event }: { event?: { message?: string; code?: string } }) => {
        const raw = event?.message;
        const code = event?.code;
        if (stoppingRef.current || /abort|BodyStreamBuffer|signal is aborted/i.test(`${raw ?? ""} ${code ?? ""}`)) return;
        const human = raw && raw !== "[object Object]" ? raw : null;
        setChatError(human ? `${human}${code ? ` (${code})` : ""}` : code ? `${t("errorGeneric")} (${code})` : t("errorGeneric"));
      },
    } as Parameters<typeof agent.subscribe>[0]);
    return () => sub.unsubscribe();
  }, [hook.agent, t]);

  // Stop handler
  const handleStop = useCallback(() => {
    stoppingRef.current = true;
    setChatError(null);
    stopGeneration();
    inflightAborts.current.forEach((c) => c.abort());
    inflightAborts.current.clear();
    const agent = hook.agent as { isRunning?: boolean; messages?: unknown[]; setMessages?: (m: unknown[]) => void } | undefined;
    if (agent) { agent.isRunning = false; agent.setMessages?.([...(agent.messages ?? [])]); }
    window.setTimeout(() => { stoppingRef.current = false; }, 3000);
  }, [stopGeneration, hook.agent]);

  // History backfill on threadId change
  useEffect(() => {
    if (!threadId || loadedThreadRef.current === threadId) return;
    loadedThreadRef.current = threadId;
    if (freshThreads?.has(threadId)) { setMessagesRef.current([] as never); return; }
    let cancelled = false;
    setHistoryLoading(true);
    fetch(`/api/chat/threads/${threadId}/messages`)
      .then((r) => (r.ok ? r.json() : { messages: [] }))
      .then((d: { messages?: { id: string; role: string; content: string }[] }) => {
        if (!cancelled) setMessagesRef.current((d.messages ?? []) as never);
      })
      .catch(() => { if (!cancelled) setMessagesRef.current([] as never); })
      .finally(() => setHistoryLoading(false));
    return () => { cancelled = true; };
  }, [threadId, freshThreads]);

  const reloadCurrentThread = useCallback(() => {
    const id = loadedThreadRef.current;
    if (!id) return;
    setHistoryLoading(true);
    fetch(`/api/chat/threads/${id}/messages`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { messages?: AGMessage[] } | null) => {
        if (!d || loadedThreadRef.current !== id) return;
        setMessagesRef.current((d.messages ?? []) as never);
      })
      .catch(() => {})
      .finally(() => setHistoryLoading(false));
  }, []);

  // History list fetch
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
      .catch(() => { if (!cancelled) setHistoryError(true); });
    return () => { cancelled = true; };
  }, [historyOpen]);

  // Tool name/id lookups
  const toolNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of messages) {
      if (m.toolCalls) for (const c of m.toolCalls) map.set(c.id, c.function?.name ?? "tool");
    }
    return map;
  }, [messages]);

  const resolvedToolCallIds = useMemo(() => {
    const s = new Set<string>();
    for (const m of messages) if (m.role === "tool" && m.toolCallId) s.add(m.toolCallId);
    return s;
  }, [messages]);

  // 聚焦输入框由 ChatInput 内部按 open prop 驱动（Phase 3 拆分后 textarea 归 ChatInput 管）。

  // Submit message
  const submit = useCallback(async () => {
    const text = draft.trim();
    if (!text || isLoading) return;
    const isFirst = messages.length === 0;
    setChatError(null);
    stoppingRef.current = false;
    setDraft("");
    const content = contextAttached ? buildPageContextEnvelope(page) + text : text;
    if (isFirst && threadId) {
      await fetch(`/api/chat/threads/${threadId}/title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: text }),
      }).catch(() => {});
    }
    void sendMessage({ id: crypto.randomUUID(), role: "user", content } as Parameters<typeof sendMessage>[0]);
  }, [draft, isLoading, messages.length, contextAttached, page, threadId, sendMessage]);

  // Resize handler
  const startResize = useCallback((e: ReactPointerEvent) => {
    e.preventDefault();
    onDragChange(true);
    const move = (ev: PointerEvent) => onWidthChange(window.innerWidth - ev.clientX);
    const up = () => {
      onDragChange(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [onWidthChange, onDragChange]);

  const onHistorySwitch = useCallback((id: string) => {
    onSwitchThread(id);
    setHistoryOpen(false);
  }, [onSwitchThread]);

  const onHistoryReload = useCallback((id: string) => {
    reloadCurrentThread();
    setHistoryOpen(false);
  }, [reloadCurrentThread]);

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
      {/* Resize handle */}
      <div
        onPointerDown={startResize}
        role="separator"
        aria-orientation="vertical"
        aria-label={t("resize")}
        className="group absolute left-0 top-0 z-10 hidden h-full w-2 -translate-x-1/2 cursor-col-resize touch-none before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-transparent before:transition-colors before:content-[''] hover:before:bg-cyan/60 lg:block"
      />

      {/* Header bar */}
      <header className="relative flex items-center gap-3 border-b border-border-subtle px-4 py-3.5">
        <span className="h-5 w-1 shrink-0 rounded-full bg-seal" />
        <div className="flex min-w-0 flex-1 items-baseline gap-2">
          <h2 className="font-display text-lg text-fg">{t("title")}</h2>
        </div>
        <button
          type="button" onClick={onNewSession}
          aria-label={t("newSession")} title={t("newSession")}
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-bg/60 hover:text-cyan"
        >
          <SquarePen className="size-4" strokeWidth={1.75} />
        </button>
        <button
          type="button" onClick={() => setHistoryOpen((v) => !v)}
          aria-label={t("history")} title={t("history")} aria-expanded={historyOpen}
          className={cn("rounded-md p-1.5 transition-colors hover:bg-bg/60 hover:text-fg", historyOpen ? "text-cyan" : "text-fg-muted")}
        >
          <History className="size-4" strokeWidth={1.75} />
        </button>
        <button
          type="button" onClick={onClose}
          aria-label={t("close")} title={t("close")}
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-bg/60 hover:text-fg"
        >
          <X className="size-4" strokeWidth={1.75} />
        </button>

        {/* History dropdown */}
        <ChatHistoryPanel
          open={historyOpen}
          threads={threads}
          historyError={historyError}
          currentThreadId={threadId}
          sourceDownLabel={t("historyError")}
          untitledLabel={t("untitled")}
          onSwitch={onHistorySwitch}
          onReload={onHistoryReload}
        />
      </header>

      {/* Messages */}
      <ChatMessageList
        messages={messages}
        toolNames={toolNames}
        resolvedToolCallIds={resolvedToolCallIds}
        isLoading={isLoading}
        historyLoading={historyLoading}
        toolDone={t("toolDone")}
        toolResultLabel={t("toolResult")}
        emptyLabel={t("empty")}
        thinkingLabel={t("thinking")}
        loadingHistoryLabel={t("loadingHistory")}
      />

      {/* Error banner */}
      {chatError && (
        <ChatErrorBanner
          error={chatError}
          onDismiss={() => setChatError(null)}
          dismissLabel={t("errorDismiss")}
        />
      )}

      {/* Input area */}
      <ChatInput
        draft={draft}
        isLoading={isLoading}
        open={open}
        contextAttached={contextAttached}
        contextKind={page.kind}
        contextId={page.id}
        onDraftChange={setDraft}
        onSubmit={submit}
        onStop={handleStop}
        onContextDismiss={() => setContextDismissed(true)}
      />
    </aside>
  );
}
