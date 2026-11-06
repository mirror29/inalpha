# Maintainer evidence checks (#208)

These offline checks support the existing #206 division of work. They do not implement
Qiming's rescoring, budget report, interventions or multi-ticker runner. No provider,
production database or network connection is made. No protocol is frozen by these tools.

Run from `services/research` using the existing project environment:

```bash
uv run python scripts/verify_pilot_materials.py packet \
  --campaign /explicit/path/to/campaign \
  --materials /explicit/path/to/materials \
  --output /explicit/path/to/new-verification

uv run python scripts/verify_pilot_materials.py data \
  --market /explicit/path/to/materials/market.json \
  --config /explicit/path/to/preflight.json \
  --output /explicit/path/to/new-data-verification
```

Outputs are `verification.json` and `verification.md`, in an exclusively created directory
(mode 0700; files 0600). Output must be outside input directories. Repeated output paths
are refused. Inputs are never rewritten. Exit 1 means recorded blockers, exit 2 means the
report could not be produced; exit 0 may still mean `unknown`, so consumers must read the
status. `formal_ready` is always false: these tools do not certify protocol or scientific
readiness. Checks use `pass`, `blocked`, `unknown`, and `not_applicable`.

## Existing packet inventory

Required campaign files: `campaign.json`, `results.json`, `report.json`, plus each indexed
run directory's manifest, evidence, call events and terminal result. Required material
files: `market.json`, `materials.json`, `inputs.json`, `outcomes.json`, `annotations.json`.
Missing or malformed files are blockers; non-successful runs remain visible rather than
being discarded. Unindexed directories are reported for manual investigation.

The checker reuses the existing material/run validators and verifies frozen commitments,
raw-market projections, four-arm coverage, base/replay lineage, configuration and result
projections, call uniqueness and local usage consistency. Recorded local settlement is
not fresh ledger reconciliation. SHA256 values are byte hashes except entries explicitly
named `*_canonical`, which use the existing canonical `digest()` helper.

Reports contain fixed file aliases, ordinal run numbers, hashes, check names and counts.
They do not copy prompts, responses, owner identities or exception contents. Resolve file
aliases against the two explicitly supplied input roots; run ordinals follow base runs,
then non-skipped replays in `results.json`. Keep the reports private alongside the materials.
A report hash inventories an old score report; it does not validate its scoring semantics.
Missing climatology is reported as unknown, not reconstructed or inserted into old files.

## Data preflight configuration

The market input is the existing `market.json` format: ordered `sessions`, `session_closes`,
asset/benchmark raw OHLCV rows, source and retrieval metadata. Run once per explicitly
selected asset/benchmark pair; the checker does not select a stock universe.

Supply a JSON object with these fields (no default formal dates, tickers or thresholds):

| Field | Meaning |
| --- | --- |
| `expected_sessions` | Ordered calendar sessions supplied from the agreed calendar, covering the snapshot |
| `candidate_sessions` | Unique, explicit decision sessions to check |
| `lookback_sessions` | Positive feature lookback length |
| `calibration_windows` | Positive count of trailing calibration outcomes |
| `horizon_sessions` | Positive open-to-open horizon, entry next session |
| `market_hash` | Optional previously recorded canonical market digest; absent is unknown |
| `symbols` | Explicit `asset` and `benchmark` source identifiers |
| `exposures` | Explicit array of `{kind, start, end}` date intervals; missing is unknown |
| `protocol_frozen` | True only for an already agreed frozen protocol; otherwise blocked |
| `requires_historical_membership` | False only when the chosen protocol does not require a historical index universe |
| `membership` | Optional `{source, as_of, members, hash}`; hash covers all fields except hash |
| `action_fields_verified` | Optional asset/benchmark booleans recording an external check of source action-field presence |

Exposure kinds are `development_decision`, `development_outcome`, and
`calibration_analysis`. Intervals are inclusive. Candidates are excluded if their decision
through exit window overlaps any recorded exposure. This is conservative exposure
screening, not a claim that overlapping historical feature windows always leak outcomes.
Incomplete exposure records cannot prove independence.

Calibration uses windows with exit strictly before the candidate decision. Feature lookback
and calibration can overlap; no artificial two-year warm-up is imposed. Coverage is checked
per candidate without computing labels, returns, thresholds or reference probabilities.

Dividends/splits distinguish missing columns from explicit zeros; nonzero events are counted
but not adjusted by this tool. The current development downloader defaults absent vendor
action columns to zero, so presence of zero fields alone cannot prove source completeness:
`action_fields_verified` records a separate source inspection, not automatic certification.
Source and membership hashes establish local consistency, not historical authenticity.
Historical membership must predate or equal the first candidate and include the supplied
asset. Present-day constituents never substitute for missing historical evidence.

Historical bar availability remains `unknown` even with retrieval and close timestamps:
this snapshot cannot establish what a vendor actually served at a past decision time.
A frozen protocol flag cannot remove that limitation or enable `formal_ready`.

## Maintainer handoff checklist

1. Record the explicit campaign/material roots locally and retain their original files.
2. Deliver the packet report with byte hashes, blockers and unknowns. Inspect every failed
   run and unmatched call without replacing or deleting it.
3. Deliver data coverage and exposure reports; attach source evidence privately. Mark stock
   universe, company actions, timestamp assumptions and model cutoff gaps explicitly.
4. When Qiming's commands are available, compare source byte hashes before/after execution;
   inspect separate output provenance, scoring version, budget target versus cap, failed and
   retried calls, and unknown usage. Do not implement a second scoring/reporting system.
5. Retain failures and inconclusive outcomes. These checks alone do not verify statistical
   superiority, live ledger totals, provider invoices or full production research quality.

## Cross-machine execution handoff (2026-10-27)

