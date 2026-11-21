# Submission checklist and repository security audit

- [x] Architecture, three-minute script, executive summary, evaluation, EDA and figures prepared.
- [x] Local regression and decision-trace lineage checks passed. Canonical ranking, resolver and eligibility thresholds remain intact.
- [x] Existing Stage 12 gate archived. No changes to workspace data, permissions or the live app were made during this pass.
- [ ] Independently replay the live warehouse checks and earlier gates from the authenticated workspace.
- [ ] After approval, deploy the revised private snapshot and open it in an authenticated browser. Record exact output, claim IDs, trace consistency and end-to-end outcome for the three demo queries in the final JSON.
- [ ] Human reviewer signs off on uncertainty and clinical language. Run the isolated Stage 13 acceptance command and archive the actual output.

## Security audit

The app manifest contains a warehouse resource key. SDK unified authentication uses the injected service principal in the workspace and local CLI OAuth for operator validation. The app source uses bound values for SQL inputs and reads from fixed tables. Citation text and link attributes are escaped. The link filter accepts HTTP and HTTPS URLs with no embedded user credentials. Streamlit retains its default CORS and XSRF protections.

A filename and pattern review scanned the repository and deployment source for common Databricks token, JWT, private-key and literal password or OAuth credential patterns. It found no matches. Pattern scanning cannot certify arbitrary secret formats. This checkout has no Git metadata, so repository history could not be inspected. The staged bundle excludes local config, caches, Databricks sync state, raw record exports and logs. Keep the app private and use the existing read-only warehouse and Unity Catalog grants.

## Run after explicit redeployment approval

Use your authenticated normal Terminal from the repository root. Upload only the app directory and keep the resource binding and ACLs as configured.

```sh
careproof_user="$(databricks current-user me --profile careproof --output json |
  .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["userName"])')"
careproof_source="/Workspace/Users/$careproof_user/careproof-agent-app"
careproof_stage="$(mktemp -d)"
rsync -a --exclude='.databricks/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='.DS_Store' app/ "$careproof_stage/"
databricks workspace import-dir "$careproof_stage" "$careproof_source" --overwrite --profile careproof &&
databricks apps deploy careproof-demo --source-code-path "$careproof_source" --mode SNAPSHOT --profile careproof &&
databricks apps get careproof-demo --profile careproof --output json &&
.venv/bin/python -m careproof_agent.runner gate 12
```

Inspect the returned private URL and confirm `RUNNING` plus a `SUCCEEDED` active deployment. Run Dialysis / Jaipur, Dialysis / Nagercoil, and Cardiac surgery / Jaipur. Confirm exact excerpts and source IDs, review a conflict, and check that trace totals and reasons agree with the result page. Update the pending browser observations in the final report with actual outcomes.

Run the live Gold/Bronze read-only check and rerun gates 00–12 in the authorized workspace. Stop to investigate any failure.

```sh
DATABRICKS_CONFIG_PROFILE=careproof DATABRICKS_AUTH_TYPE=databricks-cli \
DATABRICKS_WAREHOUSE_ID=3c53fead4663137d .venv/bin/python -m tests.stage12_live
for stage in 00 01 02 03 04 05 06 07 08 09 10 11 12; do
  .venv/bin/python -m careproof_agent.runner gate "$stage" || break
done
```

The trusted Stage 13 gate checks report fields, at least three demo entries, explicit final approval, and the live Stage 12 app status. Its minimum checks do not perform browser searches. Complete those before executing the signoff command.

```sh
.venv/bin/python -m careproof_agent.runner gate 13 --approve-final
```
