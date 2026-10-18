# Agent Onboarding — Inalpha

> Audience: an AI coding agent (or a new human contributor) picking up this repository cold.
> Derived from a full read-only familiarization pass of HEAD `4822bb9` (2026-10-14).
> This document is a map and a contract, not a substitute for the code. Where this file and the
> code disagree, **the code wins** — and fix this file in the same change.

## 1. Working agreements

These are binding for any agent working in this repository.

- Never commit or document sensitive information. No API keys, secrets, private keys, wallet material, tokens, passwords, or personal data in code, tests, fixtures, docs, commit messages, or logs. Adapter credentials stay in environment variables. Read credentials from the environment only, and never add an environment variable to a test in a way that makes the test depend on it.

- Always commit and push code after a code change. A code change is not finished when the file is written; it is finished when the change is committed and pushed.

- Fix defects on the spot, unless the owner decides otherwise. When a defect is found in the code being worked on, fix it as part of the same change rather than reporting it and moving on. Defer only when the owner explicitly decides to defer, and record the deferral.

- Never make code changes unless they are defects. Keep the change surface limited to defect fixes. Do not add unrelated features, refactors, renames, abstractions, drive-by cleanups, or speculative improvements. Note unrelated observations instead of changing them.

- Always keep corresponding documentation in sync with code changes. A code change updates the documents and generated artifacts that describe it, in the same change. This includes concept and integration docs, coding-standards guidance, and the generated stubs and docstrings. Maintainer-owned files such as RELEASES.md are updated by maintainers, not by agents.

- **Always do an extensive and deep web search before deciding, or when the answer is unknown.** When a decision has more than one defensible option, or the answer is not already established by this repository's own code, tests and docs, search the web FIRST — broadly, across several independent sources — and read the PRIMARY source before deciding: the vendor/API documentation, the specification, the issue tracker, or the pinned dependency's own source at the version this repository locks. Confirm that version, because an API that changed after an old forum answer sends you the wrong way, and cross-check any claim the decision rests on against a second independent source. Record what the search established, with its sources, in the change notes (the commit message, or the doc the change touches) whenever it determined the outcome. "Found nothing" is a result: report it, and label the choice an assumption — never present a guess as researched. A search never overrides evidence this repository already holds: for internal behavior the code, tests and stored artifacts are ground truth, and a run you measured outranks anything published.

Repository-level rules that layer on top of the above live in [`AGENTS.md`](AGENTS.md) (hard constraints,
naming, "don't touch" list, GitHub collaboration language) and [`CONTRIBUTING.md`](CONTRIBUTING.md)
(branch model, commit conventions, review flow). Read both before your first change.

---

## 2. What this repository is

**Inalpha** is an AI-agent-orchestrated, multi-kernel quant research and paper-trading framework.
It is a multi-service **application**, not a library: a Next.js operator console drives a Mastra
(TypeScript) orchestration layer, which drives five independent Python FastAPI services over HTTP.

| Layer | Component | Port | Role |
|---|---|---|---|
| Entry | `apps/dashboard` | 3001 | Operator console: auth, same-origin BFF, docked agent chat, portfolio / runners / risk / factors / lab / evolution pages |
| Entry | `apps/web` | — | Static marketing site (`output: "export"`) |
| Entry | `mastra dev` playground | 4111 | Live trace / debug surface for the orchestrator |
| Orchestration | `packages/orchestration` | 4111 | One orchestrator agent, ~60 HTTP tools, hooks, permissions, plan/exec proxies, scheduler, MCP, skills, evals |
| Kernel | `services/data` | 8001 | Market data, fundamentals, news, web search, FX, point-in-time event ledger |
| Kernel | `services/paper` | 8002 | Event-driven kernel, backtest, live runner, strategy authoring sandbox, plan/exec, risk, wallets |
| Kernel | `services/research` | 8003 | Multi-analyst + bull/bear/risk debate → `StrategyHint` |
| Kernel | `services/factor` | 8004 | Factor library, IC/decay, DSL discovery, panel scoring (signals only) |
| Kernel | `services/evolver` | 8005 | E1 single-generation mutation; E2 event campaigns + persistent `EvolutionLoop` |
| Shared | `services/_shared` | — | `inalpha_shared` (JWT, psycopg pool, errors, logging, usage ledger) + `_shared/llm` |
| Persistence | Postgres 17 + TimescaleDB, Redis | — | All business state; `bars` / `ticks` hypertables; Mastra memory is LibSQL/SQLite |

