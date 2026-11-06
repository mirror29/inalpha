import { NextResponse } from "next/server";
import { readSession } from "@/lib/session";
import { getPool } from "@/lib/db";
import { usageFilter } from "@/lib/usage-query";

export const dynamic = "force-dynamic";

/** Returns known subtotals with explicit missing-usage coverage, scoped to the owner. */
export async function GET(request: Request) {
  const session = await readSession();
  if (!session) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  let filter;
  try { filter = usageFilter(new URL(request.url).searchParams, session.subject); }
  catch { return NextResponse.json({ error: "invalid filters" }, { status: 400 }); }
  let client;
  try {
    client = await getPool().connect();
    await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    await client.query("SET LOCAL statement_timeout='5s'");
    const summary = await client.query(`SELECT COUNT(*)::int calls,
      SUM(input_tokens)::text input_tokens,SUM(output_tokens)::text output_tokens,
      SUM(estimated_cost_usd)::text known_cost_usd,
      COUNT(*) FILTER(WHERE usage_status='unknown')::int unknown_usage_calls,
      COUNT(*) FILTER(WHERE estimated_cost_usd IS NULL)::int unknown_cost_calls,
      COUNT(*) FILTER(WHERE status='pending')::int pending_calls,
      MIN(created_at) first_record_at
      FROM llm_usage_calls WHERE ${filter.where}`, filter.values);
    const items = await client.query(`SELECT call_id,logical_call_id,service,stage,operation_id,
      attempt,provider,model,response_model,status,usage_status,input_tokens::text,output_tokens::text,
      cached_input_tokens::text,reasoning_tokens::text,estimated_cost_usd::text,created_at,
      settled_at,latency_ms,finish_reason,provenance,links
      FROM llm_usage_calls WHERE ${filter.where}
      ORDER BY created_at DESC,call_id DESC LIMIT 51 OFFSET $${filter.values.length + 1}`,
      [...filter.values, filter.offset]);
    await client.query("COMMIT");
    return NextResponse.json({ summary: summary.rows[0], items: items.rows.slice(0, 50),
      nextOffset: items.rows.length > 50 ? filter.offset + 50 : null,
      coverage: { version: "usage-v1", historicalAggregatesIncluded: false,
        sources: ["orchestration", "research", "evolver"],
        limitations: ["Historical aggregate evolution costs are excluded from request totals.",
          "Pending calls may include interrupted requests; their cost is unknown."] } },
      { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    await client?.query("ROLLBACK").catch(() => undefined);
    return NextResponse.json({ error: "usage temporarily unavailable" }, { status: 503 });
  } finally { client?.release(); }
}