The original ten-case **input materials** have been recovered and checked. The old
campaign and per-call output records have not been located. Full real-campaign acceptance
therefore remains **pending**; synthetic tests and input validation do not replace it.
No fresh model calls or production database reconciliation were performed on this machine.

### Recover the exact inputs

The [public material receipt](https://github.com/mirror29/inalpha/issues/208#issuecomment-6117572702)
and the original delivery identify `inalpha-qiming-pr226-handoff-2026-10-12.zip`.
Retrieve it from the original shared delivery on the execution machine; keep it outside
the repository. Its SHA256 must be:

```text
b02d9149a979ef0ab7cf0553797c617c409f66cb41d81597b73b83ee054d0a8d
```

The bundle contains `market.json`, `materials.json`, `inputs.json`, `outcomes.json`,
`annotations.json`, `README.md`, and `SHA256SUMS`. All six listed file hashes matched
on 2026-10-27. The native material validator returned `ready: true`, with no blockers;
this is structural development-material validation, not formal evaluation readiness.
Frozen input/outcome commitments and market projections matched. The packet contains
542 aligned TSLA/SPY sessions (2024-05-07 through 2026-07-07) and ten development
decisions (2026-05-01 through 2026-05-14). It contains **no campaign or model outputs**.
The original material code revision is `592820a6d8d299a7b986c08b61edd40d674b5357`.

Do not substitute the older 41-case delivery (ZIP SHA256
`0866435dba0fe6f8d4a353c33e81c8fe5ad802616888e15cf9eada7b33952458`)
or mix its thresholds with the ten-case inputs. Its exposed development interval
2026-05-01 through 2026-06-30 and outcome coverage through 2026-07-09 still belong in
exposure records. Recover exact calibration/exposure records before claiming independence.
Neither delivery supplies the missing climatology or strict historical PIT evidence.

### Execution order on the next machine

1. Pull `main`, then run `uv sync --frozen` in `services/research`. Retrieve and extract
   the original bundle into a new private directory; check the ZIP hash and its
   `SHA256SUMS` before use. Record local paths privately, not in Git.
2. First look for the original campaign on the machine that ran it: `campaign.json`,
   `results.json`, `report.json`, and all referenced run directories, including
   `calls.jsonl`. Copy the complete tree without dropping failed runs. Existing receipt
   summaries alone are insufficient. This machine's relevant project/artifact/download
   locations and prior project conversations did not reveal those original outputs;
   this does not prove they are absent from another machine or shared storage.
3. Validate the recovered inputs, then execute the offline packet check using explicit
   paths and a new output directory:

   ```bash
   cd services/research
   uv run python -m inalpha_research.pilot validate /private/materials/materials.json
   uv run python scripts/verify_pilot_materials.py packet \
     --campaign /private/original-campaign \
     --materials /private/materials \
     --output /private/new-packet-verification
   ```

4. Build the explicit preflight configuration described above from agreed calendar,
   provenance and exposure evidence. Run `data` separately. Keep the protocol unfrozen
   until agreed; missing source action columns, historical availability, membership and
   calibration evidence remain `unknown` or `blocked`. Do not invent candidate dates,
   a six-stock universe, labels, or climatology to clear checks.
5. If original outputs remain unavailable, the maintainer has authorized a **new
   development batch** using the existing campaign implementation. It must have a new
   experiment directory/date and cannot stand in for historical output verification.
   Before running, configure the authorized operator's DeepSeek credentials and a
   dedicated development PostgreSQL ledger with migrations applied (including 0066).
   Do not use production or another project's database. No credentials are included in
   this handoff; this machine had no configured provider/ledger credentials.
6. Freeze a private campaign config with explicit provider/model, current pricing,
   finite `limit_usd`, token limits, sample/revision counts and rounds, using the existing
   CLI schema. Separate the experiment's budget-matching target from its safety cap.
   Record code revision, config hash and input byte hashes. Use the existing command:

   ```bash
   uv run python -m inalpha_research.pilot.campaign run \
     /private/materials/inputs.json /private/new-development-campaign \
     --config /private/pinned-campaign-config.json --owner "$PILOT_OWNER" \
     --intervention-cases 1
   uv run python -m inalpha_research.pilot.campaign report \
     /private/new-development-campaign /private/materials/outcomes.json
   ```

   This step makes paid provider calls; the preceding offline commands do not. Supply
   `DEEPSEEK_API_KEY` and `DATABASE_URL` securely in the execution environment. Never
   record them in config, Git, reports or shell transcripts. Preserve partial failures
   and unknown settlements; do not automatically resume paid calls. Report creation is
   exclusive and must not overwrite an existing report. Then run the offline checker
   against the new campaign and verify that input hashes are unchanged.
7. Retain JSON/Markdown reports, input/output hashes, exact commands and limitations
   locally. When Qiming's delivered rescoring/budget commands are available, use them
   for the independent acceptance checklist above. File reproducible local defect
   cases before fixing delivered defects. No parallel rescoring implementation,
   reassignment, GitHub comments, E2 work or production deployment is included.

### Validation record and remaining acceptance

The Research suite passed **292 tests**, with **3 PostgreSQL ledger integration tests
skipped** because a dedicated test database was not configured. Research Ruff and
format checks passed. The suite was run with proxy variables removed for that test
process because the host SOCKS proxy requires an unavailable optional dependency.
Synthetic fixtures cover valid packets, tampering, missing artifacts, duplicate calls,
unknown settlement, missing climatology, misaligned sessions, missing action fields,
and development-window overlap. They also check input immutability, offline operation
and output overwrite refusal.

Still required on the execution machine: full real-campaign verification, explicit
real-data preflight, and independent acceptance of Qiming's delivered report commands.
Do not mark these complete based on this input-only receipt or synthetic tests.
