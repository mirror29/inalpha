import { randomUUID } from "node:crypto";
import { beforeEach, describe, expect, it } from "vitest";
import { getSettings, setSettings } from "../src/config.js";
import {
  openExecution,
  sealExecution,
  type ExecutionBinding,
} from "../src/permissions/execution-envelope.js";
import {
  approvalInputDigest,
  type ApprovedExecutionCommand,
} from "../src/permissions/pending.js";

const secret = "test-approved-command-secret-for-local-only";
const request = {
  config: {
    params: {
      token: "BTC",
      wallet: 3,
      nested: { password: "not-a-live-credential" },
    },
  },
};
const approvalInput = { request };
const view = {
  requestId: randomUUID(),
  sessionId: "owned-session",
  toolName: "evolver.run_evolution",
  inputDigest: approvalInputDigest(approvalInput),
  toolInput: request,
  createdAt: new Date().toISOString(),
  deadline: new Date(Date.now() + 30_000).toISOString(),
};
const command: ApprovedExecutionCommand = { approvalInput, view };
const binding: ExecutionBinding = {
  authSub: "alice",
  operationId: view.requestId,
  sessionId: view.sessionId,
  toolName: view.toolName,
  inputDigest: view.inputDigest,
};

beforeEach(() => setSettings({ ...getSettings(), jwtSecret: secret }));

describe("authenticated frozen execution storage", () => {
  it("preserves legitimate sensitive-looking parameters without plaintext persistence", () => {
    const sealed = sealExecution(command, binding);
    const wire = JSON.stringify(sealed);
    expect(wire).not.toContain("BTC");
    expect(wire).not.toContain("not-a-live-credential");
    expect(wire).not.toContain("approvalInput");
    expect(openExecution(JSON.parse(wire), binding)).toEqual(command);
    expect(
      approvalInputDigest(openExecution(sealed, binding).approvalInput),
    ).toBe(view.inputDigest);
    expect(sealExecution(command, binding)).not.toEqual(sealed);
  });
  it.each([
    "authSub",
    "operationId",
    "sessionId",
    "toolName",
    "inputDigest",
  ] as const)("rejects replacement of %s", (field) => {
    const sealed = sealExecution(command, binding);
    expect(() =>
      openExecution(sealed, { ...binding, [field]: "other" }),
    ).toThrow("could not be authenticated");
  });
  it("rejects ciphertext tampering, unknown formats and rotation without leaking content", () => {
    const sealed = sealExecution(command, binding);
    expect(() =>
      openExecution(
        { ...sealed, ciphertext: Buffer.from("changed").toString("base64") },
        binding,
      ),
    ).toThrow("could not be authenticated");
    expect(() =>
      openExecution({ ...command, format: "invalid" }, binding),
    ).toThrow("could not be authenticated");
    setSettings({ ...getSettings(), jwtSecret: "different-local-test-secret" });
    expect(() => openExecution(sealed, binding)).toThrow(
      "could not be authenticated",
    );
  });
  it("reads existing plaintext commands only under the original binding", () => {
    expect(openExecution(command, binding)).toEqual(command);
    expect(() =>
      openExecution(command, { ...binding, operationId: randomUUID() }),
    ).toThrow("could not be authenticated");
  });
});
