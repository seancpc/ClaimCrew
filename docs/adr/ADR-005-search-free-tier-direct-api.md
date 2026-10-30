# ADR-005: AI Search Free tier, called directly by agents

**Status**: accepted (2026-08-30); **amended 2026-10-01** — retrieval is not on the default path (see Measured outcome)

## Context
The policy corpus is three synthetic policies totalling a few MB — far below the Free tier's 50 MB / 3 indexes. Azure OpenAI's "On Your Data" integration requires Basic tier (~$74/month), the single largest line item in the standing-cost estimate.

## Decision
Start on the Free tier and have the Policy Agent call the Search API directly as a tool, rather than routing retrieval through On Your Data. Upgrade to Basic only for interview-peak windows (SLA + semantic ranker), as a deliberate, reversible spend decision.

## Alternatives rejected
- **Basic tier from day 1**: pays ~$74/month for SLA and semantic ranking that a demo corpus does not need.
- **Serverless preview**: free during preview but preview terms expire (observed: 2026-09-13 deadline) — exactly the drift class ADR-002's checklist exists for; unsuitable as the default.

## Consequences
- Retrieval quality relies on hybrid (keyword + vector) search without semantic ranker; the citation-accuracy metric in the eval set will tell us whether that ceiling matters.
- The agent-calls-Search-directly pattern keeps the retrieval boundary explicit, which the Gate exploits to verify every citation against the indexed corpus.

## Measured outcome (2026-10-01)
- Index built on Free tier: 104 clause documents, hybrid (BM25 + 1536-dim HNSW) via REST 2024-07-01, embedding deploy on Standard. Recall@5 0.655, @10 0.835; the misses cluster on definitional / procedural clauses that a query built from case facts cannot reach, so a title-rule **core set** (18/14/18 clauses) was unioned with top-10 → recall 1.000 on 71/71 truth bases.
- Each policy is only ~3.3k tokens of a ~16k-token call, so **A/B on all 72 cases** (completed 2026-10-02 after the DI F0 quota reset): `core+search` saved **6.2 %** prompt tokens (1,059,027 vs 1,128,896), averaged 17.9 vs 34.0 clauses, and was slightly worse pre-Gate (59/72 vs 61/72 correct; amounts 31/38 vs 31/40). On the 56-case paired subset measured the day before: 46 vs 48, deny→approve 7 vs 5, citation precision 0.918 vs 0.899.
- **Decision (user, 2026-10-01): `full` is the Phase 1 default**; `core+search` stays as an option and as proof the Search path works. Rejected: enlarging the core set to ~25 clauses (recall up, savings down to <3 %, pointless). Search becomes relevant again only if the corpus grows past what fits in one prompt.
