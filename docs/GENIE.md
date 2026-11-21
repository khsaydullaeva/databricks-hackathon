# Optional: a Genie space for district planners

Genie answers questions in natural language over the gold tables. It works best for *aggregate* planning questions. Use the app for patient-level routing, because only the app runs the full location resolution and ranking logic.

## Create the space

Go to **Genie → New**, then add these tables from `workspace.findcare`:

- `v_service_evidence`: one readable row per facility × service, with its top quote
- `gold_district_service_coverage`
- `silver_nfhs_long`
- `gold_district_nfhs_map`
- `gold_facilities`

## General instructions (paste into the space)

```
- "Reliable" or "verified" means evidence_level IN ('Strong','Moderate'). Never count 'Indirect' as offering a service.
- Always show top_quote and top_quote_field when listing facilities for a service, so users see the evidence.
- trust_score < 0.6 means the record has contradictions; mention trust_flags when listing such facilities.
- Distances in gold_district_service_coverage are straight-line km from the district centre.
- NFHS-5 values are 2019-21 survey percentages. is_suppressed = true means not reported (do not treat as 0);
  is_low_n = true means based on 25-49 cases, so add a caution.
- Join coverage to NFHS via gold_district_nfhs_map (state_norm, district_norm) -> (nfhs_state_norm, nfhs_district_norm).
  Mention when match_method = 'fuzzy_name'.
- service_id values: dialysis, emergency_csection, nicu, cardiac_surgery, cath_lab, mri, ct_scan, burn_care, trauma,
  emergency, icu, oncology, radiotherapy, blood_bank, neurosurgery, maternity, transplant, cataract,
  joint_replacement, psychiatry.
```

## Sample questions

- Which districts in Bihar have no facility with Strong or Moderate NICU evidence?
- For dialysis, list the 10 districts farthest from a reliable facility, and include each district's high-blood-sugar percentage.
- How many facilities in Uttar Pradesh have Strong evidence for emergency C-section, and which have trust_score below 0.6?
- Show the evidence quotes for MRI facilities in Guwahati.
