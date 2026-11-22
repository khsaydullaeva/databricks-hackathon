# Find the Right Care: CMU Datathon, Fall 2026 (Virtue Foundation × Databricks)

You give it a **service** (dialysis, emergency C-section, NICU, cardiac surgery, MRI, burn care, and 14 more) and a **location** (city, district, 6-digit PIN code, or `lat, lon`). It returns the best facility options, and for each one it shows:

- **why**: the exact sentence from the facility record that supports the claim, which part of the record it came from, and how strong that evidence is;
- **how**: the score arithmetic (evidence, closeness, record quality, size), what decided the order, and what we traded off;
- **cautions**: contradictions in the record, approximate locations, possible merged records, claims we ignored, and the reminder to always call ahead.

The full scoring rules are in **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)**.

## Combined solution (branch `combined`)

This branch merges two team solutions:

- **Find the Right Care** (`app/`, `notebooks/`): PIN, district and coordinate search with data cleaning; 20 services; NFHS-5 district context; fact-checking against each hospital's own website; calibrated evidence levels.
- **CareProof** (`careproof/`, by our teammate): a staged SQL pipeline with quality gates, attribution guards, analyst review holds, abstention, a decision funnel and sensitivity analysis.

The app in `app/` uses CareProof's best ideas directly: safety mode with abstention, the attribution guard, analyst holds, the decision funnel and the equal-weight check. See [docs/METHODOLOGY.md §2d](docs/METHODOLOGY.md).

## AI assistant (branch `agent`)

- **💬 Ask the assistant:** describe the need in your own words. A Llama 3.3 70B agent on the Databricks Foundation Model API calls our audited tools (location, ranking with safety checks, evidence, district context, ranking explanation). Every answer is checked against the tool results before you see it, and each step is traced in MLflow.
- **🤖 AI reader:** `ai_query` re-read 2,471 claims and returned a verdict plus a word-for-word supporting quote. It appears as a badge and as cautions on each card.
- **Evaluation:** MLflow GenAI evaluate on 20 cases, with 100% of user-facing answers verified. See [docs/agent_eval_results.md](docs/agent_eval_results.md) and METHODOLOGY §2e.

## Live deployment

- **App:** https://find-right-care-7474660607277698.aws.databricksapps.com (Databricks App `find-right-care`, workspace `dbc-35db76e6-0782`). You need workspace access to open it.
- **Tables:** `workspace.findcare.*`, built by notebooks 01 → 02 → 02b → 03 on serverless compute.
- **Note:** serverless runs pandas 1.5.3 and numpy 1.23.5. The test suite passes on both pandas 1.5.3 and pandas 3.
- **App resources:** `sql-warehouse` (CAN_USE), `llm` = `databricks-meta-llama-3-3-70b-instruct` (CAN_QUERY), `experiment` = `findcare-agent` (CAN_EDIT, for traces).

## Who it's for

| User | Workflow |
|---|---|
| Community health worker (for example in rural Bihar) | Open the app, pick "Dialysis", type the village PIN, and read the best option plus its phone number. Green, orange and red badges show how sure we are. |
| NGO coordinator | Compare the top 5 options, read the cautions, and download a CSV to share. |
| District planner | Use notebook 05's planner view (high NFHS-5 burden and far from well-evidenced care) or a Genie space over the gold tables. |

## Repository layout

```
app/                      Databricks App (Streamlit) + the shared logic package
  app.py                  UI
  app.yaml, requirements.txt
  data.py                 loads gold tables from a SQL warehouse (or local parquet)
  findcare/               ALL logic: services taxonomy, evidence, trust, geo, NFHS, ranking
notebooks/                Databricks notebooks (medallion pipeline + notebook UI + evaluation)
  00_common.py            shared setup (%run)
  01_bronze_ingest.py     Marketplace tables / CSVs -> bronze (auto-discovers sources)
  02_silver_clean.py      cleaning + data-quality profile (+ quarantine of malformed rows)
  02b_web_factcheck.py    fact-check claims on each hospital's own website (load crawl results, or crawl)
  03_gold_evidence.py     trust flags, evidence per facility x service, lookups, coverage
  04_find_care.py         interactive notebook version of the app (widgets)
  05_evaluation.py        spot-checks, negation audit, benchmark queries, weight sensitivity, planner view
docs/METHODOLOGY.md       WHY / HOW, scoring tables, trade-offs, limitations
docs/GENIE.md             optional Genie space for planners
tests/                    pytest suite on synthetic data that mimics the real quirks
scripts/build_local.py    build gold tables locally (no Spark) for app development
scripts/verify_websites.py  crawl official hospital websites -> web_site_status / web_service_hits parquet
```

## Setup on Databricks Free Edition (about 15 minutes)

1. **Get the data.** Go to Marketplace, search for **"Virtue Foundation Dataset (DAIS 2026)"**, and click **Get instant access**. This adds a catalog to your workspace.
2. **Bring in this code.** Go to Workspace, then **Create → Git folder** and paste this repo URL. Or zip the folder and use **Import**.
3. **Run the pipeline** on serverless compute, in this order: `notebooks/01_bronze_ingest` → `02_silver_clean` → `02b_web_factcheck` → `03_gold_evidence`.
   - For `02b`, first upload `web_site_status.parquet` and `web_service_hits.parquet` (from `scripts/verify_websites.py`) to `/Volumes/workspace/findcare/raw/web/`. The notebook creates the volume if it doesn't exist. If you skip `02b`, everything still works, just without website confirmations.
   - Notebook 01 finds the facility, PIN and NFHS sources by their column signatures. If it can't, it prints `NOT FOUND`; paste the table name or the `/Volumes/...csv` path into that widget.
   - Output goes to `workspace.findcare` by default (change this with the `catalog` and `schema` widgets).
   - Check the coverage table printed in notebook 02. It should roughly match the brief: capability 99.7%, procedure 92.5%, and so on.
