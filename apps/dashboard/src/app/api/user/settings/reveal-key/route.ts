/**
 * 揭示完整 API key API（需二次验证）。
 *
 * POST /api/user/settings/reveal-key
 * Body: { config_id: string, verification: string }
 * Response: { api_key: string, expires_at: string }
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionSubject } from "@/lib/backend";
import { revealUserApiKey } from "@/lib/user-preferences";

export async function POST(request: NextRequest) {
  try {
    const subject = await getSessionSubject();
    const body = await request.json();

    if (!body.config_id || !body.verification) {
      return NextResponse.json(
        { error: "Missing required fields: config_id, verification" },
        { status: 400 },
      );
    }

    const result = await revealUserApiKey(
      subject,
      body.config_id,
      body.verification,
    );

    return NextResponse.json(result);
  } catch (err) {
    console.error("[POST /api/user/settings/reveal-key]", err);

    // 验证失败返回 401
    if (err instanceof Error && err.message.includes("Verification failed")) {
      return NextResponse.json(
        { error: "Verification failed" },
        { status: 401 },
      );
    }

    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}
