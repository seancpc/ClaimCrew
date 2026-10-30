# ADR-006: LLM judges facts; code decides routing (program-side Gate)

**Status**: accepted (2026-08-31)

## Context
Claims adjudication has failure modes with asymmetric costs (a wrongly approved deny is the worst cell in the confusion matrix). LLMs fabricate citations, inflate confidence, and fill missing slots with the nearest plausible material. Routing policy (what goes to a human) must be auditable and tunable without retraining or re-prompting.

## Decision
A deterministic Gate (`app/gate/`) runs after the Adjudication Agent and holds veto power. It re-checks structure, not judgment: every citation must resolve to a real clause in the parsed policy index and its quote must be a verbatim substring of that clause; amounts must be zero on deny/manual and within per-policy theoretical upper bounds on approve; approvals must carry at least one citation; confidence below a code-constant threshold, or low-confidence extraction fields, force `manual_review`. Any flag downgrades the decision to `manual_review`, preserving the original verdict and flags for audit. Prompts mirror the same philosophy: agents report facts and calculations with per-claim citations; they do not decide routing.

## Alternatives rejected
- **Full deterministic re-adjudication in the Gate**: would make the Gate a second adjudicator (surgery-table lookup, merged-admission logic); complexity grows until the LLM is redundant. Kept as a possible Phase 2 extension for amount recomputation only.
- **LLM self-verification (ask the model to check itself)**: shares failure modes with the thing it checks; fabricated citations pass self-review.

## Consequences
- Citation fabrication is structurally detectable, not probabilistically discouraged.
- Threshold changes are code changes with tests and eval reruns — production-change discipline applies.
