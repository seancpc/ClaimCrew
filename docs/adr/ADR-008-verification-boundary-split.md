# ADR-008: Split agents at verification boundaries; an LLM repair layer never holds release authority

**Status**: **accepted (2026-10-03)** — measured over SPEC-m/n/o/p (2026-10-02 → 10-03); amends ADR-006 in one clause (deterministic deny flags route to `deny`, not `manual_review`).

## Context
The project's thesis is a fully managed, multi-agent claims adjudicator. Phase 1 shipped two LLM roles (extraction on gpt-4.1-mini, policy+adjudication on gpt-4o) with a program-side Gate of 32 deterministic checks. Phase 2 asked the obvious question: which further splits actually improve the system, and where does Foundry Agent Service fit?

Three measurements answered it (all on the frozen 72-case set; details in `docs/evidence/EVIDENCE-20261001-single-agent.md` parts 18–21):

1. **Splitting by knowledge domain loses context.** A standalone Policy agent (same prompt minus adjudication) reached clause recall **0.772** on 10 cases (threshold 0.80) and systematically missed POL-A 第11條 — a payable clause whose applicability only follows from the claim facts. Splitting Policy from Adjudication was rejected.
2. **Foundry Agent Service works as a host for an agent with tools.** Agents v2 (`azure-ai-projects` 2.7.0, `agent_reference` on the Responses API, strict JSON schema, a client-executed `run_gate` function tool, bounded two-round loop) passed every mechanical probe: 10/10 schema-valid, prompt cache 76–92 %, arm-to-arm decision agreement 9/10 against a same-arm noise floor of 7/10.
3. **An LLM repair layer with release authority loses money even behind six deterministic guards.** Two full replays (R1, R2) of a Repair agent — which re-decides Gate-flagged cases, with Gate as a tool and guards for upgrade, unflagged lines, confidence, facts, and a second opinion — lifted post-Gate accuracy (v8 0.556 → 0.681/0.653; route_v1 0.694 → 0.792/0.778, dangerous errors 0) but released one wrong amount per round (C003 −2,000: the agent shrank a receipt fact to satisfy the cap check; C055 +5,000: the agent fixed a `kind` and the Gate had no per-segment daily-cap check) and in R2 turned a payable claim into a release-grade deny (C065). Every released error mapped to a hole in the Gate; the repair layer converted "lucky manual" cases into losses.

## Decision
1. **Split by verification boundary, not by knowledge domain.** A role exists only where a deterministic check separates it from its neighbour and a measured gap justifies it: Extraction (mini) → program Intake → Decision (4o) → program Gate → Repair-as-advisor → human. Policy and Adjudication stay in one call.
2. **The LLM repair layer is advisory only.** `repair_case(advisory=True)` is the default: it runs on Gate-flagged cases, its guards are evaluated and recorded (`RepairRecord.would_release`, `applied=False`), but `adjudication` and `gate` are never replaced. Its output is material for the human reviewer (and the forthcoming Logic Apps review card). The release path survives only as `rerepair --release` for measurement.
3. **Deterministic deny.** Flags the program can establish on its own (`waiting_period_unmet`, `claim_deadline_exceeded`) route the final decision to `deny` instead of `manual_review`, provided the policy effective date was extracted with confidence ≥ the field threshold and no date field failed to parse. Measured on eight historical sets: 40/40 auto-denied cases have truth `deny`; dangerous errors stay 0; accuracy rises on every set (e.g. route_v1 0.681 → 0.750, v7 0.653 → 0.736). This amends ADR-006's "any flag → manual".
4. **Gate holes exposed by the repair experiment are closed deterministically.** New check `calc_segment_cap_mismatch` (POL-C 第8條 per-segment daily cap, the C055 hole). Merging split capped lines (the C003 hole) is implemented but **opt-in** (`regate --merge-split-lines`): on v7 it removed the cap-sum flag that had been catching a missing-line under-payment (C069 −5,200), so it waits for a receipt-coverage check. **Addendum 2026-10-03 (SPEC-q):** the receipt-coverage check `receipt_category_uncovered` landed (POL-C approve: every receipt category hit under 第8~11條 must have a covering calculation line; 附錄一 covers 第9條); on the same eight sets it flags only v7 C069 with zero false flags, so merging is now **on by default** (`finalize_adjudication(merge_split_lines=False)` / `regate --no-merge-split-lines` kept as opt-out for measurement).
5. **Foundry Agent Service hosts intra-role tool loops; inter-role routing stays in code.** Connected-agent orchestration by an LLM was rejected because routing is the Gate's job (ADR-006).

## Alternatives rejected
- **Standalone Policy agent** (recall 0.772, misses payable clauses).
- **Repair with release authority** (R1/R2 numbers above); a third guarded round was declined by pre-commitment.
- **LLM-orchestrated connected agents**: puts routing back into the model.
- **Hosting the decision call on Agent Service for its own sake**: no measurable gain; a hosting choice, not a capability.
- **Auto-filling the missing 第11條 line** (program generating LLM material): higher risk than benefit; left as a candidate.

## Consequences
- The honest multi-agent story is a verification pipeline: two LLM roles with release-relevant output, one advisory LLM agent with tools on Agent Service, and program roles that own every irreversible step. Role count is not the metric; measured gaps are.
- Every repair-era guard (G1–G7) remains in code and is exercised by tests; they are the contract if release authority is ever reconsidered.
- Cost: an advisory repair pass over a 72-case run is ≈ US$1.3 (92 % cache hit); the Foundry account, project and agent versions carry no fixed charge.
- Open: receipt-coverage check (would make the merge safe), auto-fill of paired daily-allowance lines, and the Logic Apps review card that gives the advisory output a reader.

## How much Agent Service is actually used (addendum 2026-10-19, E11)
Stated plainly so the multi-agent story is not overstated:
- **Only one role runs on Foundry Agent Service**: the advisory Repair agent (`app/pipeline/agent_service.py`, `app/pipeline/repair.py`). Extraction and Decision are plain Azure OpenAI calls with structured output; Intake and Gate are program code.
- **It is off by default.** It runs only when `tools/run_pipeline.py --repair` (or `tools/rerepair.py`) is passed. The cloud Container Apps Job never passes it: its arguments are `--review`, `--safety` (both optional) and `--pace 5` (`infra/bicep/modules/containerapp.bicep`). Every cloud run so far, including the v17 regression and the demo run, used no Agent Service.
- **Even when on, it cannot change an outcome**: `applied` is always False, so its output is reviewer material only (decision 2 above).
- **Routing between roles is deliberately in code** (decision 5, ADR-006). Agent Service was evaluated as a host for an intra-role tool loop and passed every mechanical probe; it was not adopted as an orchestrator.

The capability is proven and kept, but the production path does not depend on it. Turning it on is a single flag once a reviewer actually wants the advisory output.
