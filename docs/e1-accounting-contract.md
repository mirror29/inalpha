# E1 Search Accounting and Evaluation Evidence Contract

**Status:** Proposed  
**Scope:** E1 evolutionary accounting and evaluation evidence  
**Related issue:** [mirror29/inalpha#225](https://github.com/mirror29/inalpha/issues/225)  
**Inspected baseline:** `bb3a46cc80f201b4282afd83cab740a57dc6326f`

## 1. Purpose

This document defines a proposed accounting contract for Inalpha's E1 evolutionary evaluation process.

The objective is to make the evolutionary search process observable and reproducible without confusing operational execution counts with statistical trial counts.

The contract distinguishes:

- Runs and run retries.
- Relationships between runs and search-family groupings.
- Candidate slots and candidate proposals.
- Distinct candidate source artifacts.
- Evaluation cases and evaluation attempts.
- Confirmed evaluator invocations and uncertain execution.
- Candidate lineage and selection history.
- Observable counts and estimated effective trial counts.
- Evaluation evidence and statistical correction results.

The contract must preserve unsuccessful, rejected, duplicate, cancelled and uncertain outcomes.

It must not infer statistical independence from different run IDs, source hashes or shared dataset identity.

## 2. Scope and exclusions

### Included

- Run and retry identity.
- Relationships between runs.
- Candidate proposal and source accounting.
- Evaluation input identity.
- Evaluation-attempt accounting.
- Duplicate and failure representation.
- Selection and lineage evidence.
- Statistical evidence availability.
- Explicit correction-result status.
- Deterministic acceptance examples.

### Excluded from this contribution

- Implementing DSR or PBO integration.
- Estimating effective trial counts.
- Changing fitness or candidate-selection logic.
- Introducing a new statistical significance gate.
- Changing LLM providers or mutation behavior.
- Changing owner isolation, permissions, approval, promotion or execution boundaries.
- Enabling live trading or requiring paid LLM runs.

The first contribution establishes the accounting and evidence contract. Statistical integration remains a separate implementation scope.

## 3. Current E1 implementation baseline

This section records behavior observed in the inspected source revision:

`bb3a46cc80f201b4282afd83cab740a57dc6326f`

It describes existing behavior and must not be interpreted as the proposed accounting contract already being implemented.

### 3.1 E1 execution path

The current managed E1 execution path is:

```text
EvolutionRunManager.start()
        |
        v
dispatch_runs()
        |
        v
run_queries.claim_next()
        |
        v
EvolutionRunManager._execute()
        |
        v
execute_managed()
        |
        v
execute_run()
        |
        v
DataClient + FrozenBarsLoader
        |
        v
execute_frozen_run()
        |
        v
execute_generation()
        |
        +---- Seed evaluation
        |
        +---- Baseline evaluation
        |
        +---- Candidate slot allocation
                    |
                    v
               Mutation
                    |
                    v
              persist_mutation()
                    |
                    v
               evaluate_slot()
                    |
                    v
          FrozenDatasetEvaluator.evaluate()
                    |
                    v
          evaluate_strategy_source()
                    |
                    v
              Backtest engine
                    |
                    v
             EvaluationSnapshot
                    |
                    v
            Candidate persistence
```

The manager handles queue dispatch and managed run execution/finalization.

The generation runtime handles seed and baseline evaluation, candidate-slot allocation, mutation and candidate evaluation.

### 3.2 Existing E1 accounting

The inspected implementation already provides:

- Run identity and idempotency.
- Run retry lineage through `experiment_id`, `retry_of_run_id` and `attempt_number`.
- Persisted frozen dataset manifests.
- Candidate-slot records.
- Candidate source hashes.
- Candidate outcomes and failure reasons.
- Evaluation snapshots and fitness.
- Overfitting-risk classification.

However, important distinctions remain:

1. Candidate-slot insertion currently hardcodes `generation=1`.
2. The candidate model includes `parent_id`, but the inspected production slot-insertion path does not populate it.
3. Exact source-duplicate detection is scoped to the same run.
4. The duplicate branch does not preserve the duplicate source hash or a direct reference to the original candidate.
5. The candidate summary's `attempted` count counts candidate-slot rows, not confirmed evaluator invocations.
6. Candidate slots are stateful records. Recovery can resume an existing slot at the evaluation stage without creating a separate append-only evaluation-attempt record.
7. The frozen evaluator computes behavior fingerprints, but the standard E1 slot-persistence path does not persist the behavior fingerprint.
8. The compact evaluation snapshot contains aggregate metrics and validation diagnostics, not the complete equity curve and fills.

These findings explain why existing candidate and run records cannot automatically be interpreted as a complete evaluation-attempt or statistical-trial history.

### 3.3 Existing evaluation diagnostics

`evaluate_strategy_source()` performs source auditing, executes the candidate through the supplied engine runner, and produces fitness and validation metrics.

`validation_from_report()` divides the individual evaluation's existing equity curve into train and holdout segments. It can also calculate bootstrap Sharpe confidence intervals.

This is an evaluation-level diagnostic.

The segment named `holdout` is not automatically an untouched search-wide holdout and does not account for repeated adaptive candidate selection.

The resulting overfitting-risk classification is also not equivalent to a search-level DSR or PBO correction.

### 3.4 Separate CV and DSR path

In `services/paper/src/inalpha_paper/engine/backtest.py`, the separate `run_cv_backtest()` function performs evaluations across configured cross-validation splits.

It constructs separate engines and strategies for the splits, assembles out-of-sample paths and calculates Sharpe statistics.

The function then sets:

```python
n_paths = len(sharpe_per_path)
```

and passes:

```python
n_strategies=n_paths
```

to `deflated_sharpe_ratio()`.

This is the number of cross-validation paths produced by that CV execution. It is not automatically the number of evolutionary candidates generated or evaluated by E1.

The separate robustness utilities also include:

- Deflated Sharpe Ratio.
- Probability of Backtest Overfitting using CSCV.
- Bootstrap Sharpe confidence intervals.

The PBO utility expects aligned returns across multiple strategies.

These utilities demonstrate that statistical functionality exists elsewhere in the repository. They do not establish that the managed E1 evolutionary candidate population is passed into search-level DSR/PBO correction.

At the inspected revision, the E1 generation, slot and candidate evaluation path does not expose evolutionary trial-count context to `evaluate_strategy_source()` or invoke the identified population-level CV/DSR/PBO correction path.

### 3.5 Baseline conclusion

The existing implementation provides meaningful run, candidate, dataset and evaluation records.

However, the inspected E1 path does not yet provide a complete, append-only accounting chain connecting:

**Search relationships → proposals → evaluation cases → evaluation attempts → selection evidence → statistical eligibility → correction results.**

The proposed contract defines the distinctions and evidence needed to establish that chain.

It does not claim that the missing capabilities already exist.

## 4. Terminology

### 4.1 Run

One execution of an E1 evolutionary workflow, identified by its `run_id`.

### 4.2 Run retry

A subsequent execution attempt associated with an existing experiment identity.

The current runtime records:

- `experiment_id`
- `retry_of_run_id`
- `attempt_number`

These fields represent run retry lineage. They do not, by themselves, establish a broader search relationship.

### 4.3 Search relationship

A directed, evidence-backed relationship between two runs indicating that one run continued, reused or was influenced by the search activity of another.

Relationship records must identify:

- The run whose search activity is being described.
- The related source run.
- The relationship type.
- Supporting evidence where available.

The direction must be explicit.

In a relationship record, `from_run_id` identifies the run that continued or reused earlier search activity. `to_run_id` identifies the earlier run from which that activity originated.

A shared dataset is recorded as data provenance. It is not, by itself, a search-history relationship.

A confirmed search relationship establishes a connection in search history. It does not automatically establish statistical dependence or independence under every statistical method.

### 4.4 Search family

A search family is a derived grouping of one or more runs connected through confirmed, family-connecting search relationships.

For family membership, confirmed connecting relationships are treated as graph connectivity, regardless of their stored direction. The original relationship direction must remain available as provenance.

All runs connected through a path of confirmed, family-connecting relationships belong to the same derived search family.

A separately initiated search may form a singleton search family when its separate origin is supported by recorded evidence.

A new run ID, the absence of a recorded relationship, or a shared or different dataset identity is not sufficient by itself to establish a separate search family.

Where the available evidence cannot establish whether a run belongs to an existing family or represents a separately initiated search, its family membership remains unresolved.

Search-family membership describes search lineage. It is not an effective-trial estimate and does not establish statistical independence.

The contract does not require a permanent `search_family_id` in its first version. Family membership may be derived from the recorded relationship graph and documented search-start evidence.

### 4.5 Generation

An evolutionary iteration within a search.

A generation is not equivalent to a run retry or evaluation attempt.

### 4.6 Candidate slot

An allocated position in a run's candidate-generation budget.

A slot is an operational record. It does not necessarily represent a generated candidate or completed evaluation.

### 4.7 Mutation attempt

An initiated mutation operation intended to produce a candidate source.

A mutation attempt may fail or produce no change.

### 4.8 Candidate proposal

A candidate source produced by a mutation operation.

A proposal remains relevant to accounting when it is subsequently rejected or identified as a duplicate.

A mutation operation that fails to produce a candidate source is a mutation attempt, not a completed candidate proposal.

### 4.9 Candidate source

An identifiable strategy-code artifact.

Its exact source hash identifies its content.

Different source hashes do not automatically establish independent statistical trials.

### 4.10 Evaluation case

One candidate source evaluated against a particular set of evaluation inputs.

Its identity depends on:

- Candidate source identity.
- Frozen dataset identity.
- Evaluation configuration.
- Evaluator implementation revision.
- Engine implementation revision.
- Other relevant runtime components capable of changing evaluation behavior.

### 4.11 Evaluation attempt

A uniquely identifiable scheduled or initiated effort to evaluate an evaluation case.

An attempt retains its own lifecycle and outcome. Multiple attempts can refer to the same evaluation case.

### 4.12 Evaluator invocation

A confirmed entry into the candidate evaluator boundary.

For the inspected E1 execution path, the proposed boundary is entry into `FrozenDatasetEvaluator.evaluate()` for a candidate evaluation, reached through `evaluate_slot()`.

Creating an attempt record, allocating a candidate slot, scheduling evaluation or entering `evaluate_slot()` does not, by itself, confirm that the evaluator was invoked.

A confirmed invocation requires durable evidence that entry into `FrozenDatasetEvaluator.evaluate()` occurred.

The candidate evaluator invocation and the subsequent `evaluate_strategy_source()` and `run_engine()` execution stages are distinct events.

Confirmation of evaluator entry does not, by itself, establish that the backtest engine started or that evaluation completed successfully.

The current E1 implementation does not persist this as an append-only evaluator-invocation event. The definition describes the proposed accounting boundary, not existing runtime instrumentation.

### 4.13 Statistical trial

A method-specific inferential unit.

It is not automatically equivalent to a run, retry, generation, candidate slot, proposal, distinct source hash, evaluation case or evaluation attempt.

Its definition and estimation depend on the statistical method and its assumptions.

## 5. Relationships between runs and search families

Runs must retain their individual identities while recording known relationships to earlier search activity.

The contract must distinguish search-history relationships from shared evaluation-data provenance.

### 5.1 Search relationship types

Relevant confirmed relationship types include:

| Relationship type | Meaning |
|---|---|
| `continuation` | A run continues an earlier search. |
| `reused_candidate` | A candidate artifact originating from an earlier run is reused as part of the later search. |
| `reused_result` | An evaluation result from an earlier run is reused by the later run. |
| `reused_feedback` | Feedback derived from an earlier run influences later candidate generation or evaluation decisions. |
| `informed_selection` | Results from an earlier run influence candidate selection in the later run. |

These are conceptual relationship categories, not a requirement to implement a particular database enum or schema.

A confirmed relationship must identify the relevant runs and provide supporting provenance where available.

A matching candidate source hash alone does not prove that one run reused the other's candidate. The relationship requires evidence of artifact provenance or actual reuse.

Likewise, the appearance of the same evaluation data in multiple runs does not establish that one run continued or reused the search activity of another.

The category `unknown` must not be treated as a confirmed relationship type.

### 5.2 Search-family derivation

For the first contract, the following confirmed relationships connect runs into the same derived search family:

- `continuation`
- `reused_candidate`
- `reused_result`
- `reused_feedback`
- `informed_selection`

These relationships establish a documented connection in search history.

Family membership is derived using the connected components of the confirmed relationship graph.

The records must preserve relationship direction even though family grouping treats confirmed connecting relationships as undirected connections.

For example:

- Run B continues Run A.
- Run C reuses feedback from Run B.
- Runs A, B and C belong to the same derived search family.

The family grouping is transitive. A run does not need a direct relationship with every other member if a confirmed relationship path connects them.

A relationship discovered after the original runs were recorded may change the derived grouping. The underlying relationship records and their evidence must remain available.

A permanent `search_family_id` is not required for this first contract. A derived family key may be used for presentation or querying, but must not replace the underlying relationship evidence.

### 5.3 Shared dataset identity

Dataset provenance must be recorded separately for every run.

Relevant evidence includes:

- Frozen dataset manifest identity.
- Dataset content hash.
- Data epoch or version where applicable.
- Relevant data-source provenance.

Two runs may use the same frozen dataset without belonging to the same search family.

Shared dataset identity alone:

- Does not create a search relationship.
- Does not establish search-family membership.
- Does not establish that the searches were separate.
- Does not establish statistical independence.

A run that reuses prior evaluation results or search feedback has a search-history relationship even when its dataset is unchanged.

A run that merely evaluates candidates against the same dataset does not thereby become part of another run's search family.

### 5.4 Separate-search evidence

Evidence supporting a separately initiated search must be recorded independently from search-relationship records.

Relevant evidence may include:

- Search-start provenance.
- Initial candidate or seed provenance.
- Initial search configuration.
- The source of candidate-generation instructions.
- Whether prior candidate artifacts, evaluation results or feedback were consumed.
- A documented protocol or initialization record.

Such evidence may support classifying a run as a separately initiated search grouping.

A protocol declaration does not establish statistical independence.

It must not override a confirmed search-history relationship. If later evidence establishes continuation or reuse of prior search activity, the derived family grouping must reflect that relationship.

A separate search grouping is not a declaration that its statistical trials are independent of those in another grouping.

### 5.5 Unknown relationships and unresolved membership

Where a relationship cannot be established, preserve that uncertainty.

An unknown relationship assessment is not a confirmed relationship edge and must not connect runs into the same derived family.

Where useful, the system may retain an explicit relationship assessment containing:

- The runs whose relationship was assessed.
- An `unknown` status.
- The reason the relationship could not be established.
- Available investigation or provenance references.

The `unknown` status belongs to the assessment of the relationship, not to the confirmed relationship-type vocabulary.

Where available evidence does not establish either a connecting relationship or a separately initiated search, family membership remains unresolved.

The absence of a recorded relationship does not prove that two searches were independent.

Shared dataset identity must remain separate evidence and must not resolve an otherwise unknown search relationship.

## 6. Observable accounting

The contract must distinguish the following quantities:

| Quantity | Definition |
|---|---|
| Run count | Number of recorded runs. |
| Run-attempt count | Number of execution attempts across run retries. |
| Generation count | Number of represented generations. |
| Candidate-slot count | Number of allocated candidate slots. |
| Mutation-attempt count | Number of initiated mutation operations. |
| Mutation-failure count | Number of failed mutation operations. |
| Candidate-proposal count | Number of candidate sources produced. |
| Distinct-source count | Number of distinct exact candidate source artifacts. |
| Duplicate-proposal count | Number of proposals identified as exact duplicates. |
| Proposal-rejection count | Number of proposals rejected before evaluation. |
| Evaluation-case count | Number of distinct candidate/input combinations. |
| Evaluation-attempt-record count | Number of uniquely recorded evaluation attempts, including attempts whose invocation or outcome is uncertain. |
| Confirmed-invocation count | Number of evaluator invocations whose start is confirmed. |
| Evaluation-success count | Number of successful evaluation attempts. |
| Evaluation-failure count | Number of confirmed failed evaluation attempts. |
| Evaluation-cancellation count | Number of explicitly cancelled attempts. |
| Indeterminate-attempt count | Number of attempts whose final execution outcome cannot be established. |

Seed and baseline evaluations should be distinguishable from candidate evaluations.

These quantities are observable operational counts. They must not be exposed or described as effective statistical trial estimates.

### Counting rules

1. Allocating a candidate slot does not establish that mutation was attempted.
2. Initiating mutation does not establish that a candidate source was produced.
3. A rejected proposal remains visible in proposal accounting.
4. An exact duplicate remains visible but does not automatically create a distinct source artifact.
5. An evaluation-attempt record does not, by itself, establish that the evaluator was invoked.
6. A confirmed evaluator invocation counts as one confirmed invocation.
7. An identical-input retry creates another attempt record and, when execution is confirmed, another invocation. It does not automatically create another independent statistical trial.
8. Changed evaluation inputs must be distinguishable from the original evaluation case.
9. An indeterminate attempt must remain visible even when invocation cannot be confirmed.
10. A recovery operation that resumes bookkeeping without invoking the evaluator does not create another evaluator invocation.
11. Unknown or interrupted outcomes must not be silently counted as successful or failed.
12. Statistical eligibility must be determined separately for each method.

## 7. Candidate and evaluation identity

### 7.1 Candidate proposals

A proposal must remain identifiable independently of whether it succeeds, fails or duplicates another proposal.

Where a duplicate is identified, the accounting evidence should preserve:

- The duplicate proposal's identity.
- Its candidate source hash.
- The identity of the earlier matching source or proposal, where known.
- The duplicate classification.
- The reason for the classification.

Illustrative duplicate record:

```json
{
  "proposal_id": "proposal-002",
  "run_id": "run-001",
  "generation": 1,
  "slot": 1,
  "candidate_source_hash": "sha256:source-a",
  "outcome": "duplicate",
  "duplicate_of_proposal_id": "proposal-001",
  "reason": "EXACT_SOURCE_DUPLICATE"
}
```

Exact-source duplication and behavioral similarity are separate concepts.

Behaviorally similar strategies with different source hashes remain distinct source artifacts unless a separately defined, versioned comparison establishes a similarity relationship.

### 7.2 Evaluation input identity

An evaluation case must be attributable to its complete evaluation inputs.

Relevant components include:

- Candidate source hash.
- Frozen dataset identity.
- Evaluation configuration identity.
- Evaluator implementation revision.
- Engine implementation revision.
- Other relevant execution dependencies or runtime build identity.

Evaluation configuration includes parameters that can alter results, such as:

- Timeframe and evaluation window.
- Fee and execution assumptions.
- Trading mode and leverage.
- Funding rate.
- Strategy parameters.
- Validation split.
- Protection settings.
- Other evaluator-affecting configuration.

Dataset identity should use the frozen dataset manifest and content identity. A timestamp or `data_epoch` alone is insufficient.

### 7.3 Evaluator and engine revisions

Evaluator and engine revisions are part of evaluation identity.

The same candidate, dataset and configuration may produce different results when the evaluation implementation changes.

The identity should therefore capture the immutable revision of the evaluator and engine used for the attempt.

Where relevant, it should also identify shared packages or runtime dependencies that can alter evaluation behavior.

A complete immutable runtime build identity may represent these components where it provides sufficient provenance.

The contract does not require a separate revision field for every package if a verifiable build manifest or immutable revision captures the complete relevant runtime.

If the actual evaluator, engine or relevant runtime revision cannot be established, the evaluation identity must be marked incomplete.

An incomplete identity must not be silently treated as equivalent to a fully identified evaluation.

### 7.4 Evaluation input digest

A reproducible evaluation-input digest should identify the canonical combination of:

- Candidate source identity.
- Frozen dataset identity.
- Evaluation configuration identity.
- Evaluator revision.
- Engine revision.
- Relevant runtime identity.

The digest must be based on canonical, versioned input representation.

It must not include mutable timestamps or unrelated metadata as substitutes for evaluation identity.

## 8. Evaluation-attempt lifecycle

An evaluation attempt should have its own identity and lifecycle.

Proposed lifecycle states:

| Status | Meaning |
|---|---|
| `queued` | The attempt is recorded but evaluator execution has not started. |
| `running` | Execution has been initiated and is in progress. |
| `succeeded` | Successful completion is confirmed. |
| `failed` | Failure is confirmed. |
| `cancelled` | Cancellation is confirmed. |
| `indeterminate` | The final execution outcome cannot be established. |

These statuses describe attempt lifecycle and outcome. They are not statistical classifications.

### 8.1 Invocation evidence

The system should distinguish:

- An attempt record being created.
- Entry into the candidate evaluator boundary.
- The evaluator invocation being durably confirmed.
- The strategy evaluation being initiated.
- The backtest engine being started.
- The evaluator returning a result.
- The final outcome being persisted.

For the inspected E1 path, the proposed candidate evaluator boundary is entry into `FrozenDatasetEvaluator.evaluate()`, reached from `evaluate_slot()`.

A confirmed invocation requires durable evidence that this boundary was reached.

Scheduling an attempt, allocating a candidate slot or entering the caller function does not establish a confirmed evaluator invocation.

Evaluator entry and backtest-engine execution are separate events. A confirmed evaluator invocation does not automatically establish that `evaluate_strategy_source()` or `run_engine()` completed or even started.

If the process crashes after the attempt is recorded but before evaluator entry can be confirmed, the invocation state remains unknown.

If evaluator entry is confirmed but the final result cannot be recovered, the attempt outcome is indeterminate.

The implementation must not infer a confirmed invocation from a candidate-slot status or a summary counter that does not represent actual evaluator-boundary evidence.

### 8.2 Indeterminate attempts

`indeterminate` is a first-class outcome.

It applies when execution may have occurred but the final result cannot be established reliably.

For example:

1. An evaluation attempt is recorded.
2. A worker begins execution.
3. The worker or parent process crashes.
4. The system cannot establish whether the evaluator completed.
5. No trustworthy final result is available.

The system must not describe this as a confirmed failure, success or cancellation.

The attempt must retain:

- Its identity.
- Evaluation-case identity.
- Last known lifecycle state.
- Any confirmed invocation evidence.
- Available timestamps.
- Failure or interruption information.
- Any available execution or result references.

Invocation state and outcome certainty should remain distinguishable.

Where the invocation itself cannot be confirmed, the attempt must not inflate the confirmed-invocation count.

Where the invocation is confirmed but its final outcome is unknown, it remains a confirmed invocation and an indeterminate outcome.

### 8.3 Retry after an indeterminate attempt

A later retry must:

- Receive a new evaluation-attempt identity.
- Reference the prior attempt.
- Preserve the prior attempt's indeterminate state.
- Record whether its evaluation inputs are identical or changed.
- Avoid silently overwriting the original attempt.

A retry does not automatically create another independent statistical trial.

A later recovered result may resolve the original attempt only when trustworthy evidence ties that result to the original invocation. Otherwise, preserve the original uncertainty and record the later execution separately.

## 9. Retry and duplicate semantics

### 9.1 Identical-input retry

When candidate source, dataset, evaluation configuration and evaluator/runtime identity remain unchanged:

- Preserve the original evaluation-case identity.
- Create another evaluation-attempt record.
- Preserve the previous attempt's outcome.
- Record another confirmed invocation only when invocation is confirmed.
- Do not automatically increment an effective statistical trial estimate.

### 9.2 Changed-input retry or replay

When an evaluation-affecting input changes:

- Preserve the candidate source identity where unchanged.
- Distinguish the new evaluation case from the previous one.
- Record the changed input identity.
- Link the new case to the previous case where it represents a re-evaluation.
- Do not automatically claim another independent statistical trial.

Examples include changes to:

- Dataset.
- Fees.
- Strategy parameters.
- Validation configuration.
- Evaluator revision.
- Engine revision.
- Relevant runtime dependencies.

### 9.3 Exact duplicate proposal

For an exact source duplicate:

- Preserve the duplicate proposal.
- Preserve its source hash.
- Identify the original matching source or proposal where possible.
- Record the duplicate outcome.
- Do not count it as a distinct source artifact.
- Do not treat it as an independent trial by default.

### 9.4 Behavioral similarity

Behavioral similarity requires a separately defined and versioned comparison process.

Where used, its evidence should identify:

- Fingerprint method and version.
- Evaluation input identity.
- Compared artifacts.
- Similarity result.
- Threshold or classification rule.

Different source hashes alone do not establish behavioral or statistical independence.

## 10. Failure and rejection semantics

Failures and rejections must remain distinguishable.

Relevant proposal outcomes include:

- `mutation_failed`
- `no_change`
- `diff_failed`
- `ast_rejected`
- `contract_rejected`
- `duplicate`

Relevant evaluation outcomes include:

- `succeeded`
- `failed`
- `cancelled`
- `indeterminate`

A proposal rejected before evaluation must not be represented as a completed evaluation.

An evaluator invocation that fails must retain its evaluation-attempt record.

A mutation operation that produces no source must remain visible as a mutation attempt or non-proposal outcome rather than being counted as a candidate proposal.

Failures and rejections must not disappear from accounting.

Whether a particular outcome is eligible for a specific statistical method is a separate method-level decision.

## 11. Selection and lineage evidence

The accounting contract should preserve enough information to reconstruct how candidate results influenced later decisions.

Where available, preserve:

- Candidate proposal identity.
- Candidate source identity.
- Parent candidate or source identity.
- Generation and slot.
- Evaluation case and attempt references.
- Selection or rejection decision.
- Decision timestamp.
- Selection criteria or ranking basis.
- Relevant evaluation evidence.
- Whether previous results or feedback influenced the decision.

A source hash identifies content, not lineage.

A fitness value identifies a result, not the selection process that produced the selected candidate.

The contract must not claim complete lineage where the current execution path does not record it.

## 12. Evidence required for statistical methods

The accounting layer should preserve evidence that downstream statistical methods may require.

Potential evidence includes:

- Relationships between runs.
- Run and retry lineage.
- Candidate proposal history.
- Candidate source hashes.
- Parent-child lineage where available.
- Duplicate relationships.
- Frozen dataset manifests and content hashes.
- Evaluation configuration identity.
- Evaluator and engine revisions.
- Relevant runtime identity.
- Evaluation-attempt history.
- Confirmed invocation evidence.
- Candidate selection history.
- Aligned return series where required by the method.

The current compact E1 evaluation snapshot contains aggregate metrics and validation diagnostics, not the complete equity curve and fills.

A statistical method requiring aligned returns must not treat the compact snapshot alone as sufficient evidence.

DSR and PBO evidence requirements must be evaluated separately. A common candidate count must not be assumed to satisfy both.

## 13. Statistical result status

Statistical assessments must use explicit, method-specific statuses.

### `uncorrected`

No search-level statistical correction was applied.

This does not mean the candidate passed a corrected significance test.

### `unsupported`

A specified statistical method could not be applied because required evidence or assumptions were not satisfied.

The result must include an explanatory reason.

### `corrected`

Reserved for a future implementation in which the specified correction has actually been applied to suitable, versioned evidence.

A correction result should identify:

- Method and method version.
- Input evidence references.
- Configuration and assumptions.
- Observed counts.
- Effective-trial estimate, if available.
- Estimation method and provenance.
- Result and limitations.

Illustrative uncorrected result:

```json
{
  "method": "DSR",
  "status": "uncorrected",
  "reason": "SEARCH_LEVEL_CORRECTION_NOT_APPLIED",
  "observed_counts": {
    "candidate_proposals": 3,
    "distinct_sources": 3,
    "evaluation_attempt_records": 3,
    "confirmed_invocations": 3,
    "successful_evaluations": 3
  },
  "effective_trial_count": null
}
```

Illustrative unsupported result:

```json
{
  "method": "PBO",
  "status": "unsupported",
  "reason": "ALIGNED_RETURN_MATRIX_UNAVAILABLE",
  "observed_counts": {
    "candidate_proposals": 3,
    "evaluation_attempt_records": 3,
    "confirmed_invocations": 3
  },
  "effective_trial_count": null
}
```

An unavailable effective-trial estimate must remain unset. It must not be replaced by raw candidate count.

Existing validation flags, bootstrap diagnostics and overfitting-risk classification are not equivalent to search-level correction.

## 14. Deterministic acceptance examples

The following examples define expected contract behavior. They are illustrative and do not claim that the current runtime already produces these records.

### Case 1: Three distinct candidates complete evaluation

Setup:

- One run.
- Three candidate proposals.
- Three different source hashes.
- Identical frozen dataset and evaluation configuration.
- Three successful candidate evaluations.

Expected summary:

```json
{
  "candidate_proposal_count": 3,
  "distinct_source_count": 3,
  "evaluation_case_count": 3,
  "evaluation_attempt_record_count": 3,
  "confirmed_invocation_count": 3,
  "evaluation_success_count": 3,
  "evaluation_failure_count": 0,
  "indeterminate_attempt_count": 0,
  "effective_trial_count": null,
  "correction_status": "uncorrected"
}
```

The observed counts are three proposals, sources, cases and confirmed evaluator invocations.

The contract does not assert three independent statistical trials.

### Case 2: Retry with identical evaluation inputs

Setup:

- One candidate source.
- One frozen dataset.
- One evaluation configuration.
- First attempt fails.
- Second attempt uses exactly the same inputs and succeeds.
- Both evaluator invocations are confirmed.

Expected record:

```json
{
  "evaluation_case_id": "case-001",
  "evaluation_input_digest": "sha256:input-a",
  "attempts": [
    {
      "attempt_id": "attempt-001",
      "status": "failed",
      "invocation_state": "confirmed"
    },
    {
      "attempt_id": "attempt-002",
      "status": "succeeded",
      "invocation_state": "confirmed",
      "retry_of_attempt_id": "attempt-001"
    }
  ],
  "evaluation_case_count": 1,
  "evaluation_attempt_record_count": 2,
  "confirmed_invocation_count": 2,
  "effective_trial_count": null
}
```

The system records two actual evaluator invocations against one evaluation case.

It does not automatically infer two independent statistical trials.

### Case 3: Retry with changed evaluation inputs

Setup:

- Same candidate source.
- First evaluation uses dataset A and configuration A.
- Second evaluation changes the fee configuration.
- Both evaluator invocations are confirmed.

Expected records:

```json
{
  "candidate_source_hash": "sha256:source-a",
  "evaluation_cases": [
    {
      "evaluation_case_id": "case-001",
      "evaluation_input_digest": "sha256:input-a",
      "dataset_manifest_hash": "sha256:dataset-a",
      "evaluation_config_hash": "sha256:config-a",
      "evaluator_revision": "evaluator-a",
      "engine_revision": "engine-a"
    },
    {
      "evaluation_case_id": "case-002",
      "evaluation_input_digest": "sha256:input-b",
      "dataset_manifest_hash": "sha256:dataset-a",
      "evaluation_config_hash": "sha256:config-b",
      "evaluator_revision": "evaluator-a",
      "engine_revision": "engine-a",
      "related_case_id": "case-001",
      "relationship": "changed_inputs_replay"
    }
  ],
  "evaluation_case_count": 2,
  "evaluation_attempt_record_count": 2,
  "confirmed_invocation_count": 2,
  "effective_trial_count": null
}
```

The second evaluation is distinguishable from the first.

Changed inputs do not automatically establish another independent statistical trial.

### Case 4: Duplicate candidate proposal

Setup:

- Proposal A produces source hash `sha256:source-a`.
- Proposal B produces the same exact source hash.

Expected records:

```json
{
  "candidate_proposals": [
    {
      "proposal_id": "proposal-a",
      "source_hash": "sha256:source-a",
      "outcome": "proposed"
    },
    {
      "proposal_id": "proposal-b",
      "source_hash": "sha256:source-a",
      "outcome": "duplicate",
      "duplicate_of_proposal_id": "proposal-a"
    }
  ],
  "candidate_proposal_count": 2,
  "distinct_source_count": 1,
  "duplicate_proposal_count": 1,
  "evaluation_attempt_record_count": 1,
  "effective_trial_count": null
}
```

The duplicate remains visible without becoming a distinct source artifact.

### Case 5: Rejected proposal and failed evaluation

Setup:

- Proposal A produces source code but fails AST validation.
- Proposal B passes preparation and reaches the evaluator.
- Proposal B's evaluation fails.
- The second evaluator invocation is confirmed.

Expected records:

```json
{
  "proposals": [
    {
      "proposal_id": "proposal-a",
      "source_hash": "sha256:rejected-source",
      "outcome": "ast_rejected",
      "reason": "AST_VALIDATION_FAILED",
      "evaluation_attempt_id": null
    },
    {
      "proposal_id": "proposal-b",
      "source_hash": "sha256:failed-source",
      "outcome": "evaluation_failed",
      "reason": "WORKER_FAILURE",
      "evaluation_attempt_id": "attempt-b"
    }
  ],
  "candidate_proposal_count": 2,
  "distinct_source_count": 2,
  "proposal_rejection_count": 1,
  "evaluation_attempt_record_count": 1,
  "confirmed_invocation_count": 1,
  "evaluation_failure_count": 1,
  "effective_trial_count": null
}
```

The rejected proposal did not undergo candidate evaluation.

The second proposal did undergo evaluation, and its failed attempt remains recorded.

Neither outcome may disappear from the accounting history.

### Case 6: Related runs, separate search starts and shared data

Setup:

- Run A begins a documented search.
- Run B reuses feedback from A.
- Run C has evidence of a separately initiated search and does not consume A's candidate artifacts, evaluation results or feedback.
- Run D has insufficient provenance to establish whether it reused or continued A's search activity.
- All four runs use the same frozen dataset.

Expected records:

```json
{
  "runs": [
    {
      "run_id": "run-a",
      "dataset_identity": "sha256:dataset-a",
      "search_start_evidence_ref": "search-start-a"
    },
    {
      "run_id": "run-b",
      "dataset_identity": "sha256:dataset-a"
    },
    {
      "run_id": "run-c",
      "dataset_identity": "sha256:dataset-a",
      "search_start_evidence_ref": "search-start-c"
    },
    {
      "run_id": "run-d",
      "dataset_identity": "sha256:dataset-a"
    }
  ],
  "search_relationships": [
    {
      "from_run_id": "run-b",
      "to_run_id": "run-a",
      "relationship_type": "reused_feedback",
      "status": "confirmed",
      "evidence_ref": "feedback-a"
    }
  ],
  "separate_search_evidence": [
    {
      "run_id": "run-c",
      "status": "documented_separate_start",
      "evidence_ref": "search-start-c"
    }
  ],
  "relationship_assessments": [
    {
      "from_run_id": "run-d",
      "to_run_id": "run-a",
      "status": "unknown",
      "reason": "INSUFFICIENT_SEARCH_HISTORY"
    }
  ],
  "derived_search_groups": [
    {
      "group_key": "family-ab",
      "run_ids": [
        "run-a",
        "run-b"
      ],
      "basis": "confirmed_relationship_graph"
    },
    {
      "group_key": "family-c",
      "run_ids": [
        "run-c"
      ],
      "basis": "documented_separate_search_start"
    }
  ],
  "unresolved_run_ids": [
    "run-d"
  ]
}
```

Expected interpretation:

- Runs A and B belong to the same derived search family because B reused feedback from A.
- Run C has a documented separate search origin. Its use of the same dataset does not connect it to A or B.
- Run C's separate search grouping does not establish statistical independence from A or B.
- Run D remains unresolved. The unknown assessment is not a confirmed relationship edge.
- The common dataset identity is retained for all four runs but does not determine their family membership.

The illustrative group keys are derived labels, not a requirement for permanent family identifiers.

No run may be declared statistically independent merely because it has a separate group key, a different run ID or no recorded relationship.

## 15. Test classification

Tests must distinguish current implementation behavior from proposed contract behavior.

### 15.1 Current-behavior characterization

Tests in this category must exercise actual production code and document existing semantics.

Relevant boundaries include:

- Run idempotency and owner-scoped retrieval.
- Run retry lineage and `experiment_id`.
- Frozen dataset manifest persistence.
- Candidate slot persistence.
- Same-run exact source duplicate detection.
- Candidate summary counting.
- Evaluation snapshot shape.
- E1 evaluator inputs and outputs.
- Recovery of a candidate slot already in the evaluation stage.

These tests must not assert that an append-only evaluation-attempt ledger or explicit run-relationship graph already exists.

### 15.2 Proposed-contract coverage

The following require production support before they can be described as runtime regression coverage:

- Append-only evaluation-attempt records.
- Stable evaluation-case identity.
- Explicit evaluator and engine revision identity.
- Same-input and changed-input retry distinction.
- Invocation confirmation and indeterminate outcome handling.
- Duplicate source linkage.
- Explicit relationships between runs.
- Persisted selection history.
- Distinct proposal, source, evaluation and trial accounting.
- Method-specific `uncorrected` and `unsupported` outcomes.

Fixtures may validate illustrative contract records or serialization. They must be labelled as contract examples, not proof of current runtime behavior.

### 15.3 Production-change dependencies

Where acceptance requires new persistence or runtime behavior, identify that dependency explicitly.

Do not expand the first contribution into an unbounded runtime refactor merely to make every proposed contract case pass immediately.

## 16. Compatibility and operational safeguards

Any later implementation should:

- Preserve existing owner-scoped access.
- Preserve permissions and credential boundaries.
- Preserve approval and promotion requirements.
- Preserve execution isolation and existing run lifecycle behavior.
- Prefer additive migrations.
- Retain historical outcomes without inventing missing lineage.
- Represent unknown legacy relationships as unknown.
- Represent incomplete historical evaluation identity explicitly.
- Avoid silently changing the meaning of existing API counters.
- Avoid double-counting parent and child cost or execution summaries.
- Keep statistical integration independently reviewable.

Existing `experiment_id` and retry fields must not be repurposed as a search-family identifier.

Historical candidate slots must not be retroactively represented as append-only evaluation attempts unless the underlying attempt evidence exists.

Historical runs must not be assigned independent search status merely because no relationship record was retained.

## 17. Contribution boundary

The first contribution should establish:

1. An agreed vocabulary and accounting contract.
2. Expected records for the six deterministic acceptance cases.
3. A source-accurate description of current E1 behavior.
4. A clear separation between current behavior and proposed behavior.
5. The production changes required for future regression coverage.

The first contribution does not need to introduce a statistical correction.

Later statistical integration should consume trustworthy accounting and evidence rather than manufacture them from candidate counts.

**Core rule:**

> Record what happened. Preserve how it happened. Distinguish what was evaluated from what was statistically tested. Never infer independence from an identifier.
```