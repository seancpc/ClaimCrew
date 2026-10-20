# ADR-010: Remove Cosmos DB; full-case persistence lives in Blob

- **Status**: **accepted** (2026-10-07; owner approved option A)
- **Related**: ADR-001 (fully managed), ADR-002 (teardown as cost control), SPEC-t (Job output written to Blob `runs/`), SPEC hardening plan G6 / E5

## Context
The original SPEC persisted every case in Cosmos DB Serverless (inputs, each agent's intermediate output, final decision, latency, token cost). Phase 1 deployed account `cos-claimcrew` (database `claimcrew` / container `claims`), but the implementation went another way:
- Desktop runs write `check/pred_*/<case_id>.json` plus `_run.jsonl`; the cloud Job (SPEC-t) uploads the whole output directory to Blob `runs/<run_id>/` and writes `run_meta.json`.
- `app/`, `tools/` and `tests/` contain **zero references** to Cosmos (full-text search, 2026-10-07).
- Cost analysis, October 2026 month to date: `cos-claimcrew` **< NT$0.01** (total for the same period NT$1,928.53, 99 % on the model accounts).

## Decision
Remove the `cosmos` module and the `cosmosEndpoint` output from Bicep; full-case persistence is carried by Blob `runs/<run_id>/`. The already-deployed account is not deleted on its own; it disappears when the resource group is torn down in improvement step ⑧ (an incremental Bicep deployment does not delete resources it no longer lists).

## Rationale
- The actual usage pattern is "one batch run → one output directory + one digest email". What is needed is whole-run file retention and replay (`regate` / `rerepair` / `resafety` all read a directory), not per-case queries or concurrent updates.
- Claiming "every case is persisted in Cosmos" while it actually lives in Blob is a gap between the public story and reality; removing the account is more honest than adding a write nobody reads.
- The database being removed is empty, so no data is lost; the module can be restored from git history if needed.

## Alternatives rejected
- **B. Keep it and store review results there**: the review loop (SPEC G7 / E4) had no write-back path yet, so Cosmos would still sit empty; it would also add an `azure-cosmos` dependency, RBAC and tests. Rejected because its premise (review write-back) did not hold.
- **C. Leave as is**: cost ≈ $0, but the story/reality gap stays and every rebuild carries a useless resource.

## Reopen conditions
The review loop (E4) lands **and** a need appears for per-case queries or several people concurrently updating one case's status. At that point compare Cosmos, Table Storage and Blob.

> Note (2026-10-11): the review loop did land in SPEC-am as Blob files under `reviews-inbox/<guid>.json`, aggregated by `tools/review_stats.py`. It is single-reviewer and append-only, so the second reopen condition has not been met.
