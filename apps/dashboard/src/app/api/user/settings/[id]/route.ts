/**
 * 更新指定 LLM 配置。
 *
 * PUT /api/user/settings/[id]
 * Body: { provider?, model?, api_key?, custom_base_url?, custom_provider_name?, label? }
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionSubject } from "@/lib/backend";
import { updateUserLLMConfig, deleteUserLLMConfig } from "@/lib/user-preferences";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const subject = await getSessionSubject();
    const { id } = await params;
    const body = await request.json();

    await updateUserLLMConfig(subject, id, body);

    return NextResponse.json({ success: true });
  } catch (err) {
    console.error("[PUT /api/user/settings/[id]]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}

/**
 * 删除指定配置。
 *
 * DELETE /api/user/settings/[id]
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const subject = await getSessionSubject();
    const { id } = await params;
    await deleteUserLLMConfig(subject, id);

    return NextResponse.json({ success: true });
  } catch (err) {
    console.error("[DELETE /api/user/settings/[id]]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}