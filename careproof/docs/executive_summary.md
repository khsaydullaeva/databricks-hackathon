# CareProof for the CMU Datathon

CareProof helps community health workers and NGO coordinators find facility leads for a requested service, understand the evidence behind each result, and decide what needs confirmation before a referral.

**Exploration.** The Bronze profile covers 10,088 rows and 51 fields. Missing coordinates, incomplete administrative fields and mixed-facility text complicate search. In a targeted 646-row Gold export, 207 representative claims were flagged for conflict and 85 had automated attribution to dataset text.

**Modeling.** Databricks preserves original text and IDs, extracts service claims and flags contradictions. A deterministic resolver and ranker screen attribution and geography before calculating transparent points. A 36-query fresh-export audit ranked 20 searches and abstained on 16. All 57 ranked placements matched their captured original record and exact excerpt.

**Impact.** A coordinator can compare candidates, inspect exact evidence and see why some leads were withheld. CareProof supports follow-up decisions. Current availability, quality and safety require direct confirmation. The next evaluation milestone is independent review of real claims and branches.

[Architecture](architecture.md) · [EDA](../reports/stage13_eda.md) · [Evaluation](../reports/stage13_evaluation.md) · [Demo](demo.md)
