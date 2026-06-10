"use client";

import * as React from "react";
import { motion, AnimatePresence } from "motion/react";
import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { cn } from "@/lib/cn";

type FAQItemProps = {
  question: string;
  answer: string;
  isOpen: boolean;
  onToggle: () => void;
};

function FAQItem({ question, answer, isOpen, onToggle }: FAQItemProps) {
  return (
    <div className="border-b border-fg/10">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full cursor-pointer items-center justify-between gap-4 py-5 text-left"
        aria-expanded={isOpen}
      >
        <span className="text-[15px] text-fg leading-snug pr-8">
          {question}
        </span>
        <span
          className={cn(
            "flex-shrink-0 text-fg-muted/60 transition-transform duration-200",
            isOpen && "rotate-45"
          )}
        >
          <Plus className="h-4 w-4" />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <p className="pb-5 text-[14px] leading-relaxed text-fg-muted max-w-[62ch]">
              {answer}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/**
 * FAQ section with expandable items. Positions Inalpha against common
 * questions for both human readers and search-engine snippet extraction.
 */
export function FAQ() {
  const t = useTranslations("faq");

  const items = t.raw("items") as Array<{ question: string; answer: string }>;
  const [openIndex, setOpenIndex] = React.useState<number | null>(null);

  const toggle = (i: number) => {
    setOpenIndex((prev) => (prev === i ? null : i));
  };

  return (
    <BroadsheetSection
      index={t("eyebrow.index")}
      eyebrow={t("eyebrow.label")}
      title={t("title")}
      intro={t("intro")}
      specRef="SEO.md §FAQ"
      align="right"
    >
      <div className="w-full max-w-2xl">
        {items.map((item, i) => (
          <FAQItem
            key={i}
            question={item.question}
            answer={item.answer}
            isOpen={openIndex === i}
            onToggle={() => toggle(i)}
          />
        ))}
      </div>
    </BroadsheetSection>
  );
}
