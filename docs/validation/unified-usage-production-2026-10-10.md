# Unified usage production acceptance — 2026-10-10

## Scope and result

Production runs merged PR #213 (`1dc8fa78f7d02857cc1c8968e9255db19b245a9f`). Authenticated browser acceptance confirms the usage page, operation filter, empty Research filter and a real Chat request. No new Research or Evolver production run was attempted in this pass; previous real local acceptance remains documented separately.

The new completed Chat receipt has 70,526 input tokens, 1,138 output tokens and estimated cost $0.0225234. Its operation-filtered UI shows exactly one call, matching the persisted ledger. The unfiltered pre-request UI also matched the database: 15 calls, 2,265,350 input tokens, 19,708 output tokens and $0.7032546. Unknown and pending counts were zero. An empty Research filter shows unknown usage/cost rather than fabricated zero cost.

The configuration menu exposes model settings, usage, diagnostics, trial audit and logout. Keyboard selection highlights the Radix menu item and Escape closes the menu. Pointer hover was not separately verified in this pass.

## Historical research inventory

Read-only production metadata inspection exported no private conversation text or research bodies. `research_memory` contains zero rows. Mastra message storage contains five distinct recoverable research result objects with briefs, thesis and synthesis reasoning. Their debate-log fields are empty; these are partially recoverable outputs, not complete discussion experiment records. There are 75 deep-dive tool parts, which must not be interpreted as 75 successful runs.

Stored yfinance TSLA daily data contains 542 bars from 2024-05-07 through 2026-07-07. SPY data is absent. No new benchmark, outcome labels or point-in-time news/fundamental evidence has been fabricated or frozen.

## Token presentation follow-up

Summary and detail tokens use compact K/M/B/T units with up to two decimal places. Exact database integer values remain in accessible labels and native hover titles; null remains unknown and zero remains known zero. Conversion uses BigInt to avoid Number precision loss. Desktop preview verified 19.42K / 5.81K tokens. At 390px, document width equals viewport width; the exact title remains 19421 / 5808 tokens. The new display is a follow-up change and was not deployed during this acceptance pass.

Validation: Dashboard 185 tests in 40 files passed, including compact-unit boundary and large-integer cases; TypeScript passed. The production build passed.

## Remaining research gates

Issue #206 must agree asset/calendar, benchmark, decision/entry timing, horizon, costs, probabilities, thresholds, scoring and model-call budget before formal material generation. A proposed price-only TSLA/SPY development pilot is awaiting maintainer selection; it is not a frozen protocol. Aligned asset/benchmark cases, deterministic outcome labels and blinded process annotations with provenance remain outstanding.

Provider/model aliases are known. Immutable model revision and training-cutoff evidence are unknown and must stay explicitly unknown. Alias names do not establish uncontaminated evaluation. The real model request verifies accounting, not provider invoice reconciliation or strategy quality. Full E2/Forward/holdout acceptance is outside this result. Issue #208 remains open.
