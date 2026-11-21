"""Read-only Stage 08 Databricks check using synthetic adversarial fixtures."""

from careproof_agent.db import ROOT, DatabricksSQL
from careproof_agent.runner import split_sql


def main():
    statement, = split_sql((ROOT / 'sql/08_verification.sql').read_text())
    query = statement[statement.index('WITH identity_by_facility AS ('):]
    query = query.replace('workspace.careproof.service_claims', """(
        SELECT * FROM VALUES
          ('clear', 'dialysis', 'Provides dialysis', 'candidate_claim', 'unverified_row_leads'),
          ('mixed', 'dialysis', 'Provides dialysis', 'candidate_claim', 'unverified_row_leads'),
          ('mixed', 'mri', 'No MRI', 'negated', 'missing'),
          ('branch', 'dialysis', 'Provides dialysis', 'candidate_claim', 'missing'),
          ('clear', 'mri', 'Previously offered MRI', 'candidate_claim', 'missing'),
          ('orphan', 'mri', 'Provides MRI', 'candidate_claim', 'unverified_row_leads'),
          ('clear', 'nicu', 'No NICU', 'negated', 'missing'),
          ('clear', 'nicu', 'Provides NICU', 'candidate_claim', 'missing')
        AS fixtures(facility_id, service_key, claim_text, claim_type, source_urls_status)
    )""").replace('workspace.careproof.identity_review', """(
        SELECT * FROM VALUES
          ('clear', 'clear', CAST(array() AS ARRAY<STRING>)),
          ('mixed', 'clear', CAST(array() AS ARRAY<STRING>)),
          ('mixed', 'needs_review', array('text_mentions_other_facility_or_branch')),
          ('branch', 'needs_review', array('cluster_spans_localities'))
        AS fixtures(facility_id, identity_status, reason_codes)
    )""")
    row, = DatabricksSQL().execute(f"""
        SELECT count(*) = 8
            AND count_if(evidence_status = 'supported_claim'
                AND facility_id = 'clear' AND service_key = 'dialysis'
                AND source_attribution_status = 'unverified_row_leads') = 1
            AND count_if(evidence_status = 'supported_claim') = 1
            AND count_if(evidence_status = 'rejected' AND claim_type = 'negated') = 2
            AND count_if(evidence_status = 'conflicting' AND facility_id = 'mixed') = 1
            AND count_if(evidence_status = 'conflicting'
                AND verification_reason = 'opposing_service_text') = 1
            AND count_if(evidence_status = 'uncertain'
                AND verification_reason IN ('possible_historical_offering',
                    'missing_identity_review', 'facility_identity_needs_review')) = 3
        FROM ({query}) AS review
    """)
    if str(row[0]).lower() != 'true':
        raise AssertionError('Stage 08 verification fixture checks failed')
    print('Stage 08 verification fixture checks passed')


if __name__ == '__main__':
    main()
