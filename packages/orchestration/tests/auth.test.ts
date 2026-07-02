import { describe, expect, it } from "vitest";

import { defaultServiceSubject, resolveRequestToken, verifyToken } from "../src/auth.js";
import { AUTH_SUB_KEY } from "../src/hooks/with-hooks.js";

/**
 * ``resolveRequestToken`` 是"agent 写操作按登录用户隔离"的核心：从 tool ctx 解析
 * 打给下游 service 的 token。历史上同类"读错字段 → 静默回落兜底"的 bug 真实发生过
 * 一次（authToken 属性 vs RequestContext Map 的 AUTH_SUB_KEY），故这里锁三条路径。
 */
describe("resolveRequestToken", () => {
  it("显式 authToken 直接 forward(不重签)", async () => {
    const token = await resolveRequestToken({ authToken: "explicit-token-abc" });
    expect(token).toBe("explicit-token-abc");
  });

  it("从 RequestContext.get(AUTH_SUB_KEY) 取已认证 sub 铸 token", async () => {
    const rc = {
      get: (k: string) => (k === AUTH_SUB_KEY ? "user:alice" : undefined),
    };
    const token = await resolveRequestToken(rc);
    const payload = await verifyToken(token);
    expect(payload.sub).toBe("user:alice");
  });

  it("既无 authToken 也无 sub → 回落 service subject", async () => {
    const token = await resolveRequestToken({ get: () => undefined });
    const payload = await verifyToken(token);
    expect(payload.sub).toBe(defaultServiceSubject());
  });

  it("rc 为 undefined → 回落 service subject", async () => {
    const token = await resolveRequestToken();
    const payload = await verifyToken(token);
    expect(payload.sub).toBe(defaultServiceSubject());
  });
});
