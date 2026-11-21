# Methodology: how we turn about 10,000 claims into one trustworthy answer

Every recommendation has to answer two questions:

- **WHY this facility?** What evidence says the facility exists, offers the service, and is the best choice for this location?
- **HOW did we decide?** What logic turned the records into this answer, and what did we trade off?

All of the logic lives in [`app/findcare/`](../app/findcare). The Delta tables, the notebook and the app all run this same code.

---

## 1. Treat every field as a claim

The facility records were pulled out of web pages by AI, so we never take a field at face value. Each answer has three independent parts:

| Question | Component | Range | Code |
|---|---|---|---|
| Does it offer the service? | **Evidence score** | 0–1 | `evidence.py` |
| Can we trust this record? | **Trust score** | 0.1–1 | `quality.py` |
| Is it reachable? | **Proximity** | 0–1 | `ranking.py` |

Facility size (beds and doctors) is a small fourth factor.

## 2. Evidence: does the record actually say it offers this service?

For each of the 20 services, [`services.py`](../app/findcare/services.py) defines two kinds of term:

- **Strong terms** name the service itself, for example `hemodialysis`, `dialysis unit`, `LSCS`, `caesarean`, `NICU`, `SNCU`, `cath lab` or `burns unit`.
- **Support terms** make the service plausible without proving it, for example `nephrology` for dialysis or `obstetrics` for C-section.

We search six parts of each record. Fields that list concrete things count for more than free prose:

| Field | Weight | Reason |
|---|---|---|
| procedure | 1.00 | names a specific act the facility performs |
| equipment | 0.95 | physical capability, such as dialysis machines or an MRI scanner |
| capability | 0.90 | structured claim |
| name | 0.80 | for example "XYZ Dialysis Centre" |
| description | 0.60 | marketing prose that is often generic |
| specialties | 0.55 | a department, not proof of a specific service |

**Field contribution** = 0.75 × field weight × term weight. The term weight is 1.0 for a strong term and 0.35 for a support term. Within a field, only the best match counts.

**Combining fields (noisy-OR):** evidence = 1 − ∏(1 − contribution).

- One field can contribute at most 0.75. So the **Strong** level (0.80 or more) is only reachable when the service is named in **two or more separate parts of the record**. Repetition inside one paragraph doesn't count as corroboration.
- If the record contains only support terms, evidence is capped at 0.30. That makes it **Indirect**, which is never recommended and only shown as a lead.

| Level | Score | Plain-language meaning shown to the user |
|---|---|---|
| Strong | ≥ 0.80 | named in two or more separate parts of the record |
| Moderate | ≥ 0.55 | clearly named, but in only one part of the record |
| Limited | ≥ 0.31 | mentioned once, in a less reliable part of the record |
| Indirect | > 0 | only related terms; the service itself is not named |

**Negation and "not really" wording.** A mention is excluded when, within a few words of it, the text says *no, not, without, not available, planned, upcoming, coming soon, under construction, referred to / referred out*. We don't hide excluded mentions. The app shows them under "Mentions we ignored", and notebook 05 lists all of them for audit.

**Plausibility.** Some services are tertiary: NICU, cardiac surgery, cath lab, MRI, burns, neurosurgery, oncology, radiotherapy and transplant. When one of these is claimed by a facility typed as a clinic, pharmacy, dentist or doctor, evidence is multiplied by 0.75. (Diagnostic centres claiming MRI or CT are exempt.) When the record shows fewer than 20 beds, evidence is multiplied by 0.85. The user sees both adjustments as cautions.

**Every recommendation quotes the field text it is based on**: the list item, or the sentence from the description.

## 3. Trust: is the record internally consistent?

`trust = clamp(1 − Σ penalties, 0.1, 1) × (0.85 + 0.15 × completeness)`

Completeness is the share of these that the record has: doctors, beds, year established, equipment.

| Check | Penalty | What the user sees |
|---|---|---|
| No usable coordinates or postcode | 0.35 | "distance cannot be computed" |
| No coordinates; location taken from the postcode centre | 0.10 | "location approximated from postcode" |
| Coordinates more than 40 km from the postcode centre | 0.20 | "one of them is wrong", plus which one we used |
| Record's state differs from the postcode's state | 0.15 | "Record says X, postcode belongs to Y" |
| At least 4 different facilities share identical coordinates | 0.10 | "likely a city-level geocode" |
| Text mentions postcodes in other districts | 0.15 | "may merge more than one site" |
| Claims ≥ 65% of all service types without 50+ beds | 0.10 | "may be a generic or merged listing" |
| No source URL | 0.10 | "claims cannot be traced" |

