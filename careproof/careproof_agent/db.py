"""Databricks SQL access for independent (non-agent) validation.

Auth uses a locally configured OAuth CLI profile. This module deliberately does not
accept or print long-lived secrets. Tested locally with stub objects; live access
requires a Databricks Free Edition workspace and a running SQL warehouse.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    path = ROOT / 'config.json'
    if not path.exists():
        raise RuntimeError('Create config.json: cp config.example.json config.json')
    config = json.loads(path.read_text())
    if config['source_table'] != 'databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities':
        raise ValueError('Source table must be the exact hackathon Marketplace facilities table')
    schema = config['target_schema']
    if schema == config['source_table'].rsplit('.', 1)[0] or not schema.startswith('workspace.'):
        raise ValueError('Default safety policy: target_schema must be a schema under workspace catalog')
    return config


class DatabricksSQL:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise RuntimeError('Install requirements: python -m pip install -r requirements.txt') from exc
        self.client = WorkspaceClient(profile=self.config.get('profile', 'careproof'))
        warehouse_id = self.config.get('warehouse_id')
        if not warehouse_id:
            warehouses = list(self.client.warehouses.list())
            if len(warehouses) != 1:
                raise RuntimeError(
                    f'Found {len(warehouses)} warehouses; set warehouse_id in config.json explicitly'
                )
            warehouse_id = warehouses[0].id
        self.warehouse_id = warehouse_id

    def execute(self, statement: str, *, max_wait_seconds: int = 300) -> list[list[str | None]]:
        response = self.client.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=statement,
            wait_timeout='50s',
        )
        start = time.monotonic()
        while True:
            status = getattr(response, 'status', None)
            value = getattr(status, 'state', None)
            state = str(getattr(value, 'value', value)).upper()
            if state == 'SUCCEEDED':
                result = getattr(response, 'result', None)
                return getattr(result, 'data_array', None) or []
            if state in ('FAILED', 'CANCELED', 'CLOSED'):
                error = getattr(status, 'error', None)
                raise RuntimeError(f'Databricks SQL {state}: {error or statement[:150]}')
            if time.monotonic() - start > max_wait_seconds:
                raise TimeoutError(f'Databricks SQL exceeded {max_wait_seconds}s: {statement[:150]}')
            statement_id = getattr(response, 'statement_id', None)
            if not statement_id:
                raise RuntimeError(f'Missing Databricks statement ID; status was {state}')
            time.sleep(2.5)
            response = self.client.statement_execution.get_statement(statement_id)

    def scalar(self, statement: str):
        rows = self.execute(statement)
        if not rows or not rows[0]:
            raise AssertionError('Query returned no scalar result: ' + statement[:160])
        return rows[0][0]

    def number(self, statement: str) -> int:
        return int(self.scalar(statement))

    def cols(self, table: str) -> dict[str, str]:
        # DESCRIBE TABLE may contain comment/partition sections: stop at marker.
        rows = self.execute(f'DESCRIBE TABLE {table}')
        columns = {}
        for row in rows:
            name = str(row[0] or '')
            if not name or name.startswith('#'):
                break
            columns[name.lower()] = str(row[1] or '').lower()
        return columns
