import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);

/** Nested overlays must share Radix's module-level focus and dismissal stacks. */
describe("Select inside Dialog", () => {
  for (const dependency of [
    "@radix-ui/react-focus-scope",
    "@radix-ui/react-dismissable-layer",
  ]) {
    it(`shares ${dependency} with Dialog`, () => {
      const dialog = createRequire(require.resolve("@radix-ui/react-dialog"));
      const select = createRequire(require.resolve("@radix-ui/react-select"));
      expect(select.resolve(dependency)).toBe(dialog.resolve(dependency));
    });
  }
});
