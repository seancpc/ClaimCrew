# ADR-001: Fully managed Azure stack, zero self-hosting

**Status**: accepted (2026-08-30) · **Drafted while**: Phase 0, pre-deployment

## Context
ClaimCrew is a solo portfolio project whose thesis is *delivery speed via managed cloud services*, contrasted with month-scale on-prem delivery cycles the author has experienced professionally. The system needs document extraction, hybrid retrieval, multi-agent orchestration, LLM inference, a workflow engine, storage, and observability.

## Decision
Use Azure managed services for every capability: Document Intelligence (extraction), AI Search (retrieval), Foundry Agent Service (orchestration), Azure OpenAI (inference), Logic Apps (human-review workflow), Blob/Cosmos (storage), Application Insights (observability). No self-hosted models, no rented GPUs, no self-managed orchestration framework.

## Alternatives rejected
- **Self-hosted LangGraph + local models**: proven elsewhere in the author's portfolio (MedAgent-EHR, SelectRAG); repeating it here would dilute the managed-cloud thesis and reintroduce infra work that the project exists to avoid.
- **Single-cloud-service hybrid** (e.g., only Azure OpenAI, rest self-built): weakens the integration-speed narrative, which depends on service-to-service composition.

## Consequences
- Build effort shifts from infrastructure to integration; per-service integration hours are logged as evidence (see build log).
- Vendor lock-in is accepted deliberately — it *is* the subject under study.
- Costs are metered, which forces the cost-engineering discipline recorded in ADR-002/003.