4. **Try it in a notebook.** Open `04_find_care`, set the widgets, and click Run all.
5. **Deploy the app:**
   1. Go to **Compute → Apps → Create app → Custom**, and name it `find-right-care`.
   2. Under **App resources**, add a **SQL warehouse** (the Serverless Starter Warehouse works) with resource key **`sql-warehouse`** and permission *Can use*.
   3. Deploy from the source path `/Workspace/.../<repo>/app`.
   4. Grant the app's service principal read access. You can find its ID on the app's *Authorization* tab:
      ```sql
      GRANT USE CATALOG ON CATALOG workspace TO `<app-service-principal-id>`;
      GRANT USE SCHEMA, SELECT ON SCHEMA workspace.findcare TO `<app-service-principal-id>`;
      ```
   5. If you changed the catalog or schema, also edit `FINDCARE_CATALOG` and `FINDCARE_SCHEMA` in `app/app.yaml`.
6. **Optional:** create a Genie space for planners. See [docs/GENIE.md](docs/GENIE.md).

## Local development

```bash
python -m venv .venv && .venv/bin/pip install -r app/requirements.txt pytest
.venv/bin/python -m pytest -q tests                      # 32 tests, synthetic data
.venv/bin/python scripts/build_local.py --synthetic      # or --facilities/--pincodes/--nfhs  <csv|parquet>
cd app && FINDCARE_LOCAL_DIR=../local_data ../.venv/bin/streamlit run app.py
```

## What was verified, and what wasn't

- **Run on the real Marketplace tables:** 10,088 facilities, 165,627 post offices and 706 NFHS districts, pulled through the SQL warehouse and run through the full pipeline locally.
  - Silver takes about 5 s and gold about 38 s.
  - Every facility column resolved automatically.
  - Benchmark queries return sensible, quoted answers, for example dialysis near Jaipur and C-section near Lucknow.
  - The Streamlit app was smoke-tested on the real gold tables. After the first load (about 1 s), each search takes about 0.1 s.
- **Fact-checked against hospitals' own websites:** we crawled 7,651 official domains. Own sites confirmed 20,164 claims. Using the record alone, claims rated Strong were confirmed 65% of the time, Moderate 47%, Limited 36% and Indirect 17%. Those are lower bounds, and they show the evidence levels carry real signal.
- **Review queue:** 1,469 facilities are marked *needs review* (contradictions or possible merged sites) and 3,229 sit in `gold_review_queue` for later labelling.
- **Tests:** 32 unit and integration tests pass for `app/` (CareProof's own suite: 156 pass; 36 need private report files that aren't in the repo). They include regression tests for every real-data quirk listed below.
- **Not yet run:** the notebooks inside Databricks itself, and the deployed app. The notebook code calls the same tested functions, but it hasn't executed on serverless compute yet.
- **Not yet measured:** precision. Spot-checks of random "Strong" and "Moderate" matches looked correct, but use notebook 05 to label a sample properly.

### Data problems found in the real tables (and how they're handled)

| Problem | Scale | Handling |
|---|---|---|
| Facility rows that are fragments of broken multi-line descriptions (text in the id column, values shifted) | 88 rows | moved to `quarantine_facilities`, leaving 10,000 facilities |
| Upstream AI-extraction leftovers in list fields ("Cancer?", "not specified", "explicitly stated in the text") | 808 items in 491 records | dropped from evidence and counted |
| Facility coordinates more than 40 km from the record's own PIN (median gap 184 km) | 1,297 flagged | the point closer to the record's city wins; 817 records use the PIN location instead |
| Merged records (for example one "Aravind Eye Hospital" with a Hyderabad address, a Delhi PIN, coordinates near Puducherry, and Delhi government-hospital services) | — | flags for state mismatch, multi-site text and service breadth, shown as cautions |
| PIN directory: lat/lon swapped | 791 rows | swapped back |
| PIN directory: same-name geocoding (229 Aurangabad, **Bihar** offices carry Aurangabad, **Maharashtra**'s coordinates) | 2,203 rows | rejected using a centre for each 3-digit PIN area |
| PIN directory: whole districts mis-placed (most Vadodara offices at latitude 15.6; most Pratapgarh, UP offices near Raipur) | 1,620 rows | rejected using a robust state-extent test |
| PIN directory: state and district given as the text `"NA"` | 715 rows | filled from other offices with the same PIN, or the state from the postal circle |
| PINs that span more than one district | 1,252 PINs | aggregated per PIN, with a warning |
| NFHS-5 columns already abbreviated (no human-readable names) | 109 columns | curated indicator list and labels for each service |
| District renamed or created after NFHS-5 (Nuh/Mewat, Ballari/Bellary, the new Andhra Pradesh districts) | 81 districts | 31 matched through a rename table; 50 are newer than the survey, so the app says "no data" instead of guessing |

## Data and licences

- Virtue Foundation facility dataset, via Databricks Marketplace.
- India Post PIN Code Directory and NFHS-5 district fact sheets, from data.gov.in under the [Government Open Data License – India](https://www.data.gov.in/Godl).
