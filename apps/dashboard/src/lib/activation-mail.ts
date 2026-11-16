import "server-only";

import nodemailer from "nodemailer";

/** 发送审批激活邮件；失败不回滚审批，不记录授权码或激活令牌。 */
export async function sendActivationEmail(
  email: string | null | undefined,
  token: string,
): Promise<"sent" | "failed" | "not_configured"> {
  const { SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ACTIVATION_PUBLIC_URL } = process.env;
  if (!SMTP_HOST || !SMTP_USER || !SMTP_PASSWORD || !ACTIVATION_PUBLIC_URL) {
    return "not_configured";
  }
  if (!email) return "failed";
  try {
    const origin = new URL(ACTIVATION_PUBLIC_URL);
    if (origin.protocol !== "https:" || origin.username || origin.password) return "failed";
    const link = new URL("/activate?from=%2Fzh", origin);
    link.hash = `token=${encodeURIComponent(token)}`;
    const transport = nodemailer.createTransport({
      host: SMTP_HOST,
      port: Number(process.env.SMTP_PORT || "465"),
      secure: true,
      auth: { user: SMTP_USER, pass: SMTP_PASSWORD },
      connectionTimeout: 10_000,
      greetingTimeout: 10_000,
      socketTimeout: 15_000,
      logger: false,
      debug: false,
    });
    try {
      const result = await transport.sendMail({
        from: { name: "Inalpha", address: SMTP_USER },
        to: { address: email, name: "" },
        subject: "Inalpha 试用申请已通过 / Your trial is approved",
        text: `你的 Inalpha 试用申请已通过！请打开以下链接设置密码并激活账号：\n\n${link.href}\n\n链接一次性有效，48 小时后过期。重新签发后旧链接失效。若非本人申请，请忽略此邮件。\n\nYour Inalpha trial is approved. Open the link above to set your password and activate your account. This one-time link expires in 48 hours. If you did not apply, ignore this email.`,
      });
      return result.accepted.length > 0 ? "sent" : "failed";
    } finally {
      transport.close();
    }
  } catch {
    return "failed";
  }
}
