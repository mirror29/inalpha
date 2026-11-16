"use client";

import { useState } from "react";
import { Check, LoaderCircle, RefreshCw, UserRoundCheck, X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";

interface WaitlistUser {
  subject: string;
  email: string;
  display_name: string | null;
  application_note: string | null;
  access_status: "pending" | "invited";
  created_at: string;
  reviewed_at: string | null;
}

interface WaitlistResponse {
  users: WaitlistUser[];
}

const fetcher = async (url: string): Promise<WaitlistResponse> => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(String(response.status));
  return response.json() as Promise<WaitlistResponse>;
};

/** 管理员 waitlist：展示申请背景并提供批准/拒绝两种原子审核动作。 */
export function WaitlistClient() {
  const t = useTranslations("adminWaitlist");
  const locale = useLocale();
  const { data, error, isLoading, mutate } = useSWR<WaitlistResponse>(
    "/api/admin/waitlist",
    fetcher,
  );
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [activation, setActivation] = useState<{ email: string; link: string; delivery?: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function review(user: WaitlistUser, decision: "approve" | "reject") {
    if (decision === "reject" && !window.confirm(t("rejectConfirm"))) return;
    if (
      decision === "approve" &&
      user.access_status === "invited" &&
      !window.confirm(t("regenerateConfirm"))
    ) {
      return;
    }
    setReviewing(user.subject);
    setActionError(null);
    try {
      const response = await fetch(
        `/api/admin/waitlist/${encodeURIComponent(user.subject)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision,
            expected_reviewed_at: user.reviewed_at,
          }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      const result = (await response.json()) as { activation_token?: string | null; email_delivery?: string };
      if (decision === "approve" && result.activation_token) {
        const link = `${window.location.origin}/activate?from=${encodeURIComponent(`/${locale}`)}#token=${encodeURIComponent(result.activation_token)}`;
        setActivation({ email: user.email, link, delivery: result.email_delivery });
        setCopied(false);
      }
      await mutate();
    } catch {
      setActionError(t("actionFailed"));
      await mutate();
    } finally {
      setReviewing(null);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-fg-muted">
        <LoaderCircle className="size-4 animate-spin" /> {t("loading")}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-fox-red/30 bg-fox-red/5 p-5 text-sm text-fox-red">
        <p>{t("loadFailed")}</p>
        <button type="button" onClick={() => void mutate()} className="mt-3 inline-flex items-center gap-2 text-cyan">
          <RefreshCw className="size-4" /> {t("retry")}
        </button>
      </div>
    );
  }

  const users = data?.users ?? [];
  if (users.length === 0 && !activation) {
    return (
      <div className="rounded-lg border border-border-subtle bg-bg-elev/40 p-10 text-center">
        <UserRoundCheck className="mx-auto size-8 text-cyan" strokeWidth={1.5} />
        <p className="mt-4 text-sm text-fg-muted">{t("empty")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {actionError && <p role="alert" className="text-sm text-fox-red">{actionError}</p>}
      {activation && (
        <div role="status" className="rounded-lg border border-cyan/30 bg-cyan/5 p-5">
          <p className="text-sm font-medium text-fg">{t(activation.delivery === "sent" ? "emailSent" : "activationReady")}</p>
          <p className="mt-2 text-xs leading-5 text-fg-muted">
            {t(activation.delivery === "sent" ? "emailSentInstructions" : "emailFailedInstructions", { email: activation.email })}
          </p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input readOnly value={activation.link} aria-label={t("activationLink")} className="min-w-0 flex-1 rounded-md border border-border-subtle bg-bg-deep/60 px-3 py-2 font-mono text-xs text-cyan" />
            <button type="button" onClick={async () => { await navigator.clipboard.writeText(activation.link); setCopied(true); }} className="rounded-md border border-cyan/30 px-3 py-2 text-xs text-cyan hover:bg-cyan/10">
              {copied ? t("copied") : t("copy")}
            </button>
          </div>
        </div>
      )}
      {users.map((user) => {
        const busy = reviewing !== null;
        const current = reviewing === user.subject;
        return (
          <article key={user.subject} className="rounded-lg border border-border-subtle bg-bg-elev/55 p-5">
            <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
              <div className="min-w-0">
                <h2 className="truncate text-base font-medium text-fg">{user.display_name || t("unnamed")}</h2>
                <p className="mt-1 truncate font-mono text-xs text-cyan">{user.email}</p>
                <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-fg-muted">
                  {t(user.access_status === "invited" ? "invited" : "pending")}
                </p>
                <p className="mt-2 text-xs text-fg-muted">
                  {t("appliedAt", {
                    time: new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(user.created_at)),
                  })}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button type="button" disabled={busy} onClick={() => void review(user, "reject")} className="inline-flex items-center gap-1.5 rounded-md border border-fox-red/30 px-3 py-2 text-xs text-fox-red transition-colors hover:bg-fox-red/10 disabled:opacity-50">
                  <X className="size-3.5" /> {t("reject")}
                </button>
                <button type="button" disabled={busy} onClick={() => void review(user, "approve")} className="inline-flex items-center gap-1.5 rounded-md border border-bull/30 bg-bull/10 px-3 py-2 text-xs text-bull transition-colors hover:bg-bull/20 disabled:opacity-50">
                  {current ? <LoaderCircle className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} {t(user.access_status === "invited" ? "regenerate" : "approve")}
                </button>
              </div>
            </div>
            <div className="mt-4 border-t border-border-subtle pt-4">
              <div className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">{t("note")}</div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-fg-muted">{user.application_note || t("noNote")}</p>
            </div>
          </article>
        );
      })}
    </div>
  );
}
