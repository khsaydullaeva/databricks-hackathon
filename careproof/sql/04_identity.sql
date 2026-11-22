-- Preserve Silver row multiplicity and original identifiers. Flags are review leads.
CREATE OR REPLACE TABLE workspace.careproof.identity_review
USING DELTA
AS
WITH normalized AS (
    SELECT unique_id AS facility_id, name, city_clean, state_clean, cluster_id,
        CASE WHEN NOT array_contains(quality_flags, 'missing_unique_id')
            THEN trim(unique_id) END AS id_key,
        trim(regexp_replace(name_key, r'[^\p{L}\p{N}\p{M}]+', ' ')) AS facility_name_key,
        nullif(trim(regexp_replace(city_clean, r'[^\p{L}\p{N}\p{M}]+', ' ')), '') AS city_key,
        CASE WHEN lower(trim(cluster_id)) NOT IN ('', 'null', 'na', 'n/a')
            THEN trim(cluster_id) END AS cluster_key,
        description, procedure, capability
    FROM workspace.careproof.silver_facilities
), signals AS (
    SELECT *,
        count(*) OVER (PARTITION BY id_key) AS id_count,
        count(*) OVER (PARTITION BY facility_name_key, city_key, state_clean) AS local_name_count,
        min(city_key) OVER (PARTITION BY facility_name_key) AS first_name_city,
        max(city_key) OVER (PARTITION BY facility_name_key) AS last_name_city,
        min(state_clean) OVER (PARTITION BY facility_name_key) AS first_name_state,
        max(state_clean) OVER (PARTITION BY facility_name_key) AS last_name_state,
        min(city_key) OVER (PARTITION BY cluster_key) AS first_cluster_city,
        max(city_key) OVER (PARTITION BY cluster_key) AS last_cluster_city,
        min(state_clean) OVER (PARTITION BY cluster_key) AS first_cluster_state,
        max(state_clean) OVER (PARTITION BY cluster_key) AS last_cluster_state,
        min(nullif(facility_name_key, '')) OVER (PARTITION BY cluster_key) AS first_cluster_name,
        max(nullif(facility_name_key, '')) OVER (PARTITION BY cluster_key) AS last_cluster_name,
        sha2(to_json(named_struct(
            'name', facility_name_key, 'city', city_key,
            'description', description, 'procedure', procedure, 'capability', capability
        )), 256) AS text_key
    FROM normalized
), mention_candidates AS (
    SELECT DISTINCT facility_id, name, facility_name_key, city_key,
        split(facility_name_key, ' ')[0] AS first_token
    FROM normalized
    -- Require a multiword catalog name with a facility-type term.
    WHERE facility_name_key LIKE '% %'
        AND facility_name_key RLIKE '(^| )(hospitals?|clinics?|institute|infirmary|dispensary|nursing home|medical cent(er|re))( |$)'
        AND facility_name_key NOT IN (
            'nursing home', 'medical centre', 'medical center', 'general hospital',
            'district hospital', 'civil hospital', 'government hospital'
        )
), source_fields AS (
    SELECT DISTINCT text_key, facility_name_key, city_key, source_field,
        trim(regexp_replace(lower(raw_text), r'[^\p{L}\p{N}\p{M}]+', ' ')) AS field_text
    FROM signals
    LATERAL VIEW explode(map(
        'description', description, 'procedure', procedure, 'capability', capability
    )) fields AS source_field, raw_text
    WHERE raw_text IS NOT NULL
), field_tokens AS (
    SELECT text_key, facility_name_key, city_key, source_field,
        concat(' ', field_text, ' ') AS bounded_text, token
    FROM source_fields
    LATERAL VIEW explode(array_distinct(split(field_text, ' '))) tokens AS token
), matches AS (
    SELECT f.text_key,
        sort_array(collect_set(named_struct(
            'source_field', f.source_field,
            'other_facility_id', c.facility_id,
            'other_facility_name', c.name,
            'other_city', c.city_key
        ))) AS other_facility_mentions
    FROM field_tokens AS f
    JOIN mention_candidates AS c ON f.token = c.first_token
    WHERE (
        coalesce(f.facility_name_key, '') <> c.facility_name_key
        -- A shorter name embedded in the record's own name is ambiguous.
        AND instr(concat(' ', coalesce(f.facility_name_key, ''), ' '),
                  concat(' ', c.facility_name_key, ' ')) = 0
        AND instr(f.bounded_text, concat(' ', c.facility_name_key, ' ')) > 0
    ) OR (
        f.facility_name_key = c.facility_name_key AND f.city_key <> c.city_key
        AND instr(f.bounded_text, concat(' ', c.facility_name_key, ' ', c.city_key, ' ')) > 0
    )
    GROUP BY f.text_key
), reviewed AS (
    SELECT s.facility_id, s.name, s.city_clean, s.state_clean, s.cluster_id,
        coalesce(m.other_facility_mentions, array()) AS other_facility_mentions,
        filter(array(
            CASE WHEN s.id_key IS NULL THEN 'missing_facility_id' END,
            CASE WHEN nullif(s.facility_name_key, '') IS NULL THEN 'missing_facility_name' END,
            CASE WHEN s.id_key IS NOT NULL AND s.id_count > 1 THEN 'repeated_facility_id' END,
            CASE WHEN nullif(s.facility_name_key, '') IS NOT NULL AND s.city_key IS NOT NULL
                AND s.local_name_count > 1 THEN 'shared_name_and_locality' END,
            CASE WHEN nullif(s.facility_name_key, '') IS NOT NULL
                AND (s.first_name_city <> s.last_name_city OR s.first_name_state <> s.last_name_state)
                THEN 'shared_name_across_localities' END,
            CASE WHEN s.cluster_key IS NOT NULL
                AND (s.first_cluster_city <> s.last_cluster_city OR s.first_cluster_state <> s.last_cluster_state)
                THEN 'cluster_spans_localities' END,
            CASE WHEN s.cluster_key IS NOT NULL AND s.first_cluster_name <> s.last_cluster_name
                THEN 'cluster_has_distinct_names' END,
            CASE WHEN m.text_key IS NOT NULL THEN 'text_mentions_other_facility_or_branch' END
        ), reason_code -> reason_code IS NOT NULL) AS reason_codes
    FROM signals AS s
    LEFT JOIN matches AS m ON s.text_key = m.text_key
)
SELECT *,
    CASE WHEN size(reason_codes) = 0 THEN 'clear' ELSE 'needs_review' END AS identity_status,
    concat_ws(', ', reason_codes) AS reason
FROM reviewed;
