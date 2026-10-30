# Architecture Decision Records — ClaimCrew

Format: [MADR-style](https://adr.github.io/) short form. Status `accepted` = decided and implemented (or committed to); `proposed` = pending validation on Azure.

| # | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-fully-managed-azure-stack.md) | Fully managed Azure stack, zero self-hosting | accepted |
| [ADR-002](ADR-002-teardown-as-cost-control.md) | Teardown-as-cost-control operating model | accepted |
| [ADR-003](ADR-003-compress-the-metered-window.md) | Cost-first build order: compress the metered window | accepted |
| [ADR-004](ADR-004-pydantic-single-source-schema.md) | Pydantic models as single source of truth for the claim schema | accepted |
| [ADR-005](ADR-005-search-free-tier-direct-api.md) | AI Search Free tier, called directly by agents | accepted |
| [ADR-006](ADR-006-llm-judges-facts-code-decides-routing.md) | LLM judges facts; code decides routing (program-side Gate) | accepted |
| [ADR-007](ADR-007-model-tier-routing.md) | Two-tier model routing (GPT-4o / gpt-4.1-mini) | accepted |
| [ADR-008](ADR-008-verification-boundary-split.md) | Split agents at verification boundaries; LLM repair layer is advisory only | accepted |
| [ADR-009](ADR-009-content-safety-boundary.md) | Content Safety boundary: groundedness / Prompt Shields not wired into the main pipeline | accepted |
| [ADR-010](ADR-010-remove-cosmos-db.md) | Remove Cosmos DB; full-case persistence lives in Blob `runs/` | accepted |