Status: self-declared **alpha**. Real-money trading is explicitly out of scope — the ceiling is
simulated ("paper") accounts. `docs/04-current-state.md` is the authoritative status page: it states
that the E2 engineering validation and the real positive production acceptance are **not** complete.

---

## 3. Ground rules — do not break these

These are enforced in code today; each maps to a test or an explicit raise. Treat violations as defects.

1. **The LLM has no order path.** The only sanctioned route is `trade.create_plan → approve_plan →
   execute_plan`, with a one-time, TTL-bound `approval_token` minted and consumed by `services/paper`.
   Direct-submit tool ids are denied *and* absent from the model's tool list.
2. **Permission resolution is deny → allow → ask → default(`ask`)**, and a layer merge can never
   downgrade a `deny`. Every `ask` requires a verified owner subject and a stable session id, else
   fail-closed with no side effects.
3. **Risk is enforced before execution.** HTTP path: `RiskGuard.enforce` takes its own DB connection
   and commits the lock *before* raising, so the lock survives the caller's rollback. The automated
   live runner is fail-closed without a guard factory. Note the asymmetry: if the HTTP risk config
   fails to load, the manual HTTP path is **fail-open** — do not widen that hole.
4. **No look-ahead.** Orders submitted on bar N fill on bar N+1 at the next open. Market events are
   visible only when `available_at <= bar.bar_known_at`. The live runner consumes only closed bars.
5. **Simulation time never goes backwards** (`TestClock.set_time` / `advance_time` raise).
6. **Order state machine legality and overfill rejection** are enforced on the domain model, not at
   the edges.
7. **No naked short, no cash overdraw, perp initial margin ≤ wallet** — checked before execution *and*
   re-checked under `FOR UPDATE` inside the fill transaction.
8. **Per-run wallets are isolated**; lock order is account → wallet → position; one running run per
   candidate per owner; per-account run cap applies.
9. **Promotion requires a completed backtest**; starting a live run requires `status = 'promoted'`
   plus owner match.
10. **Candidate source passes three gates** — AST audit, restricted loader, `Strategy` contract check —
    re-run at authoring, at backtest, and at live build. Guard-order forgery by literal string is rejected.
11. **Evolution is authorized, owner-scoped, fenced and budgeted.** No auto-promote, no auto-start,
    no auto-order. Adopted artifacts are literally `runner_eligible: false`. E1 needs per-run explicit
    approval; E2 behind `EVENT_EVOLUTION_ENABLED` authorizes a bounded loop, not generations.
12. **Secrets never enter run records, logs, or the browser.** Per-user LLM keys are encrypted at rest,
    resolved per owner at call time, and cleared after the credential exchange.
13. **Freshness is a correctness property.** Treat "looks new but is stale" as a bug; reason about
    `bars[-1].ts` distance from `as_of`, never about bar count.

---

## 4. Repository map — where to look for what

