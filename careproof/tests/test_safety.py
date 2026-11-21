"""Offline local regression tests: source protection and stage contract."""
import json
import unittest
from pathlib import Path

from careproof_agent.runner import guard_statement, SRC, split_sql

CFG={'target_schema':'workspace.careproof'}

class SafetyTests(unittest.TestCase):
    def test_stage_sql_files_are_parseable_and_guarded(self):
        root=Path(__file__).resolve().parents[1]
        for stage in ['00','01','02','03']:
            scripts=list((root/'sql').glob(stage+'_*.sql'))
            self.assertTrue(scripts)
            for script in scripts:
                statements=split_sql(script.read_text())
                self.assertGreaterEqual(len(statements),1)
                for statement in statements:
                    guard_statement(statement,CFG)

    def test_source_read_is_allowed(self):
        guard_statement(f'SELECT * FROM {SRC} LIMIT 1',CFG)

    def test_create_bronze_allowed(self):
        guard_statement(f'CREATE TABLE IF NOT EXISTS workspace.careproof.bronze_facilities AS SELECT * FROM {SRC}',CFG)

    def test_schema_creation_allowed(self):
        guard_statement('CREATE SCHEMA IF NOT EXISTS workspace.careproof',CFG)

    def test_source_drop_blocked(self):
        with self.assertRaises(RuntimeError):
            guard_statement(f'DROP TABLE {SRC}',CFG)

    def test_target_schema_drop_blocked(self):
        with self.assertRaises(RuntimeError):
            guard_statement('DROP SCHEMA workspace.careproof CASCADE', CFG)

    def test_arbitrary_write_blocked(self):
        with self.assertRaises(RuntimeError):
            guard_statement('CREATE OR REPLACE TABLE other.bad AS SELECT * FROM workspace.careproof.bronze_facilities',CFG)

    def test_source_insert_blocked(self):
        with self.assertRaises(RuntimeError):
            guard_statement(f'INSERT INTO {SRC} VALUES (1)',CFG)

    def test_stage_specs_complete(self):
        root=Path(__file__).resolve().parents[1]
        data=json.loads((root/'spec/stages.json').read_text())
        self.assertEqual(len(data['stages']),14)
        self.assertEqual([s['id'] for s in data['stages']],[f'{i:02d}' for i in range(14)])

if __name__ == '__main__':
    unittest.main()
