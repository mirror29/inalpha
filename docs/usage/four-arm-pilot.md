# Four-arm development pilot (#206 / #208)

The local command chain prepares aligned data, freezes relative evidence, runs all four
arms with the unified ledger, replays interventions, and produces paired development
metrics. It does not freeze the formal protocol, claim compute matching before calibration,
or replace Qiming's protocol review. The starting settings implement the v0.1 discussion
on #206; the configuration must be reviewed and frozen before formal evaluation.

## Prepare inputs

From `services/research`:

```bash
uv sync --group pilot
uv run --group pilot python -m inalpha_research.pilot.materials /private/path/materials
uv run python -m inalpha_research.pilot validate /private/path/materials/materials.json
```

Defaults fetch TSLA and SPY from Yahoo Finance with `auto_adjust=False`, daily OHLCV,
from 2024-05-07 through 2026-07-07 inclusive. Both series must exactly match XNYS
sessions. Source retrieval time, library version, raw rows and content hashes are retained.
`--market /path/market.json` reuses a snapshot without another network request.

Ten consecutive development cases start 2026-05-01. Each uses a 252-session lookback.
Asset and benchmark returns, volatility, volume ratio and range position are model-visible;
tickers, dates, absolute prices, source hashes and outcomes are not. Daily-close availability
is an explicit historical assumption, not observed archival availability; vendor revisions
cannot be excluded. Returns use unadjusted prices, exclude dividend cashflows and ignore
borrow costs. Split-containing periods require a revised protocol before use.

The outcome enters at the next session open and exits five sessions later. Labels use
`tau = 0.5 * sample_std(five-session excess returns)`, calibrated only on windows whose
exit strictly precedes the first development case. Costs are 20 bp round trip, with 0/50 bp
sensitivity. Outcome labels live separately from `inputs.json` and are only opened by the
report command. The deterministic pre-run process rubric is not a human annotation or
inter-rater agreement claim. The formal split is empty; do not reuse these development
windows as formal evaluation cases.

### Review options (#206, opt-in until the protocol freezes them)

Defaults reproduce the 2026-10-12 development packet exactly (tau 3.79538%). The #206 review
proposals are explicit flags so neither packet is silently changed:

- `--benchmark-dividends` adds benchmark cash dividends whose ex-date falls in
  (entry, exit] to the benchmark return. On the 542-session packet this moves tau to
  3.79096% and changes no development label.
- `--tau-windows N` estimates tau from the trailing N eligible windows instead of all of
  them. With N = 252, tau is 3.01326% and two development labels move from FLAT.
- The protocol always records `tau_calibration_windows`, `benchmark_return` and the
  calibration-window `climatology` (UP/FLAT/DOWN base rates) used by the report.

## Run after budget authorization

Use an authorized local/test Postgres database with migration 0066 applied. Set
`DATABASE_URL` and `DEEPSEEK_API_KEY` in the process environment without printing them.
The explicit `--owner` is the authorized operator identity for this standalone run, not
an unauthenticated web request parameter. Do not use this CLI to impersonate a production
user. Credentials are never written into manifests, prompts or ledger links.

```bash
uv run python -m inalpha_research.pilot.campaign run \
  /private/path/materials/inputs.json /private/path/campaign \
  --config examples/pilot/development-config.json --owner '<authorized-owner>'
uv run python -m inalpha_research.pilot.campaign report \
  /private/path/campaign /private/path/materials/outcomes.json
```

The example $5 configuration is a proposal, not authorization to spend. A campaign creates
an exclusive directory and never silently resumes or overwrites a prior paid run. API SDK
retries are disabled. Each attempted final answer may retry once, within the remaining
budget; every attempt persists an intent first and uses the shared `UsageRecorder`.
Unconfirmed ledger settlement or unknown provider usage stops further paid calls.
Unknown attempts retain the full cost/token reservation, never zero. Failures remain in
the paired dataset. Interrupted directories retain call events and must be investigated;
there is no automatic paid replay.

Input reservations conservatively use UTF-8 bytes plus framing allowance, not an exact
provider tokenizer. Actual reported usage reconciles each reservation; an overrun stops
the campaign. Therefore this is an observed guard with a conservative preflight estimate,
not a mathematical claim that provider tokenization or billing cannot exceed a bound.
Prices are frozen peak/non-cache estimates, not invoices. The example metadata records
the provider-documented version separately from unknown immutable revision/training cutoff.

## Arm and replay semantics

- A: one independent final decision, lower-budget reference.
- B: fixed independent samples and aggregation.
- C: fixed sequential reflection/revision units and final decision.
- D: fixed bull/bear/risk turns across configured rounds and final synthesis. It uses the
  production research roles in an isolated price-only adapter, without live tools,
  disagreement-trigger skips or convergence early exits. Budget exhaustion can shorten
  the run and is recorded along with completed units.

The working stage may consume at most 85% of the arm token cap. Final aggregation has
at least 15% reserved, including its prompt. Defaults are machinery-smoke settings;
calibrate B/C/D sample counts and rounds on separate development runs until provider-
reported median usage is within 10%, then freeze. `compute_match_within_10_percent` reports
observed matching; a successful command does not imply matching or scientific readiness.

