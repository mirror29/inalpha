import { NextResponse } from "next/server";
import { BACKENDS, backendFetch, getServiceToken, getSessionSubject } from "@/lib/backend";
import { decryptActiveUserApiKey } from "@/lib/user-preferences";

/** Forward the selected owner's model configuration over the existing private transport, never to the browser. */
export async function POST(request: Request) {
  try {
    const subject = await getSessionSubject();
    const config = await decryptActiveUserApiKey(subject);
    if (!config) return NextResponse.json({ error: "NO_LLM_CONFIG" }, { status: 428 });
    const response = await fetch(`${BACKENDS.mastra}/evolution/approval`, {
      method: "POST", cache: "no-store", signal: AbortSignal.timeout(120_000),
      headers: { Authorization: `Bearer ${await getServiceToken()}`, "Content-Type": "application/json",
        "X-LLM-Config": JSON.stringify({ id: config.id, provider: config.provider, model: config.model,
          api_key: config.api_key, custom_base_url: config.custom_base_url,
          custom_provider_name: config.custom_provider_name, label: config.label }) },
      body: JSON.stringify(await request.json()),
    });
    if (!response.ok) return NextResponse.json({ error: "EXPERIMENT_APPROVAL_UNAVAILABLE" }, { status: response.status });
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "EXPERIMENT_APPROVAL_UNAVAILABLE" }, { status: 503 }); }
}


/** Restore only the authenticated owner's approval; query parameters carry no execution authority. */
export async function GET(request: Request) {
  const id = new URL(request.url).searchParams.get("requestId");
  if (!id || !/^[0-9a-f-]{36}$/i.test(id)) return NextResponse.json({ error: "bad_request" }, { status: 400 });
  try {
    return NextResponse.json(await backendFetch("mastra", `/evolution/approval/${encodeURIComponent(id)}`), { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "EXPERIMENT_APPROVAL_UNAVAILABLE" }, { status: 503 }); }
}
