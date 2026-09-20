import type { FullOutput } from "@mastra/core/stream";
import { describe, expect, it } from "vitest";

import {
  classifyResult,
  normalizeTrajectory,
  stableStringify,
} from "../../src/evals/trajectory.js";

function output(
  result?: { result: unknown; isError?: boolean },
  toolName = "fixture.read",
): FullOutput {
  return {
    steps: [
      {
        toolCalls: [
          {
            payload: {
              toolCallId: "call-1",
              toolName,
              args: { symbol: "AAPL" },
            },
          },
        ],
        toolResults: result === undefined
          ? []
          : [
              {
                payload: {
                  toolCallId: "call-1",
                  result: result.result,
                  isError: result.isError,
                },
              },
            ],
      },
    ],
  } as unknown as FullOutput;
}

describe("agent eval trajectory", () => {
  it("uses failed raw execution when a provider omits its tool result", () => {
    const trajectory = normalizeTrajectory(output(), [
      {
        tool: "fixture.read",
        input: { symbol: "AAPL" },
        status: "failed",
      },
    ]);

    expect(trajectory[0]).toMatchObject({
      attempted: true,
      executed: true,
      resultClass: "tool_error",
    });
  });

  it("restores dotted fixture IDs after Mastra normalizes tool names", () => {
    const trajectory = normalizeTrajectory(
      output({ result: { ok: true } }, "fixture_read"),
      [{ tool: "fixture.read", input: { symbol: "AAPL" }, status: "succeeded" }],
      ["fixture.read"],
    );

    expect(trajectory[0]).toMatchObject({
      tool: "fixture.read",
      executed: true,
      resultClass: "success",
    });
  });

  it("classifies permission results without claiming raw execution", () => {
    const trajectory = normalizeTrajectory(
      output({ result: { deniedBy: "permission", isError: true } }),
      [],
    );

    expect(trajectory[0]).toMatchObject({
      executed: false,
      resultClass: "permission_deny",
    });
  });

  it("classifies every permission, middleware, and tool error branch", () => {
    expect(classifyResult({ requiresApproval: true }, undefined, undefined))
      .toBe("approval_required");
    expect(classifyResult({ deniedBy: "permission-ask" }, undefined, undefined))
      .toBe("approval_required");
    expect(classifyResult({ deniedBy: "permission" }, undefined, undefined))
      .toBe("permission_deny");
    for (const deniedBy of ["hook", "middleware-error"]) {
      expect(classifyResult({ deniedBy }, undefined, undefined))
        .toBe("middleware_error");
    }
    expect(classifyResult({ isError: true }, undefined, "succeeded"))
      .toBe("tool_error");
    expect(classifyResult({}, true, "succeeded")).toBe("tool_error");
    expect(classifyResult({}, false, "failed")).toBe("tool_error");
    expect(classifyResult({}, false, "succeeded")).toBe("success");
  });

  it("sorts keys and removes sensitive metadata", () => {
    expect(
      stableStringify({
        z: 1,
        nested: { password: "hidden", a: 2 },
        providerMetadata: { requestId: "unstable" },
      }),
    ).toBe(
      '{"nested":{"a":2,"password":"[REDACTED]"},' +
        '"providerMetadata":"[REDACTED]","z":1}',
    );
  });
});
