import {
  CheckCircle,
  Circle,
  Clock,
  Wrench,
  XCircle,
  type LucideIcon,
} from "lucide-react";

/**
 * 工具调用状态机（7 态 · 定义先行）。
 *
 * 参照 Omnigent 的 ToolUIPart["state"] 设计，适配 AG-UI 的消息模型。
 *
 * ⚠️ **接线现状（避免误以为七态都已可用）**：
 * AG-UI 当前只传递二态（tool call → tool result），所以 `inferToolState`
 * 实际只会产出下面 3 个态：
 *   - **input-available**（Running）：toolCalls 存在但无对应 tool result
 *   - **output-available**（Completed）：tool result 存在且无 error
 *   - **output-error**（Error）：tool result 存在且含 error
 *
 * 另外 4 个态（input-streaming / output-denied / approval-requested /
 * approval-responded）是**为 mastra 侧未来上报中间状态预留的定义**——
 * TOOL_STATE_MAP 里有它们的渲染样式，但目前**没有生产者**会产出这些态。
 * 等 mastra 上报 tool 生命周期细分（如 plan/exec 审批态）时，只需扩
 * `inferToolState` 的输入即可点亮，无需再动样式表。
 */

export type ToolState =
  // ── 已接线（inferToolState 会产出）──
  | "input-available" // 参数就绪，执行中（Running）
  | "output-available" // 执行成功（Completed）
  | "output-error" // 执行失败（Error）
  // ── 预留（样式已备，待 mastra 上报后点亮，当前无生产者）──
  | "input-streaming" // 正在接收工具参数（Pending）
  | "output-denied" // 被权限拒绝（Denied）
  | "approval-requested" // 等待审批（Awaiting Approval）
  | "approval-responded"; // 审批已响应（Responded）

export interface ToolStateProps {
  label: string;
  Icon: LucideIcon;
  color: string;
  /** 是否可展开查看结果 */
  expandable: boolean;
  /** 是否显示 pulse 动画 */
  pulse: boolean;
}

export const TOOL_STATE_MAP: Record<ToolState, ToolStateProps> = {
  "input-streaming": {
    label: "Pending",
    Icon: Circle,
    color: "text-fg-muted",
    expandable: false,
    pulse: true,
  },
  "input-available": {
    label: "Running",
    Icon: Wrench,
    color: "text-gold",
    expandable: false,
    pulse: false,
  },
  "output-available": {
    label: "Completed",
    Icon: CheckCircle,
    color: "text-bull",
    expandable: true,
    pulse: false,
  },
  "output-error": {
    label: "Error",
    Icon: XCircle,
    color: "text-fox-red",
    expandable: true,
    pulse: false,
  },
  "output-denied": {
    label: "Denied",
    Icon: XCircle,
    color: "text-orange-500",
    expandable: true,
    pulse: false,
  },
  "approval-requested": {
    label: "Awaiting Approval",
    Icon: Clock,
    color: "text-yellow-500",
    expandable: false,
    pulse: true,
  },
  "approval-responded": {
    label: "Responded",
    Icon: CheckCircle,
    color: "text-blue-500",
    expandable: false,
    pulse: false,
  },
};

/**
 * 从 AG-UI 消息推断工具状态。
 *
 * 当前只产出 3 个态（input-available / output-available / output-error）——
 * 其余 4 个态待 mastra 上报中间状态后在此扩展（见 ToolState 注释）。
 *
 * @param hasResult 工具结果是否已到达
 * @param hasError 结果中是否包含 error
 */
export function inferToolState(
  hasResult: boolean,
  hasError = false,
): ToolState {
  if (hasResult) return hasError ? "output-error" : "output-available";
  return "input-available";
}