Interventions modify the closest-probability analyst in the last completed round (stable
turn-order tie-break), preserve the earlier prefix and replay all later turns. Contribution
removal has an unrelated character-length replacement control. Conclusion flipping is an
explicit edited message; it does not ask the model to cooperate with an instruction to flip.
If the selected contribution is neutral, conclusion flipping is explicitly skipped because
there is no directional reverse; the runner does not inject an arbitrary long stance.
Evidence removal drops the first cited evidence block and replays from round zero, with
an uncited similar-length placebo where available. Missing controls receive explicit skip
reasons. Intervention stimuli carry no meta signal: a deleted contribution keeps its step slot for
replay alignment but is omitted from every model-visible history (no removal marker); a
flipped conclusion swaps `p_up`/`p_down` and the action but keeps the original rationale
verbatim, so the rationale/conclusion inconsistency is itself part of the stimulus; the
replacement control is digit-free, topic-unrelated prose from the versioned
`neutral-prose-v1` corpus, under a neutral `message` key, matched by characters.
Fresh stochastic noise reruns use the same replay boundaries; no unsupported
provider seed guarantee is made. Literal ID/numeric scans report possible evidence re-entry,
with rounded/inferred reconstruction remaining a limitation.

Optional `sample_temperature` in the campaign configuration applies only to B's independent
samples (default 0.2, unchanged); the #206 review proposes 0.8 for the formal arm definition.
Every other call, including B's aggregation, stays at 0.2.

Invalid runs are scored strictly by default: the probability forecast receives the maximum
Brier (2.0) with action 0, so a failure can never score better than a real forecast. Uniform
and climatology fallbacks are reported as `itt_sensitivity_brier`. The report also gives
uniform and climatology reference Brier scores, Brier skill vs climatology for every arm,
per-class reliability (mean predicted vs observed), `B-mean` (mean of the completed B
sample probabilities, argmax action, neutral on ties; same calls, so a scoring variant
rather than a separately budgeted arm) with a paired B-mean minus B interval, and B's
pairwise sample action disagreement.

The report includes multiclass Brier, hit/coverage/invalid rates, net excess returns,
intention-to-treat scoring with the strict invalid-run rule (Brier 2.0, action 0) as the
primary analysis, uniform and climatology fallbacks as sensitivity analyses only,
complete-case results, paired moving-block bootstrap intervals, usage, intervention
flip/probability/Brier changes and noise-adjusted flip differences. Character matching is
not exact tokenizer matching; formal intervention controls and blinded human process
scoring still need protocol review.

Budget diagnostics (#206 v0.3 §1) are in every report under `budget_diagnostics`. They
keep the matching target T and the safety cap separate. The cap comes from
`baseline_tokens` (A) and `arm_tokens` (B/C/D); the target comes only from an explicitly
declared `matching_target_tokens` (an int for B/C/D, or a per-arm mapping) and is never
inferred from observed usage. Per arm and per case they report:

- known input/output tokens, median, quartiles and IQR, and each case's ratio to T and to
  the cap;
- the share of cases within ±25% of T and the preregistered verdict (median within ±10% and
  at least 80% of cases within ±25%), or null when no target is declared;
- attempts, retries and failed attempts; token budget stops and accounting stops
  (unconfirmed ledger or unknown usage) by reason;
- invalid runs and rate, and the distribution of completed units.

A case with any unknown attempt keeps its known subtotal but is excluded from matching
statistics and counted separately; unknown usage is never zero-filled.

## Offline rescoring

Rescore saved outputs into a separate, versioned report without provider or database access:

```bash
uv run python -m inalpha_research.pilot.rescore \
  /private/path/campaign /private/path/materials/outcomes.json /private/path/rescore-v1 \
  --target-tokens 9400 --code-revision "$(git rev-parse HEAD)"
```

The command verifies the outcome hash against the pre-run commitment, hashes every file in
the campaign directory before and after, and fails if any changed. It writes only
`rescore.json` into a new directory, which must lie outside the campaign directory. The
report carries the version (`pilot-rescore-v1`), the source file hashes (including the
original `report.json`, which is left untouched), the declared target and its source, the
caps, the climatology used, the full scored report and the budget diagnostics. Rescoring
cannot validate settings or stimuli the saved run did not use, such as a new sampling
temperature or intervention text.

Not yet covered (tracked on #206): formal intervention selection (v0.3 §4), meaning a
preregistered seeded selection rule in place of the first N cases, and full-pool / valid-D /
selected / skipped / attempted / failed / valid-pair counts with explicit denominators. The
runner currently retains individual skip reasons only.

A 10-case run validates the machinery. Retain negative/inconclusive results and limitations.

## Real-provider development acceptance (2026-10-12)

Under an explicitly approved total $5 limit, DeepSeek completed a one-case calibration
(14 calls) and the ten-case four-arm campaign (296 calls, including replays). All 40 base
runs and six replay runs completed; the uncited-evidence placebo was explicitly skipped
because the selected base decision cited every evidence block. All artifact validators
passed and all 296 main call IDs matched the unified ledger, with zero pending/unknown
usage. Total estimated cost including calibration was $0.1601403, not an invoice total.

Calibrated settings were 11 B samples, 6 C revisions and 2 D rounds. Observed median
input-plus-output tokens were A=593, B=9348, C=9383, D=9324. B/C/D match within 10%
of their mean; 24000 remains a guard cap, not an achieved preregistered token target.
Calibration reused the first development case; no held-out or formal evaluation is claimed.

Brier means were A=.81066, B=.77360, C=.68910, D=.72692. These ten overlapping
development cases do not demonstrate multi-agent superiority. All six replays retained
the same action. Evidence-removal outputs misattributed benchmark returns to the asset;
this factual error remains in the results. The literal re-entry ID scan also matched
`returns` within `benchmark_returns`, so its flag is a heuristic requiring inspection,
not proof that removed input leaked. Exact numeric scanning cannot rule out reconstruction.

The receipt is `docs/validation/research-pilot-readiness-2026-10-12.json`. Independent
review found a report-label validation gap, now fixed with duplicate/invalid/empty-label
regressions. Exact tokenizer controls, blinded human process review, and formal protocol
and disjoint sample freeze remain open.
