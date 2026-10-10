# Unified model usage and Research pilot artifacts

The ledger records requests, not invoices. It is the common source for the Dashboard
usage page and pilot call attribution. Apply migration **0066 before deploying writers**.
The migration adds tables/columns and a transactional chat compatibility bridge; existing
chat readers remain supported. Never add candidate totals or chat preparation rollups to
request totals: those are projections of the same underlying work.

## Accounting contract

`llm_usage_calls` has one UUID `call_id` per provider attempt. Retries share
`logical_call_id` with zero-based `attempt`; business retries may start another logical
call. `auth_sub` comes from verified authentication or the persisted, approved job.
`operation_id` identifies a research, chat or evolution operation. `parent_operation_id`
links a verified owned chat invocation. `links` carries pilot experiment/case/arm/run IDs.

- Persist intent before invoking a paid provider. If intent cannot persist, do not call.
- Settlement updates the matching owner/call once. Retry settlement up to three times;
  never repeat the model request because accounting failed.
- A crash or failed settlement leaves `pending`/unknown. Pending does not mean free,
  nor prove that a provider is still running. No automatic paid replay is performed.
- Input/output counts are normalized totals. OpenAI reasoning is an output subset and
  cached input is an input subset. Anthropic cache-read/write counts are added to its
  uncached input count. Gemini thoughts are added to candidate output. Detail columns
  are not added again when summing totals. Normalization version: `usage-v1`.
- Missing usage is null. Provider-reported zero is a valid known value. Missing or
  unsupported pricing leaves cost null even when tokens are known.
- Costs use frozen, versioned USD input/output rates, without inventing cache discounts;
  these are estimates, not billed amounts. Research production requests accept explicit model-bound snapshots via
  `RESEARCH_USAGE_PRICING` (a JSON array); absent or ambiguous matches remain unknown.
- The ledger contains no prompts, response bodies, keys or authorization headers.
  Pilot content lives in restricted local artifacts, joined by call ID.

The chat compatibility bridge retains original `chat_usage_receipts` and atomically
mirrors every write. Historical imports are tagged `legacy-chat-step`; their old step
receipts cannot prove unavailable historical retry detail. Evolver historical candidate
aggregates remain in existing views and are excluded from request totals. The migration
refuses rollback once any ledger records exist; roll back application code instead.

## Query interface

Open **Settings → Usage** in the Dashboard sidebar (Chinese: **配置 → 模型用量**),
or `/{locale}/usage`, for the owner-scoped view.

Authenticated `GET /api/usage` returns `summary`, 50 `items`, `nextOffset`, and `coverage`.
Filters: `from` (inclusive UTC), `to` (exclusive UTC), `service`, `model`, `status`,
`operation`, `experiment_id`, `case_id`, `arm_id`, `run_id`, `offset`. An operation filter
includes its directly linked child requests once. Ownership always comes from the
session, never query parameters. Totals and rows use one repeatable-read snapshot.
Numeric token/amount values are strings to avoid JSON integer precision loss.

Coverage:

| Path | Capture boundary | Limitations |
| --- | --- | --- |
| Chat/orchestration | Mastra input/output step processors, SDK retries disabled | Failed/incomplete streams without an output receipt remain pending/unknown; response model is retained where exposed by the step; otherwise unknown |
| Scheduler agent | Same orchestrator processor, verified service identity | Service-owned requests appear under that identity, not another user's account |
| Research deep dive/parallel/personas/debate/synthesis | Each SDK request, explicit observed retries | Live research is not frozen historical evidence |
| Research pilot adapters | Same SDK boundary plus local call artifacts | #206 owns four-arm prompts, budgets and scoring |
| Evolver E1 mutation/repair and E2 proposer/mutation | Shared SDK request through approved owner client | Legacy aggregate candidate costs are not reconstructed as calls |
| Event extractor | No LLM call: deterministic rules | No token row is appropriate |
| Developer scripts (including `scripts/deepseek_review.py`) and standalone evaluation utilities | Outside authenticated application runtime | Not automatically billable owner records; explicitly wire a recorder if used for a paid pilot |

## Pilot preparation

From `services/research`:

```bash
uv run python -m inalpha_research.pilot freeze examples/pilot/synthetic-input.json > /tmp/pilot-evidence.json
uv run python -m inalpha_research.pilot validate /tmp/pilot-evidence.json
uv run python -m inalpha_research.pilot inventory /path/to/authorized-export.json
```

