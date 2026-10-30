# ADR-002: Teardown-as-cost-control operating model

**Status**: accepted (2026-08-30)

## Context
The finished system serves as an interview demo, not a production service. A standing deployment would cost roughly $220–280 per 10 weeks (Search Basic scenario) for zero users, and the demo may sit unused for up to a year between job-search windows. Budget hard cap: NT$5,000.

## Decision
After the project completes, tear down all Azure resources (`az group delete`); standing cost drops to $0. Before an interview window, rebuild from the repo using Bicep plus a **rebuild runbook**. The repo stores *ingredients and scripts that can regenerate everything* — never artifacts: DI custom models are retrained from committed samples + labels (they cannot be exported), Search indexes are rebuilt from committed policy PDFs, all case data regenerates from committed generators.

The runbook is expected to be stale by rebuild time: it records the last successful rebuild's steps and pinned versions, plus a **drift checklist** (model retirements, SDK breaking changes, free-tier term changes, quota re-approval, soft-delete purges). Realistic rebuild time: one command + half a day to two days of drift fixing — hence rebuild a week before interviews, with the demo video as insurance.

## Alternatives rejected
- **Always-on minimal tier**: still a fixed monthly cost, and contradicts the cost-engineering narrative the project sells.

## Consequences
- Every piece of state must be regenerable from the repo; anything configured by hand in the Portal is a bug.
- Rebuild-with-newer-models becomes an upgrade opportunity (rerun the frozen eval set; keep the pinned version if scores regress).
- The teardown→rebuild→timed-demo drill is itself a Phase 3 deliverable.
