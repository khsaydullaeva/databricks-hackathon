---
marp: true
theme: default
paginate: true
title: Find the Right Care, CMU Datathon Fall 2026
---

# Find the Right Care
### Evidence-checked healthcare routing for India

**Service + location → the best facility, with the quote behind it and how sure we are**

CMU Datathon Fall 2026 · Virtue Foundation × Databricks

Live app: `find-right-care-7474660607277698.aws.databricksapps.com`

<!-- Speaker: One sentence: a health worker gives us a service and a place; we return where to go, quote the facility record behind every claim, and say how much to trust it. -->

---

## The problem

A health worker in rural Bihar has a patient who needs **dialysis**. Where should they go?

- The Virtue Foundation dataset lists **10,000 facilities**, but its fields were extracted by AI from web pages.
- **Claiming** a service isn't the same as **offering** it.
- A confident wrong answer **costs a patient hours they don't have**.

**The brief's one rule:** for every recommendation, say **WHY this facility** and **HOW we decided**.

---

## What we built

| Input | Output |
|---|---|
| **Service:** 20 services, including dialysis, C-section, NICU, trauma, MRI and burns | The top facilities on a **3D map** |
| **Location:** city, district, 6-digit PIN, or a pin dropped on the map | For each: **quotes from its record**, evidence level, website confirmation, cautions |
| …or describe the need in plain words | An **AI assistant** that shows each step and checks its answer before you see it |

It runs entirely on **Databricks Free Edition**: Delta tables, serverless jobs, a SQL warehouse, hosted Foundation Models, MLflow and Databricks Apps.

---

## Demo (3 minutes)

1. **NICU near PIN 846004 (Darbhanga):** `?service=nicu&pin=846004&open=1`
   → the camera flies in; 3D columns (height = score, colour = evidence); the top facility's details show its quotes and score bars
2. **Held for review:** cardiac surgery near Jaipur. *CKS Hospital* mentions it, but fails a safety check, and the app says why
3. **Assistant:** *"Newborn born at 30 weeks, struggling to breathe, we are in Darbhanga"*
   → watch it live: its plan, `search_facilities(nicu, Darbhanga)`, the result, "✅ grounding check passed"
4. **Follow-up:** *"Why is the first one ranked above the second?"* → answered using our ranking-explanation tool

<!-- Speaker: Keep the Orbit button for the closing shot. -->

---

## The data was messy, so we measured how messy

| Problem found | Scale | What we did |
|---|---|---|
| Rows that are fragments of broken descriptions | 88 | Quarantined, leaving 10,000 facilities |
| Leftover AI-extraction text ("Cancer?", "not specified") | 808 items | Dropped and counted |
| Facility coordinates far from the record's own PIN (median gap 184 km) | 1,297 | Used whichever point is closer to the record's city; 817 use the PIN location |
| PIN directory: same-name geocoding errors (Aurangabad, Bihar placed in Maharashtra) | 2,203 rows | Rejected using a centre for each PIN area |
| PIN directory: whole districts misplaced (Vadodara at latitude 15.6) | 1,620 rows | Rejected using a robust state-extent test |
| Records that mix up two facilities (Kalyani vs Katyani, Shifa vs Shiva) | 635 | Flagged as "describes another facility" |

---

## WHY this facility: evidence you can quote

- **20 services**, each with keyword patterns: terms that name the service, related terms, and negation ("not available", "planned", "referred to").
- **Each field counts differently:** procedures 1.0 · equipment 0.95 · capabilities 0.9 · name 0.8 · description 0.6 · specialties 0.55.
- **Fields combine** as 1 − ∏(1 − field score). **Strong** requires the service to be named in **2 or more separate parts of the record**.
- **Every card quotes the record**, with the field name and the record ID.

**Independent check against each hospital's own website.** We crawled **7,651 official domains** and found **20,164 claims confirmed** there. A confirmation counts as a separate, independent field of evidence.

---

## Do the evidence levels mean anything? Yes.

Claims were rated using the record alone, then checked against the hospital's own website (44,445 claims where the site was reachable):

| Evidence level | Confirmed on the hospital's own website |
|---|---|
| **Strong** | **65%** |
| Moderate | 47% |
| Limited | 36% |
| Indirect | 17% |

The order is exactly right, and these are **lower bounds**, because hospital websites are often incomplete. The app shows this rate on every card, and this is how it **communicates uncertainty**.

---

## HOW we decided: transparent ranking

```
score = 0.45 × evidence + 0.30 × closeness + 0.15 × record quality + 0.10 × size
closeness halves every 20 km (emergency) / 40 km (urgent) / 80 km (elective)
```

