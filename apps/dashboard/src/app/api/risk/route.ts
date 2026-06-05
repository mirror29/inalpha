import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import type { RiskLock, RiskPayload, RiskRule } from "@/lib/types";

export const dynamic = "force-dynamic";

interface RulesResp {
  enabled: boolean;
  starting_balance: number;
  rules: RiskRule[];
}
interface LocksResp {
  locks: RiskLock[];
}

/**
 * GET /api/risk —— 风控面板:规则配置 + 当前活跃锁。
 * rules(配置)与 locks(DB)独立 allSettled 降级:任一取不到只标 sources.<x>=false。
 */
export async function GET() {
  const [rulesR, locksR] = await Promise.allSettled([
    backendFetch<RulesResp>("paper", "/risk/rules"),
    backendFetch<LocksResp>("paper", "/risk/locks"),
  ]);

  const rules = rulesR.status === "fulfilled" ? rulesR.value : null;
  const locks = locksR.status === "fulfilled" ? locksR.value.locks : [];

  const payload: RiskPayload = {
    enabled: rules?.enabled ?? false,
    starting_balance: rules?.starting_balance ?? 0,
    rules: rules?.rules ?? [],
    locks,
    sources: {
      rules: rulesR.status === "fulfilled",
      locks: locksR.status === "fulfilled",
    },
    asOf: new Date().toISOString(),
  };
  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store" },
  });
}
