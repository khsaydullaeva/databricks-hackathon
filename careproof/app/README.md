# Run and deploy CareProof

## Local run

From the project root, install the app dependencies in a Python environment and authenticate with the workspace CLI profile.

```sh
python -m pip install -r app/requirements.txt
databricks auth login --host https://dbc-9ca739b5-234d.cloud.databricks.com --profile careproof
cd app
DATABRICKS_CONFIG_PROFILE=careproof DATABRICKS_WAREHOUSE_ID=3c53fead4663137d streamlit run app.py
```

Try Dialysis in Jaipur, Rajasthan. Compare the top result against another facility and open its recorded source evidence. Use coordinates for straight-line distance. PIN searches match recorded text and have no postal directory lookup.

From the project root, run regression tests and the read-only live acceptance checks:

```sh
python -m pytest -q
DATABRICKS_CONFIG_PROFILE=careproof DATABRICKS_WAREHOUSE_ID=3c53fead4663137d python -m tests.stage12_live
```

The live script requires the operator to be able to read Bronze to reconcile each claim. Bronze access is unnecessary for the app itself.

## Workspace setup after explicit approval

In Databricks Apps, configure the private app `careproof-demo` with these resources. The app runs under its own service principal, using Databricks SDK unified authentication.

| Type | Resource key | Target | Minimum grant |
| --- | --- | --- | --- |
| SQL warehouse | `sql-warehouse` | `3c53fead4663137d` | Can use |
| Unity Catalog table | `gold-index` | `workspace.careproof.gold_service_index` | SELECT |
| Unity Catalog table | `claim-evidence` | `workspace.careproof.verified_claims` | SELECT |

Check that the same principal has `USE CATALOG` on `workspace` and `USE SCHEMA` on `workspace.careproof`. Verify the app resource list and the effective privileges before deploying. The trusted runner imports the contents of `app/`; its app creation step does not set these resources, so configure the app before using that deployment step. See [SQL warehouse resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/sql-warehouse) and [Unity Catalog table resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/tables).

The source bundle contains byte-for-byte copies of `careproof/recommend.py` and `careproof/location.py` because the runner uploads only `app/`. Refresh them after a backend change and run the parity test.

```sh
cp careproof/recommend.py careproof/location.py app/careproof/
```

After configuration and explicit `--allow-deploy` approval, the trusted runner owns Stage 12 deployment and independent gate verification. Use the running app as an authorized user to repeat strong-evidence, conflict and abstention searches. Open claim evidence to exercise the service principal's access to both tables, and check `RUNNING` plus a `SUCCEEDED` active deployment in Databricks.

## Exact Terminal sequence for the private demo

Run from the repository root in your normal Terminal. Continue only after each check succeeds. Databricks supplies the Streamlit host and port. The app keeps Streamlit's default CORS and XSRF protections enabled. [Databricks Apps runtime environment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env)

```sh
. .venv/bin/activate
python -m pip install -r app/requirements.txt
python -m pip check
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
databricks auth login --host https://dbc-9ca739b5-234d.cloud.databricks.com --profile careproof
DATABRICKS_CONFIG_PROFILE=careproof DATABRICKS_AUTH_TYPE=databricks-cli DATABRICKS_WAREHOUSE_ID=3c53fead4663137d python -m tests.stage12_live
databricks apps get careproof-demo --profile careproof --output json
```

If the final command returns `RESOURCE_DOES_NOT_EXIST` after successful authentication, create the private app with `databricks apps create careproof-demo --profile careproof`. In the Databricks Apps UI, assign the three resources from the table above. Use **Can use** for the warehouse and **Select** for both tables. Check the app's Permissions view for broad access before deploying. If access is broader than intended, stop and review the existing permissions with the workspace owner.

Read the app's service principal client ID from `apps get`, then inspect its warehouse and Unity Catalog grants. The `--principal` argument is the service principal application ID. Check the warehouse entry for that principal, the parent usage grants, and effective `SELECT` on both tables. If grants are missing or broader than expected, resolve that before uploading.

```sh
careproof_principal="$(databricks apps get careproof-demo --profile careproof --output json | python -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')"
databricks apps get-permissions careproof-demo --profile careproof --output json
databricks warehouses get-permissions 3c53fead4663137d --profile careproof --output json
databricks grants get-effective catalog workspace --principal "$careproof_principal" --profile careproof --output json
databricks grants get-effective schema workspace.careproof --principal "$careproof_principal" --profile careproof --output json
databricks grants get-effective table workspace.careproof.gold_service_index --principal "$careproof_principal" --profile careproof --output json
databricks grants get-effective table workspace.careproof.verified_claims --principal "$careproof_principal" --profile careproof --output json
```

After these checks, upload and deploy the tested source to `careproof-demo`. This uses the same Workspace snapshot deployment as the trusted runner and avoids invoking another coding-agent run.

```sh
careproof_user="$(databricks current-user me --profile careproof --output json | python -c 'import json,sys; print(json.load(sys.stdin)["userName"])')"
careproof_source="/Workspace/Users/$careproof_user/careproof-agent-app"
careproof_bundle="$(mktemp -d)"
rsync -a --exclude='__pycache__/' --exclude='*.pyc' --exclude='.DS_Store' app/ "$careproof_bundle/"
databricks workspace import-dir "$careproof_bundle" "$careproof_source" --overwrite --profile careproof
databricks apps deploy careproof-demo --source-code-path "$careproof_source" --mode SNAPSHOT --profile careproof
databricks apps get careproof-demo --profile careproof --output json
python -m careproof_agent.runner gate 12
```

Open the URL returned by `apps get` in your authenticated browser. Confirm that the active deployment is `SUCCEEDED`, the app is `RUNNING`, and a search form submission and follow-up evidence selection work with browser protections enabled. Run Dialysis in Jaipur, MRI in Jaipur, Dialysis in Lucknow, and Cardiac surgery in Jaipur for the abstention case. Compare ranked citations with the live Bronze checks. Open an evidence detail to exercise the separate `verified_claims` grant, confirm the uncertainty labels and lack of city-derived distances, and submit invalid coordinates to check input error handling. If login succeeds but the interface fails to load or submit, inspect `databricks apps logs careproof-demo --profile careproof` and the browser network console while retaining default protections. Record actual browser outcomes in the Stage 12 report.
