# Isolated paper run accounting

Each new strategy run owns a wallet and a position book. Starting a run transfers actual available cash from the owner's main account; it does not create additional capital. The default allocation is the smaller of available cash and 10,000 in the account's reporting currency. Verified conversion rates are required, and legacy perpetual margin and negative currency balances reserve purchasing power.

`account_id` remains the authentication boundary. JWT ownership is checked before wallet lookup, and database composite foreign keys bind every run position, order and closed trade to the wallet's owner. Manual orders use the main/legacy book (`run_id IS NULL`). Different candidates may trade the same symbol independently. A partial unique index prevents two running instances of the same candidate for one owner.

Lock order is account → wallet → position. Allocation, execution, reset and release follow this order. Reset is rejected while any wallet remains unsettled. Backtests and Evolution Forward retain their separate storage and never receive a live wallet.

Wallet equity minus original capital is the authoritative net PnL, including trading fees, liquidation fees and funding. Restart restores the wallet cash and this run's own position; historical warmup updates indicators without executing trades. Position and cash valuation reads share one PostgreSQL snapshot, and revision checks prevent an older valuation overwriting a later fill.

`GET /strategy_runs/{id}/wallet` exposes balances, positions, PnL components, valuation timestamp and warnings. Stale prices or unavailable FX retain the last trusted value. Totals include main-account equity and unsettled run-wallet equity exactly once. Internal capital transfers are excluded from external deposits and do not increase profit.

Stopping leaves positions and capital intact. `POST /strategy_runs/{id}/resume` explicitly resumes the same owned, unreleased wallet and promoted candidate without allocating again. Legacy runs cannot resume. An explicit stop acknowledges an errored wallet while preserving its error log.

`POST /strategy_runs/{id}/release_capital` requires a stopped, flat book and fresh conversion rates; it returns cash once and retains final equity and performance. Perpetual books must finish outstanding funding reconciliation before release. The funding worker continues processing stopped books, using published historical rates and marks with exact settlement timestamps. Cash changes and unique funding events commit together. Failed history reads do not advance the cursor and are explicitly marked pending. The worker excludes the latest five minutes and retries every minute, replaying the full wallet history in seven-day pages. Event keys prevent duplicates and allow records published late to be charged after a previous empty response.

## Legacy rollout and audit

1. Back up PostgreSQL, verify the compressed archive and remote copy, and stop every legacy run through the authenticated stop endpoint.
2. Deploy only a fixed SHA after CI and image builds pass. Migration `0058` pauses legacy runs, saves their original statistics and keeps their positions, executed orders and cash in the historical book. It never funds a replacement wallet.
3. Run `uv run --project services/paper python scripts/audit-paper-books.py --output /secure/path/audit.json`. The report follows decision → closing order → closed trade → opening order chains. Symbol or timestamp matching alone cannot establish ownership.
4. With reviewed evidence, `--apply-metadata` writes only separate legacy attribution records. Repeating this operation cannot duplicate accounting entries. `--confirmed-contamination RUN_UUID --note EXPLANATION` records an explicitly confirmed incident; original performance and transactions remain available. Never reverse an executed close to make a historical run appear flat or unfilled.
5. Confirm old runs remain stopped and old positions are shown as no longer automatically managed. New books are created only at the user's request using actual available main-account cash.
6. Validate service health, ledger reconciliation and a deterministic simulated start/fill/restart round trip without a model call. Keep operational evidence outside the public repository.

## Rollback

Pause all new runs before changing application versions. Keep wallet tables, positions and cash events. Migration downgrade deliberately refuses to remove a schema containing wallets. Never allow an old runner to resume or adopt an isolated book. A legacy application must remain unable to start paper runs until it supports the new schema and ownership rules.
