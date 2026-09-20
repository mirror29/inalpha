import { createOrchestrator } from "../mastra/agents/create-orchestrator.js";
import { wireToolList } from "../mastra/tool-wiring.js";
import { PendingApprovalsStore } from "../permissions/pending.js";
import { EvalFailureError } from "./errors.js";
import { createFixtureTools } from "./fixture-tools.js";
import { gradeTrial } from "./grader.js";
import type { GoldenTask } from "./schema.js";
import { ScriptedModel } from "./scripted-model.js";
import { normalizeTrajectory } from "./trajectory.js";
import { createEvalRequestContext } from "./runner-context.js";
import {
  acquireNetworkGuardLock,
  classifyFailure,
  failedResult,
  installNetworkGuard,
  messageOf,
  numberField,
  selectFailureClass,
} from "./runner-helpers.js";
import type { EvalTrialResult, RunTrialOptions } from "./types.js";

/** 串行执行隔离评测。 */
export async function runEvalTrial(
  task: GoldenTask,
  options: RunTrialOptions,
): Promise<EvalTrialResult> {
  const started = performance.now();
  const runId = `eval:${task.id}:${options.trial}`;
  const scripted = task.mode === "scripted"
    ? new ScriptedModel(task.id, task.fixtures.modelTurns)
    : undefined;
  const model = options.model ?? scripted?.asLanguageModel();
  if (!model) return failedResult(task, options, started, "live_provider", "live model missing");

  const fixtureSet = createFixtureTools(task);
  const pendingApprovals = new PendingApprovalsStore(() => undefined);
  const wired = wireToolList(fixtureSet.tools, {
    auditSink: () => undefined,
    pendingApprovals,
    askTimeoutMs: 100,
  });
  const agent = createOrchestrator({
    model,
    tools: Object.fromEntries(wired.map((tool) => [tool.id, tool])),
    maxSteps: task.budget.maxSteps,
  });
  const requestContext = createEvalRequestContext(task);
  const releaseNetwork = await acquireNetworkGuardLock();
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new EvalFailureError("timeout", "eval item timeout")),
    task.budget.timeoutMs,
  );
  let restoreFetch: () => void = () => undefined;

  try {
    restoreFetch = installNetworkGuard(options.allowNetwork === true);
    const output = await agent.generate(task.prompt, {
      runId,
      requestContext,
      abortSignal: controller.signal,
      maxSteps: task.budget.maxSteps,
    });
    if (controller.signal.aborted) throw controller.signal.reason;
    scripted?.assertConsumed();
    const trajectory = normalizeTrajectory(
      output,
      fixtureSet.events,
      task.fixtures.tools.map((tool) => tool.id),
    );
    const findings = gradeTrial({
      task,
      text: output.text,
      steps: output.steps.length,
      trajectory,
    });
    const failed = findings.filter((finding) => !finding.passed);
    const failureClass = selectFailureClass(findings);
    const usage = output.totalUsage as unknown;
    return {
      taskId: task.id,
      taskVersion: task.taskVersion,
      trial: options.trial,
      passed: failed.length === 0,
      failureClass,
      findings,
      text: output.text,
      trajectory,
      metrics: {
        steps: output.steps.length,
        toolCalls: trajectory.length,
        inputTokens: numberField(usage, "inputTokens"),
        outputTokens: numberField(usage, "outputTokens"),
        totalTokens: numberField(usage, "totalTokens"),
        latencyMs: Math.round(performance.now() - started),
        costUsd: null,
      },
      model: options.modelDescriptor ?? {
        provider: scripted?.provider ?? "live",
        modelId: scripted?.modelId ?? "unknown",
      },
      runId: output.runId ?? runId,
      traceId: output.traceId ?? null,
    };
  } catch (error) {
    return failedResult(task, options, started, classifyFailure(error), messageOf(error));
  } finally {
    clearTimeout(timer);
    pendingApprovals.clearAll();
    restoreFetch();
    releaseNetwork();
  }
}
