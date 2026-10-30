# ADR-003: Cost-first build order — compress the metered window

**Status**: accepted (2026-08-30) · Executed as Phase 0, completed 2026-09-01 at $0

## Context
Most of this system does not require a cloud to build: synthetic data, schemas, deterministic validation, prompts, IaC templates, CI, and the evaluation harness are all cloud-independent. Azure billing starts the moment resources exist.

## Decision
Sequence all cloud-independent work into a "Phase 0" completed **before any Azure resource is created**: synthetic policy corpus and claim cases with frozen ground truth, the Pydantic claim schema, the program-side Gate, the eval pipeline (validated to perfect scores against ground-truth-as-predictions), all four agent prompts, the full Bicep template set (linted), and CI. The metered window then contains only work that genuinely requires the cloud.

## Alternatives rejected
- **Provision-first ("walking skeleton on day 1")**: standard advice for team projects, but for a metered solo demo it burns billed days on work a laptop can do.

## Consequences
- Evidence: Phase 0 shipped 3 policies, 40 cases (JSON + PDF, ground truth human-verified and frozen), DI training labels, schema, Gate, eval, prompts, Bicep, and green CI at exactly $0.
- Integration risk (e.g., structured-outputs schema compatibility, DI label format) is deferred to Phase 1 by design; both are flagged in-code as pending real-API validation.
