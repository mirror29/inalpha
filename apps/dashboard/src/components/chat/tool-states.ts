import {
  CheckCircle,
  Circle,
  Clock,
  Wrench,
  XCircle,
  type LucideIcon,
} from "lucide-react";

/**
 * 工具调用状态机（7 态）。
 *
 * 参照 Omnigent 的 ToolUIPart["state"] 设计，适配 AG-UI 的消息模型。
 * AG-UI 当前仅传递二态（tool call → tool result），完整七态需要 mastra
 * 侧上报中间状态。现阶段基于可用数据推断：
 *   - toolCalls 存在但无对应 tool result → Running
 *   - tool result 存在且无 error → Completed
 *   - tool result 存在且有 error → Error
 */

export type ToolState =
  | "input-streaming" // 正在接收工具参数
  | "input-available" // 参数就绪，执行中
  | "output-available" // 执行成功
  | "output-error" // 执行失败
  | "output-denied" // 被权限拒绝
  | "approval-requested" // 等待审批
  | "approval-responded"; // 审批已响应

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