**Resolving a conflict between coordinates and postcode.** When the two disagree, the record's own city and state text gets a vote. If the postcode matches more of {city, state} than the coordinates do, we use the postcode centre for distance. Otherwise we keep the coordinates. Either way the flag is shown.

**Duplicates.** Records with the same normalised name within 2 km are collapsed into one result, and the result says so.

## 4. Geography

- **PIN codes.** The directory has one row per *post office*, and one PIN can span several districts. We aggregate to one row per PIN using the median of the geocoded offices, after dropping offices more than 50 km from the first median (bad geocodes and `NA` values are excluded). We keep `n_districts`, `other_districts` and `spread_km`. Ambiguous PINs and widely spread PINs produce a warning. We never do a raw join on `pincode`, which would multiply rows.
- **Place names.** Names are looked up in this order: facility cities (with at least 2 records), then districts, then post offices. Common renames are handled (Bombay → Mumbai, Gurgaon → Gurugram, …), and close misspellings are matched fuzzily with a "closest name" warning. When a name exists in several states (Aurangabad is in both Bihar and Maharashtra), the user is asked to choose.
- **Coordinates.** We check them against India's bounding box. Swapped (lon, lat) input is detected and corrected, with a note.
- **Distance.** Straight-line (haversine). The user is told that road distance is usually 1.2–1.5× longer.
- **District for health context.** We use the facility's postcode district, or failing that the nearest district centre. The method is stored in `district_method`. We didn't use polygons because no boundary file is bundled. That is a documented limitation; the upgrade path is a point-in-polygon join with geoBoundaries.

## 5. Ranking: what we weigh and what we trade off

```
score = 0.45 × evidence × plausibility
      + 0.30 × 0.5^(distance_km / half_life)
      + 0.15 × trust
      + 0.10 × size            (+0.03 if a 24x7 / emergency-department signal, for emergency services only)
```

- **Half-life depends on urgency.** It is 20 km for emergency services (C-section, NICU, trauma, burns, ICU, cath lab), 40 km for urgent ones (dialysis, CT, neurosurgery) and 80 km for elective ones (cardiac surgery, MRI, oncology, cataract). A facility 40 km away keeps only 25% of its closeness score for an emergency, but 71% for elective surgery.
- **Size** is a log scale of beds (saturating at 500) and doctors (saturating at 100). When neither is known it is a neutral 0.30. We don't penalise missing size data heavily, because 75% of records lack a bed count.
- **Gating.** Only facilities with plausibility-adjusted evidence of 0.31 or more (Limited or better) are recommended. The user can raise this to Moderate or Strong only.
- **Radius.** If fewer than 3 options qualify, the search radius doubles (to 2×, then 4×, up to 600 km), and the user is told.

**The main trade-off is evidence versus distance.** A closer facility with weak evidence can rank below a farther one with strong evidence, because a wasted trip to a facility that doesn't really offer dialysis costs the patient more than a longer drive. For emergencies the short half-life pushes the balance back towards proximity. Each result's "Ranked above X mainly because…" line states which factor decided the order. Users can change the weights in the sidebar.

**Robustness.** Notebook 05 changes each weight by ±0.10 and reports how often the #1 answer stays the same.

## 6. Demand-side context (NFHS-5)

For the patient's district we show the NFHS-5 indicators relevant to the service, next to the state average. For example:
- dialysis: blood sugar and blood pressure
- C-section: caesarean rate and institutional births
- burns: clean cooking fuel

How the data is handled:
- `*` (suppressed) is shown as "not reported", never as 0.
- `(29.5)` values are flagged "low sample (25–49 cases)".
- PIN-directory districts are matched to NFHS districts within the same state: an exact match first, then a fuzzy match (similarity ≥ 0.80, labelled in the UI). If neither works, we say "could not be matched" rather than guess.
- NFHS-5 data is from 2019–21. It describes the population, not any facility.

The planner view in notebook 05 combines need (NFHS) with supply (`gold_district_service_coverage`: the distance from the district centre to the nearest facility with Strong or Moderate evidence) to rank underserved districts.

## 7. Known limitations (and what we would do next)

- **"Corroboration" is within one record.** The fields were all extracted from the same web pages, so two fields agreeing reduces *extraction* error but not a false claim on the website itself. Next step: weight by the number of distinct source domains.
- **Keyword matching** can miss paraphrases ("kidney machine"). An LLM extractor could catch these, but its output would also need citations. We chose transparent rules that a reviewer can audit.
- **No real-time availability**, cost, or quality-of-care data. The app tells users to call ahead every time.
- **Straight-line distance**, and district assignment without polygons (see section 4).
