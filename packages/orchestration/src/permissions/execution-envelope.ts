import {
  createCipheriv,
  createDecipheriv,
  hkdfSync,
  randomBytes,
} from "node:crypto";

import { getSettings } from "../config.js";
import type { ApprovedExecutionCommand } from "./pending.js";

export interface ExecutionBinding {
  authSub: string;
  operationId: string;
  sessionId: string;
  toolName: string;
  inputDigest: string;
}

interface ExecutionEnvelope {
  format: "inalpha-approved-execution-v1";
  nonce: string;
  ciphertext: string;
  tag: string;
}

/** Uses a separate HKDF domain so approval encryption never reuses the JWT signing key. */
function encryptionKey(): Buffer {
  return Buffer.from(
    hkdfSync(
      "sha256",
      getSettings().jwtSecret,
      "inalpha-approved-execution-v1",
      "aes-256-gcm",
      32,
    ),
  );
}

/** Binds ciphertext to the exact authenticated owner and persisted approval identity. */
function associatedData(binding: ExecutionBinding): Buffer {
  return Buffer.from(
    JSON.stringify([
      binding.authSub,
      binding.operationId,
      binding.sessionId,
      binding.toolName,
      binding.inputDigest,
    ]),
  );
}

/** Saves the executable command losslessly; audit history remains separately redacted. */
export function sealExecution(
  command: ApprovedExecutionCommand,
  binding: ExecutionBinding,
): ExecutionEnvelope {
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), nonce);
  cipher.setAAD(associatedData(binding));
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(command), "utf8"),
    cipher.final(),
  ]);
  return {
    format: "inalpha-approved-execution-v1",
    nonce: nonce.toString("base64"),
    ciphertext: ciphertext.toString("base64"),
    tag: cipher.getAuthTag().toString("base64"),
  };
}

/** Authenticates stored commands; malformed envelopes cannot fall back to plaintext. */
export function openExecution(
  stored: unknown,
  binding: ExecutionBinding,
): ApprovedExecutionCommand {
  try {
    if (!stored || typeof stored !== "object") throw new Error();
    const value = stored as Record<string, unknown>;
    let command: ApprovedExecutionCommand;
    if ("format" in value) {
      if (
        value.format !== "inalpha-approved-execution-v1" ||
        typeof value.nonce !== "string" ||
        typeof value.ciphertext !== "string" ||
        typeof value.tag !== "string"
      )
        throw new Error();
      const nonce = Buffer.from(value.nonce, "base64");
      const tag = Buffer.from(value.tag, "base64");
      if (nonce.length !== 12 || tag.length !== 16) throw new Error();
      const decipher = createDecipheriv("aes-256-gcm", encryptionKey(), nonce);
      decipher.setAAD(associatedData(binding));
      decipher.setAuthTag(tag);
      command = JSON.parse(
        Buffer.concat([
          decipher.update(Buffer.from(value.ciphertext, "base64")),
          decipher.final(),
        ]).toString("utf8"),
      ) as ApprovedExecutionCommand;
    } else {
      /** Pre-envelope records remain readable; dispatch still checks the original digest. */
      command = value as unknown as ApprovedExecutionCommand;
    }
    if (
      !command.view ||
      command.view.requestId !== binding.operationId ||
      command.view.sessionId !== binding.sessionId ||
      command.view.toolName !== binding.toolName ||
      command.view.inputDigest !== binding.inputDigest ||
      !("approvalInput" in command)
    )
      throw new Error();
    return command;
  } catch {
    throw new Error("approved execution could not be authenticated");
  }
}
