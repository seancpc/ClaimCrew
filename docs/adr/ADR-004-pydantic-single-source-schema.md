# ADR-004: Pydantic models as single source of truth for the claim schema

**Status**: accepted (2026-08-30)

## Context
The claim schema is consumed by four parties: the data generator, the agents (via structured outputs JSON Schema), the program-side Gate, and the eval pipeline. Field definitions drifting between any two of them produces silent scoring corruption.

## Decision
Define all models once in `app/schemas/claim.py` (Pydantic v2, pinned 2.9.2). The structured-outputs JSON Schema is *generated* by `to_structured_output_schema()`, which enforces the API subset: `additionalProperties: false`, all fields required, and `Decimal` fields collapsed to `string` (amounts travel as strings; Python converts, avoiding float error). Generator, Gate, and eval import the same models — field consistency is structural, not disciplinary.

## Alternatives rejected
- **Hand-written JSON Schema file**: at least two sources of truth (schema file + code-side validation) that will drift; the pre-mortem for this option was a silent metric corruption after a field rename.
- **Dataclasses + hand-rolled validation**: re-implements what Pydantic already provides, with worse error messages.

## Consequences
- The transform layer carries its own gauntlet: property-based invariants (closed/required everywhere, idempotence, dual-validator agreement on LLM-shaped payloads) and a 6/6 manual mutation kill score. Notable finding: 100% line coverage still missed a mutant on first run — the added test now pins the function's core semantics directly.
- Real Azure OpenAI acceptance of the generated schema remains a Phase 1 checkpoint.
