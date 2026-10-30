# ADR-007: Two-tier model routing (GPT-4o / mini-class)

**Status**: **accepted (2026-10-02)** — extraction routed to gpt-4.1-mini, decision stays on gpt-4o; −33.8 % cost per case with no eval regression on the frozen 72 (see Measured outcome)

> **2026-09-30 Phase 1 correction**: the first real `az deployment sub what-if` rejected both pinned versions (`ServiceModelDeprecating`: gpt-4o 2024-08-06 and gpt-4o-mini 2024-07-18 are *Deprecating* in eastus, retire 2027-04-14), and this subscription has 0 TPM of GlobalStandard quota but 50K TPM Standard for gpt-4o. Bicep now deploys **gpt-4o 2024-11-20 (Legacy) on Standard** as the single model; the mini tier is behind `enableMiniModel=false`. A quota request for `gpt-5.6-luna` (GA, retire 2028-01-11) is pending; the tier experiment resumes once a mini-class model has quota. Every version string above is superseded by `infra/bicep/modules/openai.bicep`.

## Context
Intake and Extraction are transcription-and-checking tasks; Policy matching and Adjudication require clause-level reasoning. Mini-class models are 10–20× cheaper per token.

## Decision (proposed)
Route Intake/Extraction to `gpt-4o-mini` (pinned 2024-07-18) and Policy/Adjudication to `gpt-4o` (pinned 2024-08-06). Both deployments are provisioned in Bicep with pinned versions. Acceptance test: the frozen eval set must show no accuracy/citation regression versus all-4o, with per-case token cost logged via Application Insights as the measured saving.

## Alternatives rejected
- **All-mini**: expected citation-accuracy loss on clause reasoning (to be verified, not assumed).
- **All-4o**: simplest, but forfeits the cost-engineering evidence this project exists to produce; retained as the baseline arm of the routing experiment.

## Consequences
- This ADR is intentionally incomplete until Phase 2 numbers exist; the decision graduates to `accepted` only with measured cost-per-case and unchanged eval scores attached.

## Measured outcome (2026-10-02, Phase 2 first cut — SPEC-k)

**What was built.** Per-call deployment override (`--extraction-deployment` / `--decision-deployment`), per-call token accounting incl. `cached_tokens`, and `tools/cost_report.py` with a price table pinned from the Azure Retail Prices API (eastus2, regional/Standard meters, USD per 1M: gpt-4o 2024-11-20 = 2.75 / 1.375 cached / 11.00; gpt-4.1-mini 2025-04-14 = 0.44 / 0.11 / 1.76). The mini tier is **gpt-4.1-mini 2025-04-14 on Standard** — the only non-deprecating text mini with a Standard SKU in `list-models` (gpt-4o-mini 2024-07-18 is Deprecating; gpt-5-mini / 5.4-mini are GlobalStandard/DataZone-only reasoning models, kept as a possible second arm). Standard quota: 200K TPM.

**Premise falsified.** The ADR assumed extraction was the bulk of spend. Measured on the first 10 cases (9 with LLM calls): extraction is **39.1 %** of prompt tokens and 26 % of completion; the decision call (full policy text + bundle + derived facts) is 60.9 %. Prompt caching is already live on both calls (58 % / 69 % cached).

**Pilot (10 cases, paired).** Routed arm: 10/10 ok, 0 schema-validation retries. Extraction output **identical** to gpt-4o on all 9 bundles (0 differing field values out of 17–20 per case; mean field confidence 0.95 → 0.95). The three cases whose *decision* differed between arms are gpt-4o run-to-run noise (same model, byte-identical inputs), not routing.

| arm | extraction USD | decision USD | total / case |
|---|---|---|---|
| baseline 4o / 4o | 0.1605 | 0.2938 | 0.0454 |
| routed 4.1-mini / 4o | **0.0238** (−85 %) | 0.2639* | 0.0288 |

\* decision cache share jumped 69 → 95 % because the routed arm ran right after the baseline (same prefixes still hot); normalising the decision call to the baseline figure gives **0.0318 per case, −30 %**. That ceiling is set by the 39 % share, not by the mini discount.

**Status of the acceptance test.** Cost-per-case is measured; the frozen-eval non-regression check needs the 72-case routed run (≈ US$2.3). Until then the ADR stays `proposed`; the honest headline is already clear: routing is worth ~30 %, and the remaining 70 % lives in the decision call's policy context — which ADR-005 showed retrieval cannot shrink without losing accuracy.

**72-case routed run (route_v1, 2026-10-02) — acceptance test passed.** 72/72 ok, 0 schema retries. Pre-Gate 0.819 vs 0.792 (all-4o v8, same day, within the ±3-case noise band), post-Gate **0.694** (best of nine runs), manual recall 1.0, dangerous errors 0/0/0, citation precision 0.980. Released 26 cases, 25 amounts exact; the one miss (C051, +7,500) has byte-identical bundles in both arms — it is a gpt-4o *decision-call* slip (second-surgery 50 % rule) that the Gate accepted because 15,000 is both a grade-2 full lump and a grade-3 half lump; tracked separately as a Gate fix (SPEC-l), not a routing effect.

Cost, priced with the pinned table: extraction 1.2854 USD on 4o-equivalent tokens → **0.1743** on 4.1-mini; decision 2.0001 unchanged; **0.0456 → 0.0302 USD per case (−33.8 %)**. The ceiling is structural — extraction is 39 % of prompt tokens — so the next lever is the decision call's policy context, which ADR-005 showed retrieval cannot shrink without accuracy loss.

**Decision as accepted.** `tools/run_pipeline.py` defaults `--extraction-deployment gpt-4.1-mini`; the Bicep mini arm stays enabled; the all-4o configuration remains one flag away for regression checks. Any model change on either tier re-runs the 72-case eval before it is adopted (LLM rule 2: no cross-version behaviour assumptions).
