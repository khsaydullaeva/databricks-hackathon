"""A resumable Codex → Databricks → independent quality-gate workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


from .db import DatabricksSQL, ROOT, load_config
from .gates import Gate

PROTECTED = ['careproof_agent/runner.py','careproof_agent/gates.py','spec/stages.json']
SRC = 'databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities'


def stages() -> list[dict]:
    return json.loads((ROOT / 'spec' / 'stages.json').read_text())['stages']


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_hashes() -> dict:
    return {path: sha(ROOT / path) for path in PROTECTED}


def ensure_protected(before: dict):
    changed = [p for p in PROTECTED if sha(ROOT / p) != before[p]]
    if changed:
        raise RuntimeError('Agent modified immutable gate/spec files: ' + ', '.join(changed))


def strip_leading_comments(sql: str) -> str:
    """Drop SQL-only comment lines; preserved quote content is not inspected."""
    return '\n'.join(line for line in sql.splitlines()
                     if not line.lstrip().startswith('--')).strip()


def split_sql(script: str) -> list[str]:
    """Split stage SQL on semicolons outside SQL quoted literals.

    This intentionally supports the restricted, simple SQL files used here;
    use a real SQL parser or Databricks jobs for complex SQL scripting.
    """
    script=strip_leading_comments(script)
    out=[]; cur=[]; quote=None; i=0
    while i<len(script):
        ch=script[i]
        if quote:
            cur.append(ch)
            if ch==quote:
                if i+1<len(script) and script[i+1]==quote:
                    cur.append(ch); i+=1
                else:
                    quote=None
        elif ch in ("'",'"','`'):
            quote=ch; cur.append(ch)
        elif ch==';':
            text=''.join(cur).strip()
            if text: out.append(text)
            cur=[]
        else:
            cur.append(ch)
        i+=1
    if quote:
        raise RuntimeError('Unclosed SQL quote in stage file')
    text=''.join(cur).strip()
    if text: out.append(text)
    return out


def guard_statement(statement: str, cfg: dict):
    """Reject accidental writes outside the configured workspace schema.

    This is a belt-and-suspenders guard. Use Unity Catalog grants and review
    generated SQL as your actual security boundary.
    """
    clean = strip_leading_comments(statement).strip().rstrip(';')
    normalized = re.sub(r'[`"]', '', clean)
    target = cfg['target_schema'].lower()
    if not clean:
        return
    if re.match(r'^\s*(?:DROP|ALTER)\s+(?:CATALOG|SCHEMA)\b', normalized, re.IGNORECASE):
        raise RuntimeError('Destructive catalog/schema operations are prohibited')
    prefix = r'^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|SCHEMA)|DROP\s+(?:TABLE|VIEW|SCHEMA)|ALTER\s+(?:TABLE|VIEW|SCHEMA)|INSERT\s+INTO|MERGE\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE\s+TABLE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([\w.]+)'
    match = re.match(prefix, normalized, flags=re.IGNORECASE | re.DOTALL)
    if match:
        obj = match.group(1).lower()
        if obj != target and not obj.startswith(target+'.'):
            raise RuntimeError('Blocked write outside '+target+': '+obj)
    elif re.match(r'^\s*(CREATE|DROP|ALTER|INSERT|MERGE|UPDATE|DELETE|TRUNCATE|REPLACE|COPY|GRANT|REVOKE)\b',clean, flags=re.IGNORECASE):
        raise RuntimeError('Unrecognized write target, refusing SQL: '+clean[:120])
    if re.search(r'(?i)\b(?:DROP|ALTER|TRUNCATE|INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO)\s+'+re.escape(SRC),normalized):
        raise RuntimeError('Blocked potential mutation of read-only Marketplace source')


def apply_sql(stage: str, db: DatabricksSQL):
    sql_files = sorted((ROOT / 'sql').glob(f'{stage}_*.sql'))
    if int(stage) < 10 and not sql_files:
        raise RuntimeError(f'No sql/{stage}_*.sql files found; stage implementation incomplete')
    cfg = load_config()
    for path in sql_files:
        stmts = split_sql(path.read_text())
        for statement in stmts:
            guard_statement(statement,cfg)
            print(f'   SQL {path.name}: {statement.strip()[:105]} ...', flush=True)
            db.execute(statement)
    if stage == '02':
        from .profile import build_profile
        build_profile(db, cfg)


def preflight():
    print('Local dependencies:')
    for command in ('codex','databricks'):
        value = shutil.which(command)
        print('  ',command,'=>',value or 'NOT FOUND')
    cfg = load_config()
    print('Profile:',cfg['profile'])
    print('Read-only source:',cfg['source_table'])
    print('Writable target:',cfg['target_schema'])
    db = DatabricksSQL(cfg)
    print('SQL warehouse:',db.warehouse_id)
    print('Source count:',db.number(f'SELECT COUNT(*) FROM {SRC}'))
    return True


def save_state(stage: str, result: dict):
    path = ROOT / '.agent_state.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    state[stage] = {'passed':result['passed'],'checked_at_utc':result['checked_at_utc']}
    path.write_text(json.dumps(state,indent=2))


def prompt_for(stage: dict, failures: str) -> str:
    criteria='\n'.join(f'- {s}' for s in stage['acceptance_criteria'])
    return f'''You are building the CareProof CMU Datathon product. Read AGENTS.md, spec/stages.json,
    spec/facilities_schema.json and previous reports. Implement ONLY stage {stage['id']}.

    Stage: {stage['name']}
    Dependencies: {', '.join(stage['depends_on']) or 'none'}
    Deliverable location: {stage['implementation_target']}
    Acceptance criteria:
    {criteria}

    IMPORTANT: source table is {SRC} (read-only).
    Writable target schema: {load_config()['target_schema']}.
    For stages 00-09, write executable, IDEMPOTENT SQL in sql/{stage['id']}_*.sql.
    Do not edit immutable gate/spec/runner files, don't fabricate source excerpts,
    don't overwrite Marketplace data and don't mark unverified clinical claims as proven.
    The OUTER runner will execute SQL with OAuth credentials after this Codex run and
    will independently validate SQL results. Your sandbox may block network; do not
    bypass security or fabricate a passing report to compensate. Write Python and
    automated tests as appropriate for later stages. Stage 02 report must be created
    from measured data, not invented. Deploying stage 12 requires user authorization.

    Last execution/gate errors: {failures or 'None yet; first implementation.'}

    Make concrete code changes now and finish with a compact change summary.'''


def deploy_app(cfg: dict):
    """Deploy app only following an explicit --allow-deploy user CLI action."""
    app_dir=ROOT/'app'
    for rel in ('app.py','app.yaml','requirements.txt'):
        if not (app_dir/rel).is_file():
            raise RuntimeError(f'App deployment is blocked: missing app/{rel}')
    profile=cfg['profile']
    def cli(*args,check=True):
        proc=subprocess.run(['databricks',*args,'--profile',profile,'--output','json'],
                            cwd=ROOT,capture_output=True,text=True,timeout=1800)
        if check and proc.returncode != 0:
            raise RuntimeError('Databricks CLI failed ('+' '.join(args[:3])+'): '+proc.stderr[-1500:])
        return proc
    me=json.loads(cli('current-user','me').stdout)
    username=me.get('userName') or me.get('user_name')
    if not username or '/' in username:
        raise RuntimeError('Could not safely locate the current Databricks workspace user')
    target='/Workspace/Users/'+username+'/careproof-agent-app'
    cli('workspace','import-dir',str(app_dir),target,'--overwrite')
    name=cfg.get('app_name','careproof-demo')
    exists=cli('apps','get',name,check=False)
    if exists.returncode:
        cli('apps','create',name)
    cli('apps','deploy',name,'--source-code-path',target,'--mode','SNAPSHOT')
    print('App deployment requested and CLI returned success. Checking live app gate next.')


def run_stages(first: str, last: str, *, allow_deploy: bool = False, approve_final: bool = False):
    cfg = load_config()
    sequence = stages()
    ids = [s['id'] for s in sequence]
    if first not in ids or last not in ids or ids.index(first)>ids.index(last):
        raise ValueError('Invalid stage range')
    if not shutil.which('codex'):
        raise RuntimeError('Codex CLI missing. Run npm install -g @openai/codex@latest')
    db = DatabricksSQL(cfg)
    for i in range(ids.index(first),ids.index(last)+1):
        st = sequence[i]
        stage = st['id']
        print(f'\n========== Stage {stage}: {st["name"]} ==========',flush=True)
        # Check prerequisites against *fresh* live gates, not stale JSON status.
        for dependency in st['depends_on']:
            dep_gate = Gate(dependency,db).run()
            if not dep_gate['passed']:
                raise RuntimeError(f'Stage {stage} blocked: prerequisite {dependency} gate failed. See reports/gate_{dependency}.json')
        # Fresh check can skip current stage only if it truly passes now.
        initial = Gate(stage,db,final_approval=approve_final if stage == '13' else False).run()
        if initial['passed']:
            print(f'Already passes independent gate: {stage}',flush=True)
            save_state(stage,initial)
            continue
        failures = json.dumps(initial['checks'],indent=2)
        for attempt in range(1, int(cfg.get('max_codex_attempts',3))+1):
            print(f'Agent attempt {attempt}: stage {stage}',flush=True)
            before = protected_hashes()
            command = ['codex','exec','--sandbox','workspace-write','--skip-git-repo-check',prompt_for(st, failures)]
            try:
                proc = subprocess.run(command,cwd=ROOT,capture_output=True,text=True,
                                      timeout=int(cfg.get('codex_timeout_seconds',1500)))
                output = (proc.stdout or '')+'\n'+(proc.stderr or '')
            except subprocess.TimeoutExpired as e:
                proc = None
                output = f'Codex invocation timed out: {e}'
            (ROOT/'logs'/f'codex_{stage}_attempt{attempt}.log').write_text(output)
            ensure_protected(before)
            if proc is not None and proc.returncode != 0:
                failures = f'Codex process failed with code {proc.returncode}: {output[-1800:]}'
                print(failures,flush=True)
                continue
            try:
                apply_sql(stage,db)
                if stage == '12' and allow_deploy:
                    deploy_app(cfg)
                outcome = Gate(stage,db,final_approval=approve_final if stage == '13' else False).run()
                if outcome['passed']:
                    save_state(stage,outcome)
                    print(f'PASS stage {stage} — report: reports/gate_{stage}.json',flush=True)
                    break
                failures = json.dumps(outcome['checks'],indent=2)
            except Exception as e:
                failures = f'{type(e).__name__}: {e}'
            print(f'FAIL stage {stage}: {failures[:1000]}',flush=True)
        else:
            raise RuntimeError(f'Stage {stage} failed after {cfg.get("max_codex_attempts",3)} attempts. Stopping; check logs and gate report.')
    print('\nCompleted requested stage range. Review actual reports and app deployment before submission.')


def main():
    parser=argparse.ArgumentParser(description='CareProof staged AI engineering runner')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('preflight')
    sub.add_parser('status')
    gate=sub.add_parser('gate'); gate.add_argument('stage'); gate.add_argument('--approve-final',action='store_true')
    apply=sub.add_parser('apply'); apply.add_argument('stage')
    run=sub.add_parser('run'); run.add_argument('--from-stage',default='00'); run.add_argument('--until-stage',default='13'); run.add_argument('--allow-deploy',action='store_true',help='Authorize app creation and deployment to your Databricks workspace'); run.add_argument('--approve-final',action='store_true',help='Explicit signoff after personally reviewing the running app and risk text')
    args=parser.parse_args()
    if args.action=='preflight':
        preflight()
    elif args.action=='status':
        state_path=ROOT/'.agent_state.json'
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        for s in stages():
            result=state.get(s['id'],{})
            print(s['id'],s['name'],result.get('passed','NOT VERIFIED'),result.get('checked_at_utc',''))
    elif args.action=='gate':
        stage=args.stage.zfill(2)
        outcome=Gate(stage,final_approval=args.approve_final).run()
        print(json.dumps(outcome,indent=2))
        raise SystemExit(0 if outcome['passed'] else 1)
    elif args.action=='apply':
        apply_sql(args.stage.zfill(2),DatabricksSQL())
    elif args.action=='run':
        run_stages(args.from_stage.zfill(2),args.until_stage.zfill(2),allow_deploy=args.allow_deploy,approve_final=args.approve_final)


if __name__=='__main__':
    main()