| You are asked to change | Primary location | Also affected |
|---|---|---|
| A model-visible tool | `packages/orchestration/src/tools/<area>.ts`, bucket in `tools/index.ts` | `packages/orchestration/config/permissions.default.yaml`, `mastra/tool-wiring.ts`, tool-description style |
| Guardrail policy | `packages/orchestration/config/permissions.default.yaml` (+ `permissions/defaults.ts` mirror) | `hooks/with-hooks.ts`, `permissions/pending.ts`, dashboard approval API |
| Order / approval flow | `services/paper/src/inalpha_paper/api/trade_plans.py`, `storage/trade_plans.py` | `orchestration/src/tools/trade-plan.ts`, `api/orders.py`, `live_runner.py` |
| Kernel semantics (bars, fills, clock) | `services/paper/src/inalpha_paper/engine/backtest.py`, `execution/*`, `kernel/*` | `engine/live_session.py`, every strategy, factor/evolver evaluation |
| Live runner | `services/paper/src/inalpha_paper/live_runner.py` | `api/strategy_runs.py`, `storage/run_wallets.py`, dashboard `/api/runners*` |
| Risk rules | `services/paper/src/inalpha_paper/execution/risk_rules/*`, `configs/risk_rules.toml` | `risk_guard.py`, `api/orders.py`, `api/trade_plans.py`, `live_runner.py` |
| Data connectors / freshness | `services/data/src/inalpha_data/connectors/*`, `api/{bars,backfill,ticker}.py` | all consumer clients, orchestrator prompts |
| Factors | `services/factor/src/inalpha_factor/{adapters,engine,effectiveness}.py` | `paper/factor_patrol.py`, `factor.*` tools, dashboard factors page |
| Evolution (E1) | `services/evolver/src/inalpha_evolver/runtime/{executor,generation,slots}.py`, `mutator/*` | `paper/strategy_authoring/*` (imported!), `evolver.*` tools |
| Evolution loops/campaigns (E2) | `runtime/{loop_manager,loop_campaign,campaign}.py`, `loop_fencing.py`, `api/{loop_start,campaign_routes}.py` | feature flag, data event snapshots, paper Forward sandbox |
| Auth / per-user keys | `services/_shared/src/inalpha_shared/auth.py`, dashboard `lib/{session,encryption,backend}.ts` | `orchestration/src/{auth.ts,mastra/identity.ts}` |
| DB schema | `infra/migrations/versions/<new>.py` (Alembic, raw `op.execute` SQL) | `storage/*` in the owning service |
| Console UI | `apps/dashboard/src/app/[locale]/**`, `src/components/**` | `apps/dashboard/design.md`, shared `components/ui/*` |
| Public docs | `README*.md`, `docs/00-05`, `AGENTS.md`, `CLAUDE.md` | run `bash scripts/check-consistency.sh` |

---

## 5. Execution paths you must understand before editing

**Chat turn to tool call**

```
browser -> dashboard /api/copilotkit (BFF: decrypt owner LLM key, inject header)
  -> mastra :4111 -> identity middleware (verify Bearer, bind user LLM config)
    -> orchestrator -> tool.execute  [wrapped by hooks]
         PreToolUse hooks -> PermissionEngine.authorize -> [ask: approval store]
         -> clients/<service>.ts -> HTTP to the Python service
         -> PostToolUse / PostToolUseFailure hooks
```

**Order path (the only route to a trading side effect)**

```
trade.create_plan -> POST /plans (pending, TTL)
trade.approve_plan -> POST /plans/{id}/approve -> one-time approval_token
trade.execute_plan -> POST /plans/{id}/execute -> consume token -> RiskGuard.enforce
                   -> order insert + positions + cash in one transaction
```

**Live runner bar loop**

```
POST /strategy_runs -> check promoted + owner + cap + allocation
  -> background task -> per run: poll closed bars -> dedup by last_bar_ts
     -> per bar: notional cap -> DB risk enforce -> spot/perp prechecks
        -> order executor -> single transaction (account lock, status re-check,
           FOR UPDATE clamps, plan create/approve/consume, order insert,
           fills -> positions + cash, decision row)
```

**Backtest**

```
POST /backtest -> validate -> data client (user Bearer forwarded) -> fresh backfill
  -> process pool -> evaluate candidate (+ buy-and-hold baseline) -> fitness
  -> optional holdout block -> persist run + trades in one transaction -> report
```

---

## 6. Commands

```bash
# install
cd packages/orchestration && pnpm i && cd ../..
for s in data paper research factor evolver; do (cd "services/$s" && uv sync); done

# database
cp infra/.env.example infra/.env && (cd infra && docker compose up -d)
(cd infra/migrations && uv sync && uv run alembic upgrade head)

# run everything locally (writes local-only Ed25519 keys into .tmp/)
bash scripts/dev.sh            # up | logs | stop | status
cd apps/dashboard && pnpm dev  # console on :3001

# self-hosted Docker path
bash scripts/selfhost.sh init|up|status|logs|down

# verification (see the ladder in section 7)
bash scripts/check-consistency.sh          # cross-file docs/code oracle, C1-C10
cd packages/orchestration && pnpm typecheck && pnpm vitest run
cd services/<svc> && uv run ruff check . && uv run pytest
cd apps/dashboard && pnpm typecheck && pnpm test

# operations
cd packages/orchestration && pnpm scheduler:trigger --list
scripts/deploy.sh                          # prod (flock + compose pull/up --wait)
```

---

## 7. Verification ladder

Never claim a change works from a compile or a single unit test. Climb as far as the change warrants:

