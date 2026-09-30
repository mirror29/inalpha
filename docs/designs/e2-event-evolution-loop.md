# ADR · E2 event-driven automatic evolution loop

Status: Accepted behind `EVENT_EVOLUTION_ENABLED`
Scope: PR #164 (`codex/evolution-task27`)

Implementation checked: 2026-09-30. The durable Loop, lease fencing, proposal checkpoints,
atomic handoffs and bounded model authorization are implemented with automated tests. Controlled
real-model execution, restart recovery and full Forward/holdout evidence remain to be validated;
this ADR is not deployment certification. Current priorities are maintained in
[`04-current-state.md`](../04-current-state.md).

## Context

E1 mutates one existing strategy generation. It cannot reliably explore event mechanisms, survive
service restarts across a long Forward window, or prevent validation and sealed-holdout information
from leaking back into proposal. A usable evolution product also cannot require users to understand
campaign IDs, snapshots, generations, or Forward APIs: from a strategy, paper run, or E1 result the
normal command is simply “start evolution”.

## Decision

Inalpha uses one owner-scoped `EvolutionLoop` as the durable workflow aggregate:

```text
target_resolved → baseline_ready → campaign_running → candidate_locked
                → waiting_forward → holdout_running
                → adoption_ready | rejected | insufficient_evidence | failed
```

- Dashboard and Orchestration resolve only a stable target reference. Evolver owns reconciliation,
  leases, fencing, budgets, retries, generation transitions, champion locking and one-shot holdout.
- One activity loop exists per owner and target. Repeated clicks or repeated short commands reuse it.
- Data owns authoritative asset identity and point-in-time event facts. Prompts receive normalized
  fact fields and evidence IDs, never source titles, bodies, URLs, or arbitrary provider strings.
- A campaign freezes bars once, persists the 60/20/20 split, and never refetches historical bars.
  Proposers see discovery evidence; selection sees validation aggregates; sealed holdout is consumed
  once only after Forward passes.
- Five generations each evaluate eight hypotheses with three deterministic implementations. Lane
  fields are executable DSL, not labels. Novelty blends semantic, signal, trade and holding distance.
- The server chooses one champion using evidence gates, Pareto ranking, BH-FDR and deterministic
  tie-breaks. An owner cannot nominate another candidate.
- Paper owns the isolated Forward sandbox. It persists received closed bars, normalized fact IDs and
  fills exactly once; signed evidence alone returns to Evolver. Sandbox state never appears in normal
  accounts, positions, orders or Runner.
- Forward requires 30 days and three independent events, with a 90-day maximum. Passing Forward
  automatically runs the already-committed sealed holdout attempt. Crashes resume the same attempt.
- A graduated result still requires manual adoption. Adoption creates an experimental research asset
  with `runner_eligible=false`; it never promotes, starts Runner, or places orders.

## Trust boundaries

- Event writes and extraction use short-lived purpose/audience-bound service JWTs.
- Every worker write is fenced by its current lease token.
- An explicit owner click or instruction authorizes the bounded research Loop; internal generations
  do not require separate approval. The start grant is owner/operation/config-digest bound and
  valid for five minutes; the 90-day durable authorization and a current lease guard later model
  requests. Credential-exchange grants last at most 30 hours and do not replace Loop authority. Credentials
  are fetched in memory, never stored in Loop records. Each network attempt reserves its frozen
  maximum cost first; missing usage receipts retain that reservation.
- Owner-scoped reads return 404 outside the owner boundary.
- `GET /capabilities` is the only E2 availability authority; every mutation checks it server-side.
- Terminal Paper evidence is HMAC-authenticated over sandbox, campaign, candidate, status, event
  count, evidence version and metrics.

## Recovery and idempotency

Campaign, Forward, and holdout steps are persisted before external work. Workers use `SKIP LOCKED`,
expiring leases, fresh fencing tokens and compare-and-swap transitions. Provider/network errors are
retryable without changing candidate or attempt identity. Deterministic evaluation errors consume the
single holdout attempt and reject the campaign. Proposal checkpoints reuse committed proposals,
and immutable phase handoffs plus their UI events commit atomically. This does not promise exactly
once model billing across every crash: an uncommitted provider response may require a bounded retry.

## Consequences

The workflow is slower and stores more audit data, but results are replayable and leakage-resistant.
Feature rollout remains disabled by default until migration, service, Compose, browser and controlled
model smoke gates pass. Multi-asset portfolios, 1m/5m execution, order-book fills, MAP-Elites and
Island Model remain out of scope.
