"use client";

import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import useSWR from "swr";

import { jsonFetcher } from "@/lib/fetcher";

type Capability = {
  event_evolution_enabled: boolean;
  reason: string | null;
};

/** Open the contextual Agent composer with the shortest valid evolution command. */
export function EvolutionStartButton() {
  const t = useTranslations("evolution.start");
  const { data } = useSWR<Capability>("/api/evolution/capabilities", jsonFetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
  });
  const enabled = data?.event_evolution_enabled === true;
  return (
    <button
      type="button"
      disabled={!enabled}
      title={enabled ? t("hint") : data?.reason ?? t("checking")}
      onClick={() => {
        window.dispatchEvent(
          new CustomEvent("inalpha:evolution-start", {
            detail: { prompt: t("prompt") },
          }),
        );
      }}
      className="inline-flex items-center gap-1.5 rounded-md border border-seal/30 bg-seal/10 px-3 py-2 font-mono text-xs text-seal transition-colors hover:bg-seal/20 disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Sparkles className="size-3.5" strokeWidth={1.75} />
      {t("label")}
    </button>
  );
}
