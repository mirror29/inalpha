import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { LocaleSwitcher } from "./LocaleSwitcher";

const replace = vi.hoisted(() => vi.fn());
vi.mock("next-intl", () => ({ useLocale: () => "zh" }));
vi.mock("@/i18n/navigation", () => ({
  usePathname: () => "/evolution/prepare",
  useRouter: () => ({ replace }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
});

it.each([
  ["?targetKind=paper_runner&targetId=fixture-id&tag=a&tag=b", "#coverage"],
  ["?targetKind=paper_runner&targetId=fixture-id", ""],
  ["", "#coverage"],
  ["", ""],
])("preserves query %s and fragment %s in the navigation callback", (search, hash) => {
  vi.stubGlobal("React", React);
  vi.stubGlobal("window", {
    location: {
      search,
      hash,
    },
  });
  const buttons = LocaleSwitcher().props.children;
  buttons.find((button: React.ReactElement<{ children: string; onClick: () => void }>) => button.props.children === "EN").props.onClick();
  expect(replace).toHaveBeenCalledWith(
    `/evolution/prepare${search}${hash}`,
    { locale: "en" },
  );
});
