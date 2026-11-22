# Jaipur dialysis challenge audit

The user's production observation reported two ranked facilities, eleven verification leads, and totals of 70 and 68. The captured Gold and original Bronze fields reproduce that outcome. The archived Stage 13 gate passed at 21:43:41 UTC. It checks report shape, approval, and deployed app status. It does not assert the deployed source revision or inspect the app browser. A new app status request in this sandbox failed before reaching Databricks because profile `careproof` had no cached OAuth credentials.

## WHY each facility

Each of the three Gold quotes matches the captured original field byte-for-byte as a substring under its facility ID and source content ID. Each captured ID has one Bronze row. Gold's full field equals the captured Bronze field for these cases. All three records use Jaipur, Rajasthan. The administrative match supports relevance to the requested place. The ranks establish neither travel convenience nor clinical suitability.

| Facility | Bronze facility ID | Source content ID | Exact excerpt and field | Automated identity and evidence |
| --- | --- | --- | --- | --- |
| Liberty Hospital | `5aaac470-a3fc-48e1-b01e-2bd42ff32a44` | `9b0d282e-54be-4b79-99b7-877c7e61019e` | “Provides dialysis services” · capability | One identity review, one clear, no flags. Candidate offering, no detected service negation, `attributable_dataset_offering_text`. |
| SCR Hospital | `4105a305-4d33-49e1-ad26-d0c3d91bc076` | `71e7e92d-a634-4362-82b4-ba43572d6162` | “Provides dialysis services” · procedure | One identity review, one clear, no flags. Candidate offering, no detected service negation, `attributable_dataset_offering_text`. |
| CKS Hospital | `3973f354-7588-4375-a5c1-f85957185077` | `d340149e-f675-40a3-b37b-10bafe1dbb20` | “CKS Hospital offers Nephrology & Dialysis” · capability | One identity review, zero clear. Identity and assessment reason `text_mentions_other_facility_or_branch`. Extracted as `mention_only` with `no_explicit_offering_cue`. No detected service negation. |

Liberty passes the existing automated attribution criteria. Its original capability array includes “Dialysis wing started in 2003” and the exact present-tense excerpt. The field also gives two different 1999 bed capacities (100 and 200) and separately says “Established in 2000”. Its Bronze description is just the facility name. The claim is safely described as **an exact excerpt tied to the Liberty dataset row**. The capture cannot establish that it belongs to the correct operating branch in the real world or that dialysis is available today. A clear identity flag means the current rules found no modeled issue. SCR's original description separately names dialysis in Vaishali Nagar within the same record, so it adds context but no independent source confirmation.

CKS's own exact quote remains visible. Its Bronze capability array also names “RS Hospital” in a statement locating that facility in Ajmer. That is a concrete cross-facility reference consistent with the captured identity flag. The capture preserves the reason code, while the exact Stage 04 matched row identifier is stored in `identity_review.other_facility_mentions` and was unavailable in this sandbox. Stage 08 maps that facility-level flag to `conflicting` even for a self-named quote. There is also an independent extraction limitation. The Stage 06 pattern requires the offering verb directly before the service alias, apart from an optional “onsite/on site”; “offers Nephrology & Dialysis” fails that pattern after normalization. Changing the cue alone would still leave the identity gate in place. A future rule change should first adjudicate real examples of coordinated offerings and mixed-facility arrays and test them against branch, referral and negation fixtures. This audit changes neither rule.

URLs for all three are `unverified_row_leads`. They are record-level leads, without a confirmed excerpt-to-page mapping.

## HOW the ranking was decided

The ranker screens service, attribution, exact excerpt and geography before it scores. The fixed source-field table gives capability **60**, procedure **58**, description **56**, specialties **54** and equipment **52**. The numerical differences have no empirical calibration in the project evidence. The policy ranks by descending total and uses ascending facility ID for exact ties. An eligible address with the required city/state status receives 10 points. Proximity is zero without a computed distance. For eligible coordinate matches, it decreases linearly from 30 at the origin to zero at the radius.

Liberty and SCR both received **10 address points** and **0 proximity points**, so the **70–68** result came entirely from the field weight. The excerpt is identical. CKS received no score because it failed eligibility. No distance was computed for Jaipur.

## UI and remaining compliance gaps

The local source already called the six-step trace on ranked results, no reliable match, ambiguous place and invalid location or radius. It previously appeared after the results and comparison on the normal path. The revision moves it next to the results header, displays the executed score table and checked identity and claim states, and shows when candidate screening stopped early. The claim excerpt and full recorded field are explicitly distinguished. Unverified links remain accessible in a disclosure with batches of at most ten when needed, displaying the URLs.

The revised UI addresses WHY at the level of dataset record identity, a grounded service claim and recorded-location fit. It addresses HOW through the screening outcomes, field table, score components and tie-breaker. The archived evidence cannot independently establish clinical availability, street-level location, branch identity, claim-to-URL corroboration, or calibrated weights. The user's production excerpt lacks the trace. A deployed snapshot mismatch or a collapsed/off-screen panel remains possible; this sandbox could not determine which. Review the revised snapshot in an authenticated browser after separately approved redeployment.

Evidence [captured Gold and Bronze](real_data_capture.json), [identity SQL](../sql/04_identity.sql), [cue SQL](../sql/06_taxonomy.sql), [verification SQL](../sql/08_verification.sql), [ranker](../careproof/recommend.py), [Stage 13 gate](gate_13.json).