1. `bash scripts/check-consistency.sh` — the repository's own doc/code oracle (C1–C10).
2. `uv run ruff check .` in the service you touched (CI enforces this).
3. `uv run pytest` in every touched service. Note: paper has ~95 test files, evolver ~60; they are the
   real regression nets for this system.
4. `pnpm typecheck && pnpm vitest run` in `packages/orchestration` if you touched it; `pnpm typecheck && pnpm test`
   in the touched app.
5. **Smoke the real path** — `bash scripts/dev.sh` plus the console or `mastra dev`, then exercise the
   changed behavior. This repository explicitly separates "code + automated tests" from
   "real-environment acceptance" (see `docs/04-current-state.md`); leases, fills, and approvals behave
   differently in a live stack than under fixtures.
6. Python type checking is **best-effort**, and nothing in this repository claims otherwise: CI runs
   `uv run mypy . || true` and `CONTRIBUTING.md §9` says "ruff must pass, mypy best-effort". Do not
   report mypy as passing CI.

---

## 8. Configuration

- **Model**: one root `.env` shared by TypeScript and Python. `scripts/dev.sh` sources it; Python
  services read it by repo-root path via pydantic-settings; dashboard reads it through
  `loadRootEnv()`. Legacy per-service `.env` files still act as cwd-level overrides.
- **Never print, commit, or paste values.** `.env.example`, `infra/.env.*.example` are templates with
  placeholders only. Keys that matter: `DATABASE_URL`, `JWT_SECRET`, `AUTH_ENABLED`,
  `LLM_PROVIDER` + provider key, `LLM_CONFIG_ENCRYPTION_KEY`,
  `EVOLUTION_CREDENTIAL_{PRIVATE,PUBLIC}_KEY_B64` (required for evolution; fail-closed without),
  `EVENT_EVOLUTION_ENABLED` (E2 flag, default false), `SCHEDULER_ENABLED` (default false),
  `FRED_API_KEY`, `CONSTITUENT_SNAPSHOT_INDICES`.
- Environment defaults differ deliberately: `AUTH_ENABLED=false` in the dev template, `true` in the
  production template. Single-worker assumptions are load-bearing (paper login throttle, live-runner
  tasks, pending approvals, evolver managers) — only a warning is printed.

---

## 9. Documentation you must not trust

Where documentation and code disagree, the code wins. A read-only audit of HEAD `4822bb9` found the
misstatements below. Anything marked **fixed** was corrected on branch `docs/agent-onboarding` — read
that branch's commits for the exact edits, and re-verify any other page against the code.

Still open (deliberately not changed — each needs an owner or design decision):

| Claim | Reality |
|---|---|
| Private-ADR policy ("ADRs live in gitignored `docs/miro/`; public docs must not cite them") | `docs/designs/e2-event-evolution-loop.md` is ADR-shaped and `docs/04` cites ADR ids; the consistency check (C2) only scans `docs/00,01,03`, so it cannot catch this |
| README: "79 factors" | No such count in source; establishing it needs a runtime `GET /catalog` |
| Release metadata (`CITATION.cff` version, `SECURITY.md` alpha tag) | Tag set stops earlier than claimed — maintainer-owned, do not edit |
| `packages/orchestration` `pnpm lint` | A stub (`echo 'TODO: add eslint or biome'`); no linter runs in CI for that package |
| `services/evolver/runtime/executor.py::_service_token` | Signs `{sub, exp}` with no `token_use` / audience / purpose, unlike every other evolver outbound token. Data's `/bars` only needs a valid signature + `sub`, so it works — but such a token is also accepted as a *user* token by any service sharing `JWT_SECRET`. Deviation, not a proven breach; tightening it needs a coordinated data-side change |

Corrected in this branch (listed so nobody re-reports them as open):

