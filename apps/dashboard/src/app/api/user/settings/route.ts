/**
 * 用户 LLM 配置 REST API —— 多配置 CRUD + 切换激活 + 揭示完整 key。
 *
 * 端点：
 *  GET  /api/user/settings          → 获取所有配置（掩码显示）
 *  POST /api/user/settings          → 新增配置
 *  PUT  /api/user/settings/[id]     → 更新配置
 *  DELETE /api/user/settings/[id]   → 删除配置
 *  POST /api/user/settings/activate → 切换激活配置
 *  POST /api/user/settings/reveal-key → 揭示完整 key（需验证）
 */
import { NextRequest, NextResponse } from "next/server";
import { getSessionSubject } from "@/lib/backend";
import {
  getUserLLMConfigs,
  addUserLLMConfig,
  updateUserLLMConfig,
  deleteUserLLMConfig,
  type UserLLMConfigInput,
} from "@/lib/user-preferences";

/**
 * GET /api/user/settings
 * 获取当前用户所有 LLM 配置（掩码显示）。
 */
export async function GET() {
  try {
    const subject = await getSessionSubject();
    const result = await getUserLLMConfigs(subject);
    return NextResponse.json(result);
  } catch (err) {
    console.error("[GET /api/user/settings]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}

/**
 * POST /api/user/settings
 * 新增一个 LLM 配置。
 *
 * Body: { provider, model?, api_key, custom_base_url?, custom_provider_name?, label? }
 */
export async function POST(request: NextRequest) {
  try {
    const subject = await getSessionSubject();
    const body = await request.json();

    // 验证必填字段
    if (!body.provider || !body.api_key) {
      return NextResponse.json(
        { error: "Missing required fields: provider, api_key" },
        { status: 400 },
      );
    }

    const input: UserLLMConfigInput = {
      provider: body.provider,
      model: body.model,
      api_key: body.api_key,
      custom_base_url: body.custom_base_url,
      custom_provider_name: body.custom_provider_name,
      label: body.label,
    };

    const configId = await addUserLLMConfig(subject, input);

    return NextResponse.json({ id: configId }, { status: 201 });
  } catch (err) {
    console.error("[POST /api/user/settings]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}

/**
 * PUT /api/user/settings/[id]
 * 更新指定配置。
 *
 * Body: { provider?, model?, api_key?, custom_base_url?, custom_provider_name?, label? }
 */
export async function PUT(
  request: NextRequest,
  { params }: { params: { id: string } },
) {
  try {
    const subject = await getSessionSubject();
    const body = await request.json();

    await updateUserLLMConfig(subject, params.id, body);

    return NextResponse.json({ success: true });
  } catch (err) {
    console.error("[PUT /api/user/settings]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}

/**
 * DELETE /api/user/settings/[id]
 * 删除指定配置。
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: { id: string } },
) {
  try {
    const subject = await getSessionSubject();
    await deleteUserLLMConfig(subject, params.id);

    return NextResponse.json({ success: true });
  } catch (err) {
    console.error("[DELETE /api/user/settings]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}