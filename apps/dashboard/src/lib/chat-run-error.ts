export interface ChatRunErrorEvent {
  code?: string;
  message?: string;
}

export interface ChatRunErrorMessages {
  generic: string;
  incompleteStream: string;
}

/**
 * 将 AG-UI 运行错误转换为可操作的用户提示，同时保留有意义的上游错误。
 */
export function formatChatRunError(
  event: ChatRunErrorEvent,
  messages: ChatRunErrorMessages,
): string {
  const raw = event.message?.trim();
  const code = event.code?.trim();
  const human = raw && raw !== "[object Object]" ? raw : null;

  if (human) return `${human}${code ? ` (${code})` : ""}`;
  if (code === "INCOMPLETE_STREAM") {
    return `${messages.incompleteStream} (${code})`;
  }
  return code ? `${messages.generic} (${code})` : messages.generic;
}


/** Handle both AG-UI error events and failed HTTP/transport runs. */
export function createChatErrorSubscriber(options: {
  isStopping: () => boolean;
  onError: (message: string) => void;
  messages: ChatRunErrorMessages;
}) {
  let handledRunError = false;

  return {
    onRunInitialized: () => { handledRunError = false; },
    onRunErrorEvent: ({ event }: { event?: ChatRunErrorEvent }) => {
      if (options.isStopping()) return;
      if (/abort|BodyStreamBuffer|signal is aborted/i.test(`${event?.message ?? ""} ${event?.code ?? ""}`)) {
        handledRunError = true;
        return;
      }
      handledRunError = Boolean(event?.code || event?.message);
      options.onError(formatChatRunError(event ?? {}, options.messages));
    },
    onRunFailed: (_params: { error: Error }) => {
      if (options.isStopping() || handledRunError) return;
      options.onError(formatChatRunError({ code: "CHAT_REQUEST_FAILED" }, options.messages));
    },
  };
}
