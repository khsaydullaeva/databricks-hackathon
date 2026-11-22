# Evaluation and failure cases

| Evidence layer | Observed outcome | Limitation |
| --- | --- | --- |
| Controlled Stage 11 fixtures | 7 ranked and 33 abstained across 40 cases. Zero unexpected asserted positives and zero failed cases. | Synthetic, author-labeled cases. Independent clinical precision and recall were unmeasured. |
| Fresh-export location audit | Ranked searches rose from 7/36 to 20/36. Abstentions fell from 29/36 to 16/36. Ambiguity fell from 24 to 0. Post-repair abstention was 44.4%. | Fixed cities and services, updated resolver and state-qualified queries. |
| Ranked lineage | 57/57 ranked placements matched their own original captured record and passed the audit's address-consistency checks. 512 placements remained under review. | The audit tests exact text lineage and reported address consistency. Current availability and street location require direct confirmation. |
| Existing private app | The user reported Dialysis / Jaipur with 2 ranked, 11 review leads and a 7.50 s warehouse read. The Stage 12 machine gate recorded a running app at 21:26:39 UTC. | Evidence for the existing deployment. The trace revision still needs browser review. |

## Three demo cases

- **Strong.** Dialysis / Jaipur returned two ranked. Liberty Hospital's verbatim capability excerpt is “Provides dialysis services” on record `5aaac470-a3fc-48e1-b01e-2bd42ff32a44`.
- **Conflicting.** Dialysis / Nagercoil abstained with one review lead in the captured replay. Mission Hospital, Nagercoil has the equipment excerpt “Dialysis unit” on record `0007a104-2bd7-412d-8ffd-7161e703dbc2`. The assessment flagged text mentioning another facility or branch.
- **Insufficient.** Cardiac surgery / Jaipur abstained with six review leads in the captured replay. CKS Hospital's capability excerpt “CKS Hospital offers Cardiology & Cardiac Surgery” on record `3973f354-7588-4375-a5c1-f85957185077` stayed under review with an evidence conflict.

The controlled suite also exercises negation, mention-only text, same-name facilities, ambiguity and missing coordinates. Coordinate radius searches may exclude facilities with unusable coordinates. City and PIN matches calculate no distance. Gold-based selection can omit offerings that were never extracted. The project has no externally labeled current-service ground truth and no independently adjudicated facility-level precision.

Sources [Stage 11 evaluation](11_evaluation.json), [Gold and Bronze capture](real_data_capture.json), [fresh-export audit](location_resolution_live_before_after.json), [Stage 12 gate](gate_12.json).
