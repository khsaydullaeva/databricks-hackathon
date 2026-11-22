# CareProof decision audit and score sensitivity

This replay uses the archived Databricks export in [real_data_capture.json](real_data_capture.json). Counts describe that slice. An authenticated app search uses the service rows returned by its own warehouse query.

## Jaipur decision funnel

| Executed step | Dialysis | Cardiac surgery |
| --- | ---: | ---: |
| Service rows across the capture | 128 | 56 |
| Distinct facility IDs after exact-row deduplication | 128 | 56 |
| Facility IDs outside the recorded address match | 115 | 50 |
| Matched facility IDs assessed | 13 | 6 |
| Dataset identity review passed / failed | 11 / 2 | 4 / 2 |
| Offering cue passed / failed | 2 / 11 | 0 / 6 |
| Ranked / requires verification | 2 / 11 | 0 / 6 |

The executed resolver matched `city=jaipur`, `state=rajasthan`. Identity and offering screens share the geographically assessed denominator and can fail for the same facility. An address match can precede a geographic-quality concern. Coordinate searches instead require an eligible point inside the radius. The funnel labels exact duplicate rows, inconsistent ID groups, unmatched address groups and coordinate exclusions separately. An ambiguous or invalid request records no candidate-screening counts. The app still retrieves rows before validation, so retrieval counts may exist on an early stop.

## Canonical policy

The ranker normalizes the requested service and place. It groups service rows with populated facility IDs and ignores exact duplicates. A matching facility with competing distinct rows is withheld. For address queries it screens recorded administrative fields and geographic quality. For coordinate queries it assesses a bounding-box-checked point and radius, excluding unusable or out-of-radius rows.

To rank, a candidate must have an exact nonempty excerpt in its recorded field, `supported_claim`, `candidate_claim`, no detected explicit service negation, exactly one identity review and one clear review with no reason flags, the `attributable_dataset_offering_text` assessment reason, and no flagged geographic issue. The ranker assigns:

- Evidence points by field. Capability 60, procedure 58, description 56, specialties 54, equipment 52.
- Address consistency. 10 if the eligible record carries `dataset_address_unverified`, otherwise 0.
- Proximity. If a distance exists, round `30 × max(0, 1 − distance_km / radius_km)` to six decimals. Otherwise 0. The distance is great-circle distance.
- Total. Sum the three components and round to six decimals. Sort descending total, then ascending original facility ID.

These are fixed heuristics. There is no empirical calibration establishing that a two-point field difference implies greater reliability or clinical quality.

## Reproducible sensitivity

Run `python -m careproof.score_sensitivity` from the project root. It first executes the unchanged ranking policy, then replaces only capability and procedure evidence points with their midpoint of 59 for candidates already eligible. All other components and eligibility are held fixed. Production scoring does not use this scenario.

| Candidate | Original evidence + address + proximity | Original total | Equal-field total |
| --- | --- | ---: | ---: |
| Liberty Hospital | 60 + 10 + 0 | 70 | 69 |
| SCR Hospital | 58 + 10 + 0 | 68 | 69 |

Under equal weights, SCR comes first solely because its facility ID `4105a305-4d33-49e1-ad26-d0c3d91bc076` sorts before Liberty's `5aaac470-a3fc-48e1-b01e-2bd42ff32a44`. CKS remains withheld. No facility was added, removed or rescored in production.

## Facility decisions

| Check | Liberty | SCR | CKS |
| --- | --- | --- | --- |
| Grounded excerpt | PASS · “Provides dialysis services” in capability | PASS · same excerpt in procedure | PASS · “CKS Hospital offers Nephrology & Dialysis” in capability |
| Offering cue | PASS | PASS | FAIL · mention-only extraction |
| No explicit denial detected | PASS | PASS | PASS |
| No other-facility reference flagged | PASS | PASS | FAIL · cross-facility reference |
| Automated identity review | PASS · one clear of one | PASS · one clear of one | FAIL · zero clear of one |
| Recorded geography | PASS · Jaipur, Rajasthan | PASS · Jaipur, Rajasthan | PASS · Jaipur, Rajasthan |
| Distance | NOT EVALUATED | NOT EVALUATED | NOT EVALUATED |
| Outcome | Eligible · 70 | Eligible · 68 | Ineligible · no score |

Liberty's full field contains inconsistent history and capacity statements. Its quote is tied to the dataset record. Confirm the operating branch and current service directly. SCR has identical offering wording and geography points. Two field points produce the entire original rank difference. Actual nearness cannot be inferred without a search origin and validated facility locations.

CKS's original capability array contains “RS Hospital is a hospital facility located at Pragati Nagar, Near Sky Tower, Ajmer.” The captured identity flag records a cross-facility or branch reference. The Gold claim preserves the original field but does not contain the exact Stage 04 matched entity ID. Confirm whether the RS Hospital reference belongs to a separate record and seek independent branch-specific support. The offering cue rule independently misses the coordinated phrase “offers Nephrology & Dialysis.” Clearing either concern alone would be insufficient. [Original IDs and earlier provenance audit](jaipur_dialysis_explainability_audit.md)

## Correctness and limitations

The prior explanation had only retrieved-state and final-result counts, so it could not distinguish skipped screening, geographic mismatches and dropped rows. This revision records the actual decision boundaries without changing the returned ranking. The existing coordinated-offering extraction limitation remains. Coordinate queries can also withhold competing Gold rows before evaluating their radius, which the funnel now identifies explicitly. The evidence still requires confirmation of branch, availability and access.
