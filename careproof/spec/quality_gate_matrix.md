# CareProof Success Criteria — Evidence Contract

This checklist clarifies what is *observed*, what is a *dataset claim*, and what still needs human review.

| Stage | Minimum machine-checkable pass | Human/research caveat |
|---|---|---|
| 00 Setup | Source SELECT works; workspace.careproof supports temporary create/drop | Valid OAuth and permission required |
| 01 Bronze | Row count equal; 51 fields; both EXCEPT ALL differences empty | Marketplace source may refresh; bronze is a frozen snapshot |
| 02 Profile | JSON report generated from live SQL; profile counts match bronze | Baseline prior audit: 10,088 rows, 118 missing coordinates, 6 outside rough India bbox; re-measure |
| 03 Silver | All rows and IDs preserved; typed companion columns and geo flags exist | Do not turn missing capacity into 0; preserve raw arrays |
| 04 Identity | One identity-review row per facility, flagged rows have reasons | Example sample shows possible mixed-hospital text; automatically merging is unsafe |
| 05 Geography | One row per original facility; coordinates flagged | PIN directory row grain is post office, not PIN; aggregate before joining; straight-line km isn't travel time |
| 06 Services | >=6 canonical services with medical synonyms | Include negation and past-only claims; dialysis synonyms are not equivalent to operational verification |
| 07 Evidence | Claim points to exact original substring + source field | A list of URLs does not prove which URL backs a claim |
| 08 Verification | Restricted statuses: supported_claim, uncertain, conflicting, rejected | supported_claim = "dataset claims this"; not "independently clinically verified" |
| 09 Gold | One row per facility/service; nonempty source text; status and geo flags | Keep multiple facilities where branch identity ambiguous |
| 10 Ranking | Deterministic testable logic and negative-path tests | Never present arbitrary ranking weights as actual safety probabilities |
| 11 Evaluation | >=20 documented cases + Python tests | Sample cases must be manually reviewed for attribution and negative/referral risks; don't invent ground truth |
| 12 App | Databricks API reports app_status RUNNING and active_deployment SUCCEEDED | App service principal needs SQL+UC permission; user workflow still merits a real browser smoke test |
| 13 Final | Three end-to-end demo queries, an explicit user `--approve-final` signoff | No 'patient-safe' or clinically verified availability claim from this data alone |

## Suggested adversarial cases
1. Hospital physically outside India according to stored coordinates but India state/city text.
2. Hospital text describing another city or a different named hospital.
3. A dialysis *mention* without a clear offered-service statement.
4. A service negated in a description ("not available").
5. Two facilities with identical names but different branches.
6. A PIN code mapping to more than one post office or district.
7. Candidates lacking coordinates and/or source URLs.
8. Referral without enough supporting evidence: system must abstain or warn.

## Exact datasets
Primary source: `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities`

Optional India Post: `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory`

The 50-row `spec/dialysis_evidence_sample.csv` is a **sample of previously exported search results**, not a verified label set. Use it to develop rules and candidate tests, but label examples manually instead of accepting model inferences as truth.
