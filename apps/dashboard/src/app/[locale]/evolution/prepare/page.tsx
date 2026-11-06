import { setRequestLocale } from "next-intl/server";
import { EvolutionPreparation } from "@/components/evolution/EvolutionPreparation";

/** Prepare a new explicit research window before requesting any paid execution. */
export default async function PreparePage({ params, searchParams }: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ targetKind?: string; targetId?: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const target = await searchParams;
  return <EvolutionPreparation targetKind={target.targetKind ?? ""} targetId={target.targetId ?? ""} />;
}
