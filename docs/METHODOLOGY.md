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

## 2b. Fact-check against the hospital's own website

The record's fields all come from the same directory and listing pages, so agreement between them is weak corroboration. To get an **independent** source, we fetch each facility's *official* website and look for the service there. The checker (`webcheck.py`, run by `scripts/verify_websites.py` or notebook `02b`) does the following:

- Fetches the homepage plus up to 4 internal pages whose link text or URL suggests services (Services, Departments, Facilities, …).
- Respects robots.txt and uses 10 s timeouts.
- Skips directory sites (Practo, Justdial, business.site, …), because they aren't the hospital speaking.
- Searches the pages with the same strong-term rules and negation handling as the record. Two extra rules apply to web text, because websites contain generic marketing language:
  - Generic words don't count: "Medicine *Delivery*" isn't childbirth, and "blood *transfusions*" in a disease explainer isn't a blood bank.
  - Blog, story, news, careers and vacancy text is ignored.

| Website outcome | Effect |
|---|---|
| Hospital's **own** site names the service | new evidence field "Hospital's own website" (weight 1.0) with quote and link. Together with the record, this reaches **Strong**. |
| A **group** site (domain shared by 3+ facilities) names it | weight 0.4 only, because the page may describe another branch |
| Site reachable, service **not** named | caution only. Hospital websites are often incomplete, so absence isn't proof. |
| Unreachable, no website, or directory only | no effect |

If a facility's own site names a service its record didn't claim, it gains a new evidence row. Users can switch on "Only show services confirmed on the hospital's own website".

**Results on the real data.** We crawled 7,651 official domains:
- 5,843 facilities' sites were reachable, 2,200 were not, 1,610 facilities have no website, and 344 list only directory sites.
- Own websites confirmed 20,164 claims, across about 3,900 facilities.
- Spot-checks of website confirmations were about 90% genuine: department menus, named units, "Cath Lab", "NICU".

**Independent validation of the evidence levels.** We took the levels computed from the record alone, which use no website information, and checked them against the hospitals' own sites (44,445 claims where the site was reachable). The share confirmed falls steadily as the level falls:

| Record-only level | Confirmed on own website | Claims checked |
|---|---|---|
| Strong | 65% | 13,154 |
| Moderate | 47% | 5,595 |
| Limited | 36% | 3,557 |
| Indirect | 17% | 22,139 |

These rates are lower bounds on precision, because a site that doesn't mention a service isn't proof the hospital lacks it. They are stored in `gold_evidence_calibration` and shown on every result card.

## 2c. Records set aside for later labelling

`gold_review_queue` separates records that are incomplete or untrustworthy, so a person can label them later. Each record gets a priority:

- **+2** for each contradiction flag: coordinates vs postcode, state vs postcode, possible merged sites, implausible service breadth, or no location.
- **+1** if no size data at all is recorded.
- **+1** if its own website is reachable but mentions none of the services the record claims.

Records with priority ≥ 2 are marked `needs_review`. The app still shows them, because in rural areas they may be the only option, but it marks them "⚑ Record needs review" and offers a "Hide records that need review" switch.

## 2d. Safety checks and abstention (combined from CareProof)

These ideas come from our teammate's CareProof solution, which is in the `careproof/` folder with its own SQL pipeline, app and audit.

- **Attribution guard.** A quote can describe a *different* facility ("Katyani Hospital is a General Surgery Clinic in Zirakpur" inside the record for *Kalyani* Hospital) or a different branch city ("Provides dialysis at Namakkal"). We detect both:
  - A record whose text declares another facility is flagged as possibly mixed. That's 635 facilities, mostly near-identical names the upstream data merged.
  - A claim whose naming quotes all describe someone else doesn't count as evidence.
- **Analyst review holds.** CareProof's hand-reviewed facility × service holds and coordinate holds (`app/findcare/data/*.json`) withhold those pairs, and the app shows the analyst's reason and quotes.
- **Safety mode, on by default.** A facility is ranked only if all four checks pass:
  - evidence is at least Moderate;
  - the quote describes this facility;
  - the record is consistent with itself;
  - there's no analyst hold.

  Facilities that fail are listed under *Held for review* with the reason. If none pass, the app **abstains** and says why, rather than guessing.
- **Decision funnel.** Every search reports how many facilities survived each step, and why the others dropped out.
- **Equal-weight sensitivity.** Every search reports whether the #1 answer would change if all four factors counted equally.

## 2e. The AI layer: agentic, but grounded

We use AI where it adds value, but facts never come from the model. The model plans and explains; tested, audited code supplies every fact.

**Care-finding agent** (`app/findcare/agent.py`, "Ask the assistant" in the app). Llama 3.3 70B runs on the Databricks Foundation Model API, with tool calling.

- **What the model does:** turns a free-text request ("her kidneys failed, she's near Gaya and can't travel far", or "dialysis kahan milega Varanasi mein?") into tool calls. It asks when the place is ambiguous or missing, handles follow-ups ("why is #1 above #2?", "government hospitals only"), and writes the answer.
- **Its five tools** wrap the pipeline described above:
  - `resolve_location`
  - `search_facilities`, which applies the safety checks and can decline to rank
  - `get_facility_evidence`
  - `district_health_context`
  - `explain_ranking`
- **Grounding check before any answer is shown:**
  - every quoted phrase (including ones abbreviated with "…") must appear in the tool results;
  - every facility named must have been returned by a tool;
  - distances must match the tool results;
  - phrases like "we are 65% sure" are rejected, because the calibration is a website-confirmation rate, not a probability.

  If the answer fails, the model gets one chance to fix it. If it fails again, the user sees a summary built directly from the tool results instead.
