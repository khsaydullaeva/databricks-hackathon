"""Read-only acceptance tests for CareProof, plus a controlled schema write probe.

Do not modify gates to claim a pass. Reports are generated from actual Databricks
queries and local tests. Some requirements require manual review; machine gates alone
cannot prove clinical correctness.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import DatabricksSQL, ROOT, load_config


class GateFailure(Exception):
    pass


class Gate:
    def __init__(self, stage: str, db=None, *, final_approval: bool = False):
        self.stage = stage
        self.final_approval = final_approval
        self.cfg = load_config()
        self.db = db or DatabricksSQL(self.cfg)
        self.src = self.cfg['source_table']
        self.tgt = self.cfg['target_schema']
        self.checks = []

    def check(self, name: str, fn):
        try:
            detail = fn()
            self.checks.append({'name': name, 'pass': True, 'detail': str(detail)[:200]})
        except Exception as e:
            self.checks.append({'name': name, 'pass': False, 'detail': f'{type(e).__name__}: {e}'[:600]})

    def must(self, condition: bool, desc: str):
        if not condition:
            raise GateFailure(desc)
        return desc

    def require_columns(self, table: str, names: set[str]):
        cols = self.db.cols(table)
        missing = {n.lower() for n in names} - set(cols)
        self.must(not missing, f'columns absent in {table}: {sorted(missing)}')
        return f'{len(cols)} columns, all required columns present'

    def has_table(self, table: str):
        n = self.db.number(f'SELECT COUNT(*) FROM {table}')
        self.must(n > 0, f'{table} unexpectedly empty')
        return f'{n} rows'

    def count_eq(self, first: str, second: str):
        a, b = self.db.number(f'SELECT COUNT(*) FROM {first}'), self.db.number(f'SELECT COUNT(*) FROM {second}')
        return self.must(a == b, f'counts differ: {a} vs {b}')

    def no_rows(self, sql: str):
        n = self.db.number(f'SELECT COUNT(*) FROM ({sql}) __gate_rows')
        return self.must(n == 0, f'{n} invalid rows found')

    def file_fields(self, path: str, keys: set[str]):
        p = ROOT / path
        self.must(p.is_file(), f'file missing: {path}')
        data = json.loads(p.read_text())
        self.must(keys <= set(data), f'missing report fields: {sorted(keys-set(data))}')
        return data

    def stage_00(self):
        self.check('read_source', lambda: self.must(self.db.number(f'SELECT COUNT(*) FROM {self.src}') > 9000, 'source table unreadable or too small'))
        def schema_test():
            probe = f'{self.tgt}.__careproof_gate_probe'
            # isolated create/drop is intentional only inside the target schema
            self.db.execute(f'CREATE TABLE IF NOT EXISTS {probe} (probe_id INT)')
            self.db.execute(f'DROP TABLE {probe}')
            return 'Writable target schema verified'
        self.check('writable_schema', schema_test)

    def stage_01(self):
        bronze = f'{self.tgt}.bronze_facilities'
        self.check('row_count', lambda: self.count_eq(self.src, bronze))
        self.check('source_schema', lambda: self.must(len(self.db.cols(bronze)) == 51, 'bronze does not have 51 columns'))
        self.check('no_loss_multiset', lambda: self.no_rows(f'SELECT * FROM {self.src} EXCEPT ALL SELECT * FROM {bronze}'))
        self.check('no_extra_multiset', lambda: self.no_rows(f'SELECT * FROM {bronze} EXCEPT ALL SELECT * FROM {self.src}'))

    def stage_02(self):
        bronze = f'{self.tgt}.bronze_facilities'
        def profiling():
            r = self.file_fields('reports/02_profile.json', {'total_rows', 'null_counts', 'json_parse_failures', 'geography', 'duplicate_ids'})
            self.must(r['total_rows'] == self.db.number(f'SELECT COUNT(*) FROM {bronze}'), 'profile count is stale')
            self.must(len(r['null_counts']) >= 20, 'null profiling must cover at least 20 columns')
            self.must('missing_coordinates' in r['geography'], 'missing geo audit data')
            return 'Profile report matches live bronze row count'
        self.check('profile_report', profiling)

    def stage_03(self):
        bronze, silver = f'{self.tgt}.bronze_facilities', f'{self.tgt}.silver_facilities'
        self.check('preserved_count', lambda: self.count_eq(bronze, silver))
        self.check('required_clean_fields', lambda: self.require_columns(silver, {'unique_id','name','name_clean','city_clean','geo_status','capability_array','procedure_array','specialties_array','equipment_array','source_url_array'}))
        self.check('preserved_ids', lambda: self.no_rows(f'SELECT unique_id FROM {bronze} EXCEPT ALL SELECT unique_id FROM {silver}'))
        self.check('coordinate_flags', lambda: self.no_rows(f"SELECT 1 FROM {silver} WHERE (latitude IS NULL OR longitude IS NULL) AND geo_status <> 'missing_coordinates' OR (latitude IS NOT NULL AND longitude IS NOT NULL AND (latitude NOT BETWEEN 6 AND 38 OR longitude NOT BETWEEN 68 AND 98)) AND geo_status <> 'outside_india_bbox'"))

    def stage_04(self):
        table, silver = f'{self.tgt}.identity_review', f'{self.tgt}.silver_facilities'
        self.check('all_facilities_reviewed', lambda: self.count_eq(table, silver))
        self.check('required_identity_fields', lambda: self.require_columns(table, {'facility_id','identity_status','reason'}))
        self.check('non_empty_status', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE identity_status IS NULL OR trim(identity_status) = ''"))
        self.check('reason_on_flag', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE lower(identity_status) NOT IN ('clear','ok') AND (reason IS NULL OR trim(reason)='')"))

    def stage_05(self):
        table, silver = f'{self.tgt}.geo_facilities', f'{self.tgt}.silver_facilities'
        self.check('preserved_facilities', lambda: self.count_eq(table, silver))
        self.check('geo_fields', lambda: self.require_columns(table, {'facility_id','latitude','longitude','geo_status'}))
        self.check('one_geo_per_facility', lambda: self.no_rows(f'SELECT facility_id FROM {table} GROUP BY facility_id HAVING COUNT(*)>1'))
        self.check('no_invalid_confirmed', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE geo_status='confirmed' AND (latitude IS NULL OR longitude IS NULL OR latitude NOT BETWEEN 6 AND 38 OR longitude NOT BETWEEN 68 AND 98)"))

    def stage_06(self):
        table = f'{self.tgt}.service_taxonomy'
        self.check('taxonomy_fields', lambda: self.require_columns(table, {'service_key','alias'}))
        self.check('six_services', lambda: self.must(self.db.number(f'SELECT COUNT(DISTINCT service_key) FROM {table}') >= 6, 'need at least six canonical services'))
        self.check('no_empty_aliases', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE service_key IS NULL OR alias IS NULL OR trim(alias)=''"))

    def stage_07(self):
        table, bronze = f'{self.tgt}.service_claims', f'{self.tgt}.bronze_facilities'
        self.check('claims_exist', lambda: self.has_table(table))
        self.check('claims_fields', lambda: self.require_columns(table, {'facility_id','service_key','claim_text','source_field'}))
        self.check('no_empty_claim_text', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE claim_text IS NULL OR trim(claim_text)=''"))
        self.check('no_orphan_facility', lambda: self.no_rows(f'SELECT c.facility_id FROM {table} c LEFT ANTI JOIN {bronze} b ON c.facility_id=b.unique_id'))
        def exact_excerpt_check():
            # Original source field names must be explicitly mapped; no invented excerpts.
            n = self.db.number(f"""
                SELECT COUNT(*) FROM {table} c JOIN {bronze} b ON c.facility_id=b.unique_id
                WHERE c.source_field NOT IN ('description','capability','procedure','specialties','equipment')
                   OR INSTR(LOWER(COALESCE(CASE c.source_field
                        WHEN 'description' THEN b.description WHEN 'capability' THEN b.capability
                        WHEN 'procedure' THEN b.procedure WHEN 'specialties' THEN b.specialties
                        WHEN 'equipment' THEN b.equipment END,'')), LOWER(c.claim_text)) = 0
            """)
            return self.must(n == 0, f'{n} claims are not exact substrings of original source field')
        self.check('no_fabricated_excerpts', exact_excerpt_check)

    def stage_08(self):
        table = f'{self.tgt}.verified_claims'
        self.check('verified_claim_fields', lambda: self.require_columns(table, {'facility_id','service_key','claim_text','evidence_status','verification_reason'}))
        self.check('claims_are_assessed', lambda: self.has_table(table))
        self.check('uncertainty_required', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE evidence_status NOT IN ('supported_claim','uncertain','conflicting','rejected') OR verification_reason IS NULL OR trim(verification_reason)=''"))
        self.check('no_unverified_live_availability', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE lower(evidence_status) LIKE '%available%' OR lower(evidence_status) LIKE '%clinically_verified%'"))

    def stage_09(self):
        table = f'{self.tgt}.gold_service_index'
        self.check('gold_index_fields', lambda: self.require_columns(table, {'facility_id','service_key','evidence_status','geo_status','claim_text'}))
        self.check('gold_index_nonempty', lambda: self.has_table(table))
        self.check('one_facility_service', lambda: self.no_rows(f'SELECT facility_id,service_key FROM {table} GROUP BY facility_id,service_key HAVING COUNT(*)>1'))
        self.check('evidence_nonempty', lambda: self.no_rows(f"SELECT 1 FROM {table} WHERE claim_text IS NULL OR trim(claim_text)=''"))

    def stage_10(self):
        def local_checks():
            for rel in ('careproof/recommend.py','tests/test_recommend.py'):
                self.must((ROOT / rel).is_file(), f'missing {rel}')
            p = subprocess.run([sys.executable,'-m','pytest','-q','tests/test_recommend.py'],cwd=ROOT,capture_output=True,text=True,timeout=120)
            self.must(p.returncode == 0, p.stdout[-1000:]+'\n'+p.stderr[-1000:])
            return 'Ranking unit tests passed'
        self.check('recommendation_tests', local_checks)

    def stage_11(self):
        def evaluation():
            r = self.file_fields('reports/11_evaluation.json', {'cases','summary'})
            self.must(len(r['cases']) >= 20, 'fewer than 20 evaluation cases')
            self.must(all({'case_id','expected','observed','evidence'} <= set(c) for c in r['cases']), 'case-level evidence absent')
            return f"{len(r['cases'])} documented cases; results need human review"
        self.check('evaluation_case_report', evaluation)
        def tests():
            p = subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,capture_output=True,text=True,timeout=180)
            self.must(p.returncode == 0,p.stdout[-1000:]+'\n'+p.stderr[-1000:])
            return 'All unit tests pass'
        self.check('test_suite', tests)

    def stage_12(self):
        self.check('app_files', lambda: self.must(all((ROOT / p).is_file() for p in ('app/app.py','app/app.yaml')), 'app.py or app.yaml missing'))
        def deploy_verification():
            # A deployed app is not established by a local JSON file; fetch live state.
            name = self.cfg.get('app_name','careproof-demo')
            p = subprocess.run(['databricks','apps','get',name,'--profile',self.cfg['profile'],'--output','json'],capture_output=True,text=True,timeout=60)
            self.must(p.returncode == 0, p.stderr[-500:])
            data = json.loads(p.stdout)
            self.must(bool(data.get('url')), 'app URL absent; app is not deployed')
            self.must(data.get('active_deployment',{}).get('status',{}).get('state') == 'SUCCEEDED', 'no successful active app deployment')
            self.must(data.get('app_status',{}).get('state') == 'RUNNING', 'app process not running (Free Edition may stop idle apps)')
            return 'Running Databricks App URL: '+ str(data['url'])
        self.check('live_app_exists', deploy_verification)

    def stage_13(self):
        def final():
            r = self.file_fields('reports/13_final.json', {'app_url','demo_queries','risk_notes'})
            self.must(len(r['demo_queries']) >= 3,'need at least 3 end-to-end demos')
            self.must(self.final_approval, 'Human signoff required: rerun stage 13 with --approve-final after viewing actual app')
            return 'Final report and human review recorded'
        self.check('final_report', final)
        self.stage_12()

    def run(self) -> dict:
        getattr(self, f'stage_{self.stage}')()
        success = all(x['pass'] for x in self.checks) and len(self.checks) > 0
        report = {
            'stage': self.stage,
            'passed': success,
            'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'source_table': self.src,
            'target_schema': self.tgt,
            'checks': self.checks,
            'note': 'Machine gates alone do NOT verify that a hospital currently offers a treatment.'
        }
        out = ROOT / 'reports' / f'gate_{self.stage}.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report,indent=2))
        return report


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit('Usage: python -m careproof_agent.gates 00')
    report = Gate(sys.argv[1].zfill(2)).run()
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
