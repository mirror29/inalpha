import { beforeEach, describe, expect, it, vi } from "vitest";
import nodemailer from "nodemailer";
import { sendActivationEmail } from "./activation-mail";

vi.mock("server-only", () => ({}));
vi.mock("nodemailer", () => ({ default: { createTransport: vi.fn() } }));

const sendMail = vi.fn();
const close = vi.fn();

describe("activation email", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.stubEnv("SMTP_HOST", "smtp.example.com");
    vi.stubEnv("SMTP_USER", "sender@example.com");
    vi.stubEnv("SMTP_PASSWORD", "test-only");
    vi.stubEnv("ACTIVATION_PUBLIC_URL", "https://dashboard.example.com");
    sendMail.mockReset().mockResolvedValue({ accepted: ["applicant@example.com"] });
    close.mockReset();
    vi.mocked(nodemailer.createTransport).mockReturnValue({ sendMail, close } as unknown as ReturnType<typeof nodemailer.createTransport>);
  });

  it("sends the token only in a trusted HTTPS link fragment", async () => {
    expect(await sendActivationEmail("applicant@example.com", "abc/def")).toBe("sent");
    const message = sendMail.mock.calls[0][0];
    expect(message.to).toEqual({ address: "applicant@example.com", name: "" });
    expect(message.text).toContain("https://dashboard.example.com/activate?from=%2Fzh#token=abc%2Fdef");
    expect(message.text).not.toContain("test-only");
    expect(close).toHaveBeenCalled();
  });

  it("treats the stored email as one address rather than an address list", async () => {
    await sendActivationEmail("first,last@example.com", "token");
    expect(sendMail.mock.calls[0][0].to).toEqual({
      address: "first,last@example.com",
      name: "",
    });
  });

  it("returns a safe failure on SMTP rejection", async () => {
    sendMail.mockRejectedValue(new Error("private SMTP details"));
    expect(await sendActivationEmail("a@example.com", "token")).toBe("failed");
    expect(close).toHaveBeenCalled();
  });

  it("does not send when configuration or recipient is missing", async () => {
    expect(await sendActivationEmail(null, "token")).toBe("failed");
    vi.stubEnv("SMTP_PASSWORD", "");
    expect(await sendActivationEmail("a@example.com", "token")).toBe("not_configured");
    expect(sendMail).not.toHaveBeenCalled();
  });

  it("rejects an insecure activation URL", async () => {
    vi.stubEnv("ACTIVATION_PUBLIC_URL", "http://dashboard.example.com");
    expect(await sendActivationEmail("a@example.com", "token")).toBe("failed");
    expect(sendMail).not.toHaveBeenCalled();
  });
});
