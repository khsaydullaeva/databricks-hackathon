# CareProof · CMU Datathon Fall 2026

CareProof helps community health workers find facility leads by service and place, inspect the exact recorded claim, compare alternatives, and see when the dataset offers too little evidence to rank a facility. The app supports dialysis, MRI, NICU, emergency C-section, cardiac surgery, and trauma/emergency care. Confirm service, branch and intake directly with the facility.

**Status.** The archived Stage 12 independent gate passed at 2026-11-21 21:26:39 UTC for private Databricks App `careproof-demo`. A reported live Dialysis / Jaipur search returned two ranked facilities and 11 review leads. The Stage 13 trace is a local revision. Final live validation and human signoff are pending. [Acceptance record](reports/13_final.json)

## Judge materials

- [Executive summary](docs/executive_summary.md) and [three-minute demonstration](docs/demo.md)
- [Architecture and data flow](docs/architecture.md)
- [EDA with figures](reports/stage13_eda.md), [evaluation](reports/stage13_evaluation.md), and [reproducible metrics](reports/stage13_metrics.json)
- [Submission checklist and security audit](docs/submission_checklist.md)
- [App runbook](app/README.md) and [Stage 12 machine evidence](reports/gate_12.json)

## Reproduce locally

Use Python 3.11, the Databricks CLI, and access to the private workspace. The app uses Databricks unified authentication. Keep OAuth credentials in the CLI-managed profile.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -r app/requirements.txt
[ -f config.json ] || cp config.example.json config.json
databricks auth login --host https://dbc-9ca739b5-234d.cloud.databricks.com --profile careproof
python -m pytest -q
```

The authoritative source is `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities`. The target is `workspace.careproof`. The working SQL warehouse is `3c53fead4663137d`. For the trusted runner, set only the `warehouse_id` entry in your private `config.json` if warehouse auto-discovery cannot select it. To recreate the tables in an authorized workspace, inspect the SQL and use the trusted `apply` and `gate` commands in order for stages 00 through 09. Stage 01 preserves an existing Bronze snapshot.

```sh
# Execute only when reproducing tables in the approved target schema.
for stage in 00 01 02 03 04 05 06 07 08 09; do
  python -m careproof_agent.runner apply "$stage" &&
  python -m careproof_agent.runner gate "$stage" || break
done
```

To run the app locally against live Gold:

```sh
(cd app && DATABRICKS_CONFIG_PROFILE=careproof DATABRICKS_AUTH_TYPE=databricks-cli \
  DATABRICKS_WAREHOUSE_ID=3c53fead4663137d streamlit run app.py)
```

City, district and recorded PIN match administrative fields. PIN geocoding is unavailable. Coordinate searches use straight-line distance and coordinate quality flags. Ranking points describe dataset evidence and location. They carry no clinical interpretation. Source URLs remain unverified leads.

## Reproduce the audit

On an authorized connection, run `python -m tests.stage12_live` with `DATABRICKS_CONFIG_PROFILE=careproof`, `DATABRICKS_AUTH_TYPE=databricks-cli` and `DATABRICKS_WAREHOUSE_ID=3c53fead4663137d`. It queries Gold through the app adapter and reconciles ranked claims against Bronze. The operator needs Bronze read access. The app needs read access to Gold and supporting claims.

The private test checkout uses `reports/real_data_capture.json` for exact-claim and UI regression. The distribution archive excludes the raw export and detailed label/audit records. Obtain the approved private artifacts to replay the published run exactly, or recapture in the original workspace with `python -m careproof.audit --output reports/real_data_capture.json` for current data. The four inputs and their hashes are recorded in [stage13_metrics.json](reports/stage13_metrics.json). To regenerate the figure, install Matplotlib in an analysis environment and run `python -m careproof.report13`. Reports explain which results are synthetic, captured from the dataset or observed in the running app.

## Stage 13 acceptance

After authorized redeployment of the revision, record three authenticated browser cases and review the uncertainty copy. Repeat the live read-only checks and the trusted prior stage gates on the current artifacts. The Stage 00 gate probes write access inside the approved schema. The [acceptance runbook](docs/submission_checklist.md) lists the exact isolated deployment and gate commands. Final review uses `python -m careproof_agent.runner gate 13 --approve-final` after explicit human signoff.
