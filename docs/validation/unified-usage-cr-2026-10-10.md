# Unified usage ledger: review follow-up

Review: PR #213 automated review of dd84b1178955 (comment 6093328358).

- Decimal precision: fixed display formatting to operate directly on the PostgreSQL decimal string. Regression covers a large value with twelve fractional digits and tiny nonzero costs.
- Undefined API-error usage: the reported null dereference was not reproducible; normalization already replaces missing input with an empty object. Added explicit error-handler idempotency coverage. The real Agent regression exposed a separate issue: output processing without a finish reason could settle a failed request as completed. It now settles as failed, preserving unknown usage.
- SDK retries: disable implicit retries only for recorded calls; unrecorded callers retain the SDK default. Declared the shared client's missing OpenAI SDK dependency and synchronized locks.
- Owner identity: verified Dashboard subject, Research user_id and Evolver requested_by_sub all originate from the unchanged JWT sub. Added PostgreSQL coverage for the same prefixed subject across chat/research/evolver and rejection of another owner. The earlier real-provider acceptance additionally verified qa:bob across these services and qa:alice isolation.
- Connections: Research uses a lifecycle-managed pool capped at ten connections with a five-second acquisition timeout. Evolver reuses its existing bounded service pool. Each ledger transaction is short and completes before network access. Evolver attribution lookup and insert share one transaction. Standalone stores retain direct-connection support for scripts. Statement timeout remains local to the ledger transaction.

Validation:

- Research: 206 passed, including three real PostgreSQL tests against the dedicated disposable database.
- Evolver targeted ownership/usage/LLM tests: 14 passed, 2 optional tests skipped; real PostgreSQL attribution test passed.
- Shared LLM: 11 passed with actual SDK construction, no paid model requests.
- Orchestration usage/processor: 17 passed; TypeScript check passed.
- Dashboard formatting: 1 passed.
- Consistency: 19 passed, 12 existing warnings, zero failures.
- Independent follow-up review found no remaining blocking issue.

Additional reviewer uncertainties were checked: chat-invocation-scope.ts is tracked; HttpClient.post accepts a third headers argument; ResearchPlan declares research_id. This round used automated and database regressions; it did not repeat the previously documented paid-model acceptance.