| Was | Now |
|---|---|
| `docs/01-architecture-overview.md`: agents are orchestrator + trader + risk | single orchestrator agent |
| `docs/01-architecture-overview.md`: `memory/` is PostgresStore | LibSQL store under `.data/` |
| Docs/README said 5 lifecycle hook events | six |
| This onboarding doc claimed `CLAUDE.md` / the PR template make mypy a CI red line | no such claim exists in either file; `CONTRIBUTING.md §9` and CI both say best-effort |
| `AGENTS.md` pointed at a non-existent `docs/brand/` | now `docs/00-05` |
| `packages/orchestration/README.md`: orchestrator coordinates trader / risk subagents | single agent |
| `services/paper/execution/*`: promised a `LiveGateway` | no live gateway; docstring now says so |
| `services/paper/api/health.py`: "paper does not touch the DB" | paper is DB-backed; `/health` is liveness-only by design |
| `services/paper/strategy_authoring/fitness.py`: turnover = `turnover / 252` | `(trades / bars_processed) * 100` |
| `services/paper/kernel/clock.py`: `set_timer` framed as a pending D-5 gap | states it is unimplemented and that the live path drives `TestClock` |
| `apps/dashboard/src/i18n/routing.ts` pointed at `src/middleware.ts` | `src/proxy.ts` |
| `services/evolver/.../evaluator/runner.py` implied it used the data service | documents that `bars` is empty; production path is `FrozenDatasetEvaluator` |
| `services/data/src/inalpha_data/cache_manager.py`: dead module with an import that would raise | deleted |

---

## 10. Risk areas (treat as fragile)

1. `services/evolver` imports `inalpha_paper` in-process (~25 modules) — the only cross-service Python
   coupling, with no contract test guarding it. Any paper-internal refactor can break evolver at import time.
2. `services/paper/src/inalpha_paper/live_runner.py` — 1600+ lines of DB + asyncio + risk + plan/exec;
   a documented session/DB divergence risk and heavy reliance on status guards.
3. Fail-open HTTP risk path (see ground rule 3).
4. Single-worker assumptions across four components.
5. Evolution lease/fencing/budget/holdout semantics — dense, mostly documented in evidence receipts.
6. Approval identity (`authSub + sessionId + toolName + inputDigest` plus a frozen LLM snapshot):
   changing input projection either invalidates approved commands or mismatches what was approved.
7. `MessageBus.publish` to an unmatched topic silently returns 0 — a typo'd topic disables a component
   with no error.
8. Hand-written raw-SQL migrations (no autogenerate; revision `0039` missing, `0043` duplicated).
9. Market→venue routing is prompt prose, not code.
10. Wallet/accounting: legacy runs are explicitly "unverified or contaminated"; violating the documented
    lock order risks double-spending capital.

---

## 11. Resolve before assuming

Open items from the familiarization pass that need first-hand confirmation:

- Exact `approval_token` TTL / one-time / quorum mechanics in `paper/storage/trade_plans.py`.
- Whether the dashboard actually sends the injected LLM-config header and mints grants as the two sides assume.
- Whether anything sets `strategy_candidates.status = 'rejected'`.
- Runtime role of `permissions/ask-cache.ts` versus `PendingApprovalsStore`.
- Whether `UserPromptSubmit` hooks are registered anywhere (declared only, so far).
- Real runtime health of the stack, and whether the production claims in `docs/04` /
  `docs/validation/*.json` reproduce.

---

## 12. Definition of done

A change is complete when: the defect or requested change is fixed at its cause (not symptom-suppressed);
every caller and the corresponding documentation (concept docs, docstrings, generated artifacts) are
updated in the same change; the verification ladder in section 7 has been climbed as far as the change
warrants, with the exercised path named; no sensitive material was added anywhere; and the change is
committed and pushed on a task branch (`docs/*`, `fix/*`, `feature/*`, `chore/*`, `test/*`) with an
English Conventional Commit message. `main` and `staging` are protected — they take PRs, not direct pushes.

Unrelated defects and observations found along the way: report them, do not silently fix them.

## 13. Further reading

- [`AGENTS.md`](AGENTS.md) — hard constraints, naming, task routing, "do not touch" list
- [`CLAUDE.md`](CLAUDE.md) — project memory and the local pre-push checklist
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch model, staging rules, commit/PR conventions
- [`docs/00-context.md`](docs/00-context.md) — goals, boundaries, borrowed designs
- [`docs/01-architecture-overview.md`](docs/01-architecture-overview.md) — layers and invariants (see section 9 for what is stale)
- [`docs/03-kernel-design.md`](docs/03-kernel-design.md) — kernel abstractions (Phase C baseline plus deltas)
- [`docs/04-current-state.md`](docs/04-current-state.md) — authoritative status and priority list
- [`docs/05-paper-wallet-accounting.md`](docs/05-paper-wallet-accounting.md) — wallet isolation and rollout rules
- [`docs/usage/README.md`](docs/usage/README.md) — unified LLM usage ledger contract
