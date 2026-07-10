/**
 * 切换激活配置 API。
 *
 * POST /api/user/settings/activate
 * Body: { config_id: string }
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionSubject } from "@/lib/backend";
import { activateUserLLMConfig } from "@/lib/user-preferences";

export async function POST(request: NextRequest) {
  try {
    const subject = await getSessionSubject();
    const body = await request.json();

    if (!body.config_id) {
      return NextResponse.json(
        { error: "Missing required field: config_id" },
        { status: 400 },
      );
    }

    await activateUserLLMConfig(subject, body.config_id);

    return NextResponse.json({ success: true });
  } catch (err) {
    console.error("[POST /api/user/settings/activate]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}
