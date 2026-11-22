"""Validate the app artifact independently of the working tree import path."""
import json

import pytest

from careproof.snapshot import APP_FILES, CANONICAL, ROOT, build_snapshot, digest
from careproof.verify_local import isolated_import, scan


def test_snapshot_is_self_contained_and_reproducible(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    expected = set(APP_FILES) | {f"careproof/{name}" for name in CANONICAL}
    hashes = build_snapshot(first)
    assert set(hashes) == expected
    assert build_snapshot(second) == hashes
    assert json.loads((first / "snapshot_manifest.json").read_text()) == hashes
    assert all(digest(first / name) == digest(second / name) == value for name, value in hashes.items())
    assert scan(first.rglob("*"), first) == []
    isolated_import(first)
    with pytest.raises(FileExistsError):
        build_snapshot(first)


def test_secret_scan_reports_only_path_and_kind(tmp_path):
    secret_file = tmp_path / "example"
    secret_file.write_bytes(b"dapi" + b"0123456789abcdef" * 2)
    assert scan([secret_file], tmp_path) == [("example", "Databricks PAT")]
    with pytest.raises(ValueError):
        build_snapshot(ROOT / "app" / "inside")


def test_warehouse_timeout_does_not_poison_next_read(monkeypatch):
    from types import SimpleNamespace as NS

    from app import data

    clock = iter((0, 40, 41, 42))
    monkeypatch.setattr(data.time, "monotonic", lambda: next(clock))
    responses = iter((
        NS(statement_id="slow", status=NS(state=NS(value="PENDING"))),
        NS(status=NS(state=NS(value="SUCCEEDED")), manifest=NS(truncated=False, total_row_count=1),
           result=NS(data_array=[['{"city_clean":"jaipur"}']], next_chunk_index=None)),
    ))
    cancellations = []
    sql = NS(execute_statement=lambda **_: next(responses), cancel_execution=cancellations.append)
    reader = data.Warehouse(NS(statement_execution=sql), "test")
    with pytest.raises(data.DataUnavailable, match="too long"):
        reader.addresses()
    assert cancellations == ["slow"]
    assert reader.addresses() == ([{"city_clean": "jaipur"}], 1)


def test_streamlit_staged_entrypoint_opens_in_isolation(tmp_path):
    import subprocess
    import sys

    snapshot = tmp_path / "app"
    build_snapshot(snapshot)
    subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "from streamlit.testing.v1 import AppTest; "
         "app = AppTest.from_file('app.py').run(); "
         "assert not app.exception; assert app.title"],
        check=True, cwd=snapshot,
    )