Inventory prints field coverage and hashes only. It does not search private storage,
export conversations, or infer persistence from a response schema. Use explicit authorized
exports and review their source provenance. Missing historical usage cannot be recovered
from output text.

`PilotArtifacts` creates a unique private run directory (0700, files 0600). It records
versioned manifest/evidence, append-only started/settled call events and an exclusive
result file. Missing result or an unmatched start is an interrupted/incomplete run, not
success. `pilot.session.pilot_client` wires the provider to the unified store and artifact
sink. The frozen configuration and client arguments must explicitly name the same provider
and model; implicit provider/model defaults are rejected for pilot calls. Keep API keys in memory; do not put them in the configuration manifest. Artifact
validation rejects credential-shaped fields and common key/bearer literals; this is not
a guarantee that arbitrary free text is safe to publish. Artifacts are private by default.

The runner uses `artifacts.model_input()` as its evidence and invokes `artifacts.finish`
with analyst/discussion/final outputs or an explicit failure status. There is no live data
client or fallback in this adapter. Inputs/outcomes must be separate objects/directories.
All arms must use the identical evidence hash. The adapter does not run arms or grant a
model-call budget.

Evidence items carry identity, kind, exact content, source, available time or explicit
uncertainty, and SHA256. Price evidence also requires actual session close. Unknown
availability is retained in a bundle but blocks model-ready validation. Unfinished bars,
future evidence, malformed schema, duplicate identities and changed hashes are rejected.
The supplied fixture is synthetic; it is not market evidence or an evaluation result.

Validate imported materials with `uv run python -m inalpha_research.pilot validate /path/to/materials.json` from `services/research`.

A material manifest contains `protocol`, `development`, `formal`, `market`, `labels`, and
`annotations`. Protocol must provide asset/calendar/timezone/benchmark/horizon/entry timing,
adjustment/cost assumptions, decision/probability semantics, neutral/short treatment and
thresholds. Market input explicitly supplies ordered expected `sessions` and asset/benchmark
rows with `session`, `source`, `raw`, and its `hash`. No calendar or benchmark is guessed.
Labels include an actual `value` plus protocol/code version and provenance; blinded annotations include `criteria`, frozen `evidence_ids`, and a rubric reference. Metadata-only labels and empty raw market records do not pass readiness. Human review and actual blindness remain evidence requirements, not
facts a boolean flag can prove.

## Readiness and rollout

1. Apply 0066, deploy writers, then query/UI. Verify the inventory above in the target environment.
2. Run offline fixtures and inspect unknown/pending counts before any approved paid trial.
3. Agree #206 material format, model revision/cutoff evidence, budgets and scoring. Unknown
   revision/cutoff is explicit; aliases and anonymization do not prove no contamination.
4. Supply aligned real data and frozen labels before interpreting a real-data pilot.
5. Keep development cases/dates separate; inspect overlapping outcome windows in #206's
   protocol. A 10–20-case pilot validates machinery, not statistical superiority.

This development change does not authorize production deployment, private data extraction,
paid calls or automatic promotion/order execution. #208 stays open for the real-data gates.

Use `python -m inalpha_research.pilot validate-run /path/to/run` to verify call-event
pairing, manifest/evidence/configuration hashes and confirmed ledger settlement. A local
response with an unavailable ledger remains usable diagnostic evidence but does not pass
readiness. `completed` in the ledger denotes a returned provider request, not proof that an
Evolver candidate passed parsing, compilation or evaluation; inspect linked business
outcomes as well. Research JSON validation marks invalid responses directly.


### Research pricing configuration

Set `RESEARCH_USAGE_PRICING` to a JSON array before starting Research. Each item must
specify `provider`, `model`, nonempty `version`, `currency: "USD"`, and finite nonnegative
`input_usd_per_million` / `output_usd_per_million` rates. Rates are frozen into each new
call; changing configuration never reprices historical calls. Set `LLM_MODEL` explicitly
when using pricing snapshots; an empty model name does not infer a pricing match. An unmatched model or
duplicate match remains unknown. Invalid rate values fail startup validation.

Example estimate (peak/non-cache rates, not an invoice):

```dotenv
RESEARCH_USAGE_PRICING='[{"provider":"deepseek","model":"deepseek-flash","version":"deepseek-peak-estimate-2026-10-10","currency":"USD","input_usd_per_million":"0.3","output_usd_per_million":"1.2"}]'
```

Verify current rates with the provider before use. DeepSeek applies cache and time-of-day
discounts; this conservative estimate intentionally does not claim those discounts.
Source checked 2026-10-10: https://api-docs.deepseek.com/quick_start/pricing/.
