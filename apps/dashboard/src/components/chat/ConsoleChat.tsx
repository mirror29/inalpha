"use client";

import { CopilotKit } from "@copilotkit/react-core";
import { MessageSquare } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import { ChatThread } from "./ChatThread";

const LS_OPEN = "inalpha-chat-open";
const LS_THREAD = "inalpha-chat-thread";
const LS_WIDTH = "inalpha-chat-width";

/** 对话栏宽度区间(px)—— 拖动调宽时 clamp 到此范围。 */
export const CHAT_MIN_WIDTH = 320;
export const CHAT_MAX_WIDTH = 720;
const CHAT_DEFAULT_WIDTH = 400;

/**
 * 操作者控制台内嵌 agent 对话栏 —— 右下角会话钮 + 右侧滑出栏。
 *
 * 设计:
 *  - 挂在 `[locale]/layout.tsx`,App Router 同段路由切换**不重挂载** → 切各切面时对话不丢。
 *  - **非浮层**:打开时给 `<html>` 打 `data-chat-open` + `--chat-w`,globals.css 让 `<main>`
 *    让出右侧宽度并 reflow;页面其余部分照常可滚动 / 可点击 / 可切面。
 *  - **可拖宽**:对话栏左缘的分隔条可拖动,对话栏与主内容同步变宽/变窄(宽度持久化)。
 *  - **默认打开**:首次访问即展开,除非用户手动关过(localStorage 记忆)。
 *  - 通过 AG-UI 走 `/api/copilotkit` → mastra orchestrator(headless 自渲染,见 ChatThread)。
 *
 * SSR 安全:`threadId` / `open` / `width` 在 mount 后从 localStorage 读,首帧返回 null,
 * 避免 hydration mismatch;`threadId` 就绪后才挂 CopilotKit provider。
 */
export function ConsoleChat() {
  const t = useTranslations("chat");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [width, setWidth] = useState(CHAT_DEFAULT_WIDTH);
  const [dragging, setDragging] = useState(false);

  // mount 后恢复持久化状态;首次无 thread 则生成稳定 id,且**默认打开**。
  useEffect(() => {
    let id = localStorage.getItem(LS_THREAD);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(LS_THREAD, id);
    }
    setThreadId(id);
    setOpen(localStorage.getItem(LS_OPEN) !== "0"); // 默认开,除非显式存过 "0"
    const w = Number(localStorage.getItem(LS_WIDTH));
    if (w >= CHAT_MIN_WIDTH && w <= CHAT_MAX_WIDTH) setWidth(w);
  }, []);

  // open / width 变化:持久化 + 驱动 main reflow(globals.css 读 data-chat-open + --chat-w)。
  useEffect(() => {
    if (threadId === null) return; // 尚未 mount 完成,别误写
    localStorage.setItem(LS_OPEN, open ? "1" : "0");
    const root = document.documentElement;
    if (open) {
      root.dataset.chatOpen = "true";
      root.style.setProperty("--chat-w", `${width}px`);
    } else {
      delete root.dataset.chatOpen;
    }
    return () => {
      delete root.dataset.chatOpen;
    };
  }, [open, width, threadId]);

  // 拖动中:打 data-chat-dragging,让 main 关过渡跟手。
  useEffect(() => {
    const root = document.documentElement;
    if (dragging) root.dataset.chatDragging = "true";
    else delete root.dataset.chatDragging;
  }, [dragging]);

  const close = useCallback(() => setOpen(false), []);

  const onWidthChange = useCallback((px: number) => {
    const w = Math.min(Math.max(px, CHAT_MIN_WIDTH), CHAT_MAX_WIDTH);
    setWidth(w);
    localStorage.setItem(LS_WIDTH, String(w));
  }, []);

  // 新建会话:换一个 threadId,CopilotKit 切到全新上下文(ChatThread 会清空 UI)。
  const onNewSession = useCallback(() => {
    const id = crypto.randomUUID();
    localStorage.setItem(LS_THREAD, id);
    setThreadId(id);
  }, []);

  // 切到历史会话:换 threadId,ChatThread 监听到变化后回填该会话消息。
  const onSwitchThread = useCallback((id: string) => {
    localStorage.setItem(LS_THREAD, id);
    setThreadId(id);
  }, []);

  // 外部(底部活动日志点某条会话)请求打开并切到指定会话。
  useEffect(() => {
    const handler = (e: Event) => {
      const id = (e as CustomEvent<{ threadId?: string }>).detail?.threadId;
      if (!id) return;
      localStorage.setItem(LS_THREAD, id);
      setThreadId(id);
      setOpen(true);
    };
    window.addEventListener("inalpha:open-chat", handler);
    return () => window.removeEventListener("inalpha:open-chat", handler);
  }, []);

  if (threadId === null) return null;

  return (
    <CopilotKit
      runtimeUrl="/api/copilotkit"
      agent="orchestrator"
      threadId={threadId}
      showDevConsole={false}
      enableInspector={false}
    >
      {/* 右下角会话钮 —— 仅关闭态显示(打开态由栏内标题的关闭钮收起)。 */}
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label={t("open")}
          title={t("open")}
          style={{ bottom: "calc(var(--activity-h, 0px) + 1.25rem)" }}
          className="seal-glow fixed right-6 z-40 flex size-12 items-center justify-center rounded-xl border border-seal/40 bg-bg-elev/90 text-seal backdrop-blur-sm transition-transform duration-200 hover:scale-105 motion-reduce:transition-none"
        >
          <MessageSquare className="size-5" strokeWidth={1.75} />
        </button>
      )}

      <ChatThread
        open={open}
        width={width}
        threadId={threadId}
        onClose={close}
        onWidthChange={onWidthChange}
        onDragChange={setDragging}
        onNewSession={onNewSession}
        onSwitchThread={onSwitchThread}
      />
    </CopilotKit>
  );
}
