# Find the Right Care: CMU Datathon, Fall 2026 (Virtue Foundation × Databricks)

You give it a **service** (dialysis, emergency C-section, NICU, cardiac surgery, MRI, burn care, and 14 more) and a **location** (city, district, 6-digit PIN code, or `lat, lon`). It returns the best facility options, and for each one it shows:

- **why**: the exact sentence from the facility record that supports the claim, which part of the record it came from, and how strong that evidence is;
- **how**: the score arithmetic (evidence, closeness, record quality, size), what decided the order, and what we traded off;
- **cautions**: contradictions in the record, approximate locations, possible merged records, claims we ignored, and the reminder to always call ahead.

The full scoring rules are in **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)**.

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
  02_silver_clean.py      cleaning + data-quality profile
  03_gold_evidence.py     trust flags, evidence per facility x service, lookups, coverage
  04_find_care.py         interactive notebook version of the app (widgets)
  05_evaluation.py        spot-checks, negation audit, benchmark queries, weight sensitivity, planner view
docs/METHODOLOGY.md       WHY / HOW, scoring tables, trade-offs, limitations
docs/GENIE.md             optional Genie space for planners
tests/                    pytest suite on synthetic data that mimics the real quirks
scripts/build_local.py    build gold tables locally (no Spark) for app development
```

## Setup on Databricks Free Edition (about 15 minutes)

1. **Get the data.** Go to Marketplace, search for **"Virtue Foundation Dataset (DAIS 2026)"**, and click **Get instant access**. This adds a catalog to your workspace.
2. **Bring in this code.** Go to Workspace, then **Create → Git folder** and paste this repo URL. Or zip the folder and use **Import**.
3. **Run the pipeline** on serverless compute, in this order: `notebooks/01_bronze_ingest` → `02_silver_clean` → `03_gold_evidence`.
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
.venv/bin/python -m pytest -q tests                      # 15 tests, synthetic data
.venv/bin/python scripts/build_local.py --synthetic      # or --facilities f.csv --pincodes p.csv --nfhs n.csv
cd app && FINDCARE_LOCAL_DIR=../local_data ../.venv/bin/streamlit run app.py
```

## What was verified, and what wasn't

- **Verified locally:** all 15 unit and integration tests pass on synthetic data that copies the real quirks: JSON-string lists, `NA` and `0,0` coordinates, PINs spanning several districts, `*` and `(29.5)` NFHS values, negated mentions, duplicate records, and coordinates that contradict the postcode. The Streamlit app was smoke-tested headlessly (normal search, ambiguous place, PIN, coordinates, unknown place, no results). A full-scale synthetic run (10k facilities, 165k post offices) builds gold in about 11 s, and each query takes about 10 ms.
- **Not yet verified:** running against the *real* Marketplace tables. The facility column names are resolved automatically (`findcare/columns.py`); check the printout in notebook 01. Precision figures should come from notebook 05's spot-check on the real data.

## Data and licences

- Virtue Foundation facility dataset, via Databricks Marketplace.
- India Post PIN Code Directory and NFHS-5 district fact sheets, from data.gov.in under the [Government Open Data License – India](https://www.data.gov.in/Godl).
