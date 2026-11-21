"""Export the measured Stage 02 Databricks profile for the independent gate."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .db import ROOT


def build_profile(db, cfg):
    schema = cfg['target_schema']
    payload = db.scalar(f'SELECT profile_json FROM {schema}.facility_profile')
    report = json.loads(payload)
    expected_fields = {
        column['name']
        for column in json.loads((ROOT / 'spec' / 'facilities_schema.json').read_text())['columns']
    }
    total = report['total_rows']
    if total == 0:
        for key in ('null_counts', 'sql_null_counts', 'blank_counts'):
            report[key] = dict.fromkeys(expected_fields, 0)
    if set(report['null_counts']) != expected_fields:
        raise RuntimeError('Measured profile does not cover the expected Bronze fields')

    report.update(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        source=f'{schema}.bronze_facilities',
        marketplace_source=cfg['source_table'],
        column_count=len(expected_fields),
        missing_rates={
            field: count / total if total else None
            for field, count in report['null_counts'].items()
        },
        notes=[
            'Missing-like values include SQL NULL, whitespace-only strings, null, NA and N/A.',
            'JSON failures count populated values that fail parsing as a top-level array.',
            'Duplicate and repeated-cluster counts are excess rows for exact populated IDs.',
            'The approximate India bounding box cannot establish city or facility identity.',
            'Multi-city clusters are review signals and may contain legitimate branches.',
            'Source URLs are unverified leads.',
        ],
    )
    dest = ROOT / 'reports' / '02_profile.json'
    dest.parent.mkdir(exist_ok=True, parents=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=dest.parent, delete=False) as output:
        temp_path = Path(output.name)
    try:
        with temp_path.open('w', encoding='utf-8') as output:
            json.dump(report, output, indent=2, sort_keys=True, allow_nan=False)
            output.write('\n')
        temp_path.replace(dest)
    finally:
        temp_path.unlink(missing_ok=True)
    print(f'   Measured data profile saved to {dest}')
    return report
