import { NextResponse } from "next/server";

import { sendActivationEmail } from "@/lib/activation-mail";

import { backendFetch } from "@/lib/backend";

import { reviewErrorResponse } from "./review-error";

/**
 * 管理员审核 BFF。
 *
 * 功能：批准或拒绝一条 pending 申请。
 * 何时用：waitlist 页面提交单次审核动作时。
 * 何时不用：修改已审核用户；后端状态机只接受 pending 转换。
 * 坑：subject 必须 URL 编码，且并发重复审核会返回 409。
 */
export async function POST(
  req: Request,
  context: { params: Promise<{ subject: string }> },
): Promise<Response> {
  const { subject } = await context.params;
  let decision: unknown;
  let expectedReviewedAt: unknown;
  try {
    ({ decision, expected_reviewed_at: expectedReviewedAt } = await req.json());
  } catch {
    return NextResponse.json({ error: "INVALID_REQUEST" }, { status: 400 });
  }
  if (decision !== "approve" && decision !== "reject") {
    return NextResponse.json({ error: "INVALID_DECISION" }, { status: 400 });
  }
  if (expectedReviewedAt !== null && typeof expectedReviewedAt !== "string") {
    return NextResponse.json({ error: "INVALID_REVIEW_VERSION" }, { status: 400 });
  }

  try {
    const result = await backendFetch<{
      subject: string;
      access_status: string;
      activation_token?: string | null;
      email?: string | null;
    }>(
      "paper",
      `/auth/waitlist/${encodeURIComponent(subject)}/review`,
      {
        method: "POST",
        body: { decision, expected_reviewed_at: expectedReviewedAt },
      },
    );
    const email_delivery =
      decision === "approve" && result.activation_token
        ? await sendActivationEmail(result.email, result.activation_token)
        : undefined;
    return NextResponse.json({ ...result, email_delivery }, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    return reviewErrorResponse(err);
  }
}
