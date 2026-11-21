"""Read-only Databricks fixture check. Run from the root with Python and project OAuth."""

from careproof_agent.db import ROOT, DatabricksSQL
from careproof_agent.runner import split_sql


def main():
    statement, = split_sql((ROOT / 'sql/04_identity.sql').read_text())
    query = statement[statement.index('WITH normalized AS ('):]
    fixtures = """
        SELECT * FROM VALUES
          ('a', 'Lotus Hospital', 'delhi', 'delhi', 'group',
           'Patients referred to Cedar Hospital.', NULL, NULL, array('fixture')),
          ('b', 'Lotus Hospital', 'mumbai', 'maharashtra', 'group',
           'Lotus Hospital, Delhi provides care.', NULL, NULL, array('fixture')),
          ('c', 'Cedar Hospital', 'delhi', 'delhi', 'group',
           NULL, NULL, NULL, array('fixture')),
          ('c', 'Cedar Hospital', 'delhi', 'delhi', 'group',
           NULL, NULL, NULL, array('fixture')),
          ('d', 'River Hospital', 'delhi', 'delhi', NULL,
           NULL, 'Cedar Hospitalized patients', NULL, array('fixture')),
          (NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
           array('missing_unique_id', 'missing_name'))
        AS fixtures(unique_id, name, city_clean, state_clean, cluster_id,
                    description, procedure, capability, quality_flags)
    """
    query = query.replace(
        'FROM workspace.careproof.silver_facilities',
        f'FROM ({fixtures}) AS rows_with_names',
    ).replace(
        'regexp_replace(name_key,', 'regexp_replace(lower(name),',
    )
    checks = DatabricksSQL().execute(f"""
        SELECT
          count(*) = 6,
          count_if(facility_id = 'a' AND exists(other_facility_mentions,
            m -> m.other_facility_id = 'c' AND m.source_field = 'description')) = 1,
          count_if(facility_id = 'b' AND exists(other_facility_mentions,
            m -> m.other_facility_id = 'a')
            AND array_contains(reason_codes, 'shared_name_across_localities')) = 1,
          count_if(facility_id = 'c' AND array_contains(reason_codes, 'repeated_facility_id')
            AND array_contains(reason_codes, 'shared_name_and_locality')
            AND array_contains(reason_codes, 'cluster_has_distinct_names')
            AND array_contains(reason_codes, 'cluster_spans_localities')) = 2,
          count_if(facility_id = 'd' AND identity_status = 'clear') = 1,
          count_if(facility_id IS NULL AND array_contains(reason_codes, 'missing_facility_id')) = 1,
          count_if(identity_status = 'needs_review' AND trim(reason) = '') = 0
        FROM ({query}) AS review
    """)
    names = ['row coverage', 'mixed hospital', 'other branch', 'duplicates and cluster',
             'whole-name boundary', 'missing ID', 'flag reasons']
    row, = checks
    failed = [name for name, value in zip(names, row, strict=True) if str(value).lower() != 'true']
    if failed:
        raise AssertionError(f'Identity fixture checks failed: {", ".join(failed)}')
    print('All seven identity fixture checks passed')


if __name__ == '__main__':
    main()