**Safety mode** (from teammate Rafig's CareProof solution). A facility is ranked only if:
- its evidence is at least Moderate, **and**
- the quote describes **this** facility, **and**
- its record doesn't contradict itself, **and**
- no analyst has put it on hold.

Otherwise it is **held for review, with the reason**. If nothing passes, we **decline to rank** rather than guess.

Every search also shows:
- a **decision funnel** (how many facilities survived each check);
- an **equal-weight check** (would #1 change if every factor counted the same?);
- "**ranked above X because…**".

---

## The AI layer: an agent, but grounded

**Model:** Llama 3.3 70B, hosted by Databricks, using tool calls.

- **The model plans and explains; our tested code supplies every fact.** It has 5 tools: locate the place · search (with safety mode) · get evidence · district health data · explain the ranking.
- **Grounding check before any answer is shown.** Every quote, facility and distance must appear in the tool results, and "we are X% sure" is rejected. If the check fails, the model rewrites once; if it fails again, the user sees a summary built directly from the tool results.
- **Live working log** in the app, and every run is **traced in MLflow**.
- **AI claim checker:** `ai_query()` re-read **2,471 claims**, and any verdict whose supporting quote wasn't found word for word was discarded (27). **891 claims are backed by all three sources:** our rules, the AI and the hospital's website.

---

## We evaluated the agent with MLflow

**20 realistic test cases:** Hinglish, PIN codes, coordinates, "government hospital only", an ambiguous place, a missing place, and a medical-advice question it should decline. Scored by an AI judge (Llama 4 Maverick) plus 9 rule-based checks.

| Check | Result |
|---|---|
| Correct service and location | 100% |
| Asks when the place is ambiguous or missing | 100% |
| Declines medical advice; respects "government only" | 100% |
| AI judge (plain language, honest uncertainty, no medical advice) | 90% |
| **Answers users saw that were verified** | **100%** |

The model's own answer passed the grounding check 85–90% of the time. In every remaining case, the check caught the problem before the user saw it.

---

## Planner view: NFHS-5 district context

- Each search shows the patient district's NFHS-5 health indicators next to the state average. For example:
  - dialysis → high blood sugar and blood pressure
  - C-section → caesarean and institutional-birth rates
  - burns → clean cooking fuel
- **Missing values (`*`)** are shown as "not reported", never as zero; **small-sample values `(29.5)`** are flagged.
- **District names** are matched between datasets by exact name, then a rename table (Nuh = Mewat), then approximate spelling. When none works (usually a district created after 2019–21), we say "no data".
- **`gold_district_service_coverage`:** how far each district centre is from the nearest facility with reliable evidence, for finding underserved districts.

---

## Built on Databricks Free Edition

```
Marketplace tables ─► Bronze ─► Silver (clean, quarantine) ─► Website check ─► Gold (evidence, trust, lookups)
                                                                               │
                         ai_query AI claim checker ◄───────────────────────────┤
                                                                               ▼
               Databricks App: FastAPI + 3D map ◄── SQL warehouse ◄── workspace.findcare.*
                       │
                       └─► Llama 3.3 (hosted Foundation Model) ─► MLflow traces and evaluation
```

- **Open-source 3D map:** MapLibre GL + deck.gl + OpenFreeMap + AWS terrain tiles.
- **One Python package** (`findcare`) powers the notebooks, the app and the agent.
- **Tests:** 46 pass on Databricks' pandas 1.5 and on pandas 3. Deploying caught a bug where pandas 1.5 silently overwrote IDs, and we fixed it.

---

## Limitations and what's next

- **"Confirmed" means a website says so.** We can't see availability today, cost or quality, so the app always says **call ahead**.
- **Distances are straight-line**, and districts come from PIN codes rather than boundary maps. Next: point-in-polygon matching with geoBoundaries.
- **No human-labelled ground truth yet.** `gold_review_queue` (3,541 records) is ready for labelling, and the calibration gives a lower bound meanwhile.
- **Next:**
  - a Genie space for planners, called from the assistant;
  - search by meaning (embeddings) for paraphrases like "kidney machine";
  - phone verification of the top recommendations.

---

# Thank you

**Every answer is cited, checked, and honest about uncertainty.**

- **Code:** `github.com/khsaydullaeva/databricks-hackathon`
- **Details:** `docs/METHODOLOGY.md` · `docs/agent_eval_results.md`
- **Team:** Find the Right Care, plus Rafig Mammadov's CareProof (attribution guards, safety mode, decision funnel)
