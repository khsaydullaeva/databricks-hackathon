import json

import pytest

from careproof.audit import collect
from stage11_fixtures import cases


class AuditDatabase:
    config = {"target_schema": "workspace.careproof"}

    def __init__(self, rows):
        self.rows = rows

    def number(self, statement):
        return 1

    def execute(self, statement):
        return [[json.dumps(row)] for row in self.rows]


@pytest.mark.parametrize("bronze_field", ["This facility offers hemodialysis.", "Unrelated content."])
def test_audit_checks_citations_against_bronze(bronze_field):
    fixture = next(iter(cases()))
    row = dict(fixture["rows"][0])
    row.update(source_content_id="record-hash", bronze_id_count=1, bronze_records=[
        {"source_content_id": "record-hash", "capability": bronze_field},
    ])
    audit = collect(AuditDatabase([row]))
    assert len(audit["searches"]) == 36
    assert audit["review_status"] == "unlabeled"
    delhi = next(search for search in audit["searches"] if search["query"] == {
        "service": "dialysis", "location": {"city": "Delhi"},
    })
    assert delhi["citation_checks"] == [{
        "facility_id": row["facility_id"],
        "bronze_id_count": 1,
        "exact_own_record_citation": bronze_field == row["claim_text"],
    }]


def test_audit_rejects_incomplete_export():
    with pytest.raises(RuntimeError, match="Incomplete audit export"):
        collect(AuditDatabase([]))