- **Tracing:** every LLM and tool call is recorded with MLflow, in the experiment `findcare-agent`. In the app, "How the assistant decided" lists each tool call and its result.

**AI claim checker** (notebook `03b`, run with `ai_query` in SQL). Llama 3.3 re-read 2,471 claims for dialysis, emergency C-section and NICU. For each it returned a verdict (*offered / not_offered / planned / other_facility / unclear*) and a supporting quote copied word for word. A verdict whose quote isn't found in the record is discarded; that happened 27 times.

Results:
- **Some extra signal for Strong claims:** the hospital's own website confirmed the service 65% of the time when the AI also said "offered", and 55% when it didn't. For Moderate claims the AI added nothing.
- **891 claims are backed by all three sources:** our rules, the AI reader and the hospital's own website.
- **How we use it:** the AI reader appears as a badge on each card, and its doubts become cautions. We don't use it as a hard filter, because the data shows its signal is modest.

**Evaluation** (`scripts/evaluate_agent.py`, MLflow GenAI evaluate, 20 realistic cases; see [agent_eval_results.md](agent_eval_results.md)). The test cases cover condition-to-service mapping, Hinglish, PIN codes, coordinates, a government-only request, an ambiguous place, a missing place, and a medical-advice question the agent should decline. There are 9 deterministic checks plus an AI judge (Llama 4 Maverick) that checks for plain language, honest uncertainty and no medical advice.

On the latest run:
- 100% correct service, location, clarifying questions, declining out-of-scope questions, respecting "government only", and telling users to call ahead.
- The model's own answer passed the grounding check 85–90% of the time across runs. In every remaining case the check caught the problem and the verified summary was shown, so **100% of the answers users saw were verified**.
- The AI judge passed 90%.

## 3. Trust: is the record internally consistent?

`trust = clamp(1 − Σ penalties, 0.1, 1) × (0.85 + 0.15 × completeness)`

Completeness is the share of these that the record has: doctors, beds, year established, equipment.

| Check | Penalty | What the user sees |
|---|---|---|
| No usable coordinates or postcode | 0.35 | "distance cannot be computed" |
| No coordinates; location taken from the postcode centre | 0.10 | "location approximated from postcode" |
| Coordinates more than 40 km from the postcode centre | 0.20 | "one of them is wrong", plus which one we used |
| Record's state differs from the postcode's state (only when the field holds a real state name) | 0.15 | "Record says X, postcode belongs to Y" |
| At least 4 different facilities share identical coordinates | 0.10 | "likely a city-level geocode" |
| Text mentions postcodes in other districts | 0.15 | "may merge more than one site" |
| Claims ≥ 65% of all service types without 50+ beds (medical colleges exempt) | 0.10 | "may be a generic or merged listing" |
| No source URL | 0.10 | "claims cannot be traced" |

**Resolving a conflict between coordinates and postcode.** When the two disagree, we use whichever point is closer to the city named in the record. If the city can't be located, the city/state text votes. Either way the flag is shown, along with the reason for the choice.

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
- PIN-directory districts are matched to NFHS districts within the same state: an exact match first, then a table of known renames (Nuh → Mewat, Ballari → Bellary, …), then a fuzzy match (similarity ≥ 0.80, labelled in the UI). If none of these works (usually a district created after the survey), we say "no NFHS-5 data" rather than guess.
- The Marketplace NFHS table has abbreviated column names, so the indicators shown for each service are curated in `nfhs.SERVICE_INDICATORS`, with plain-English labels.
- NFHS-5 data is from 2019–21. It describes the population, not any facility.

The planner view in notebook 05 combines need (NFHS) with supply (`gold_district_service_coverage`: the distance from the district centre to the nearest facility with Strong or Moderate evidence) to rank underserved districts.

## 7. What the real data looked like

On the real Marketplace tables, the evidence extractor found these Strong / Moderate counts:

| Service | Strong | Moderate |
|---|---|---|
| Dialysis | 726 | 320 |
| Emergency C-section | 560 | 112 |
| NICU | 286 | 452 |
| Burn care | 86 | 91 |
| Cardiac surgery | 593 | 265 |
| MRI | 668 | 278 |

Thousands more records mention only related terms (Indirect). Those are never recommended.

Spot-checks of random Strong and Moderate matches were almost all correct, for example "Provides hemodialysis and CRRT in the dialysis center", "Level 3 NICU" and "Burn care ward". The errors we found led to rule changes:
- Future plans were being read as current services ("Phase II will include TrueBeam…", "Plans to add a radiation oncology program"), so we added future/plan wording to the negation rules.
- Corneal transplants were counted as organ transplants; they now count as eye care.
- AI-extraction leftovers ("Cancer?", "not specified") are now dropped.

The real-data cleaning is listed in the README; the most important issues were geocoding errors in both the facility table and the PIN directory.

**Settling disagreements between coordinates and postcode.** In 1,671 records the two disagree by more than 40 km, with a median gap of 184 km. When the record's own city can be located (as a district or post office in its state), we use whichever of the two points is closer to that city. When it can't, the city/state text votes.

## 8. Known limitations (and what we would do next)

- **Corroboration within one record is weak.** The fields were all extracted from the same pages. The website fact-check (section 2b) adds an independent source, but only for facilities with a reachable official site. A hospital's own site can also over-claim.
- **Keyword matching** can miss paraphrases ("kidney machine"). An LLM extractor could catch these, but its output would also need citations. We chose transparent rules that a reviewer can audit.
- **No real-time availability**, cost, or quality-of-care data. The app tells users to call ahead every time.
- **Straight-line distance**, and district assignment without polygons (see section 4).
