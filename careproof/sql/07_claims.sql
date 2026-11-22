-- Extract evidence from the immutable Bronze snapshot using Stage 06 rules.
-- Keep the raw field and the exact excerpt, including any JSON escapes.
CREATE OR REPLACE TABLE workspace.careproof.service_claims
USING DELTA
AS
WITH raw_fields AS (
    SELECT
        b.unique_id AS facility_id,
        b.source_content_id,
        b.source_urls AS source_urls_raw,
        f.source_field,
        f.source_text
    FROM workspace.careproof.bronze_facilities b
    LATERAL VIEW STACK(
        5,
        'description', b.description,
        'capability', b.capability,
        'procedure', b.procedure,
        'specialties', b.specialties,
        'equipment', b.equipment
    ) f AS source_field, source_text
    WHERE b.unique_id IS NOT NULL AND trim(b.unique_id) <> ''
      AND f.source_text IS NOT NULL
), field_formats AS (
    SELECT *,
        CASE
            WHEN substring(ltrim(source_text), 1, 1) = '['
                THEN CASE WHEN from_json(source_text, 'ARRAY<STRING>') IS NOT NULL
                          THEN 'json_array' ELSE 'malformed_array' END
            ELSE 'prose'
        END AS field_format
    FROM raw_fields
), spans AS (
    SELECT *, span.ordinal + 1 AS span_number, span.claim_text
    FROM field_formats
    LATERAL VIEW POSEXPLODE(
        CASE field_format
            WHEN 'json_array' THEN regexp_extract_all(source_text, r'"((?:[^"\\]|\\.)*)"', 1)
            WHEN 'prose' THEN split(source_text, r'(?<=[.!?])\s+|[;\r\n]+')
            ELSE array(source_text)
        END
    ) span AS ordinal, claim_text
), normalized AS (
    SELECT *,
        trim(regexp_replace(lower(
            CASE WHEN field_format = 'json_array'
                 THEN element_at(from_json(concat('["', claim_text, '"]'), 'ARRAY<STRING>'), 1)
                 ELSE claim_text END
        ), '[^a-z0-9]+', ' ')) AS matching_text
    FROM spans
), matches AS (
    SELECT
        n.facility_id,
        n.source_content_id,
        t.service_key,
        n.source_field,
        n.source_text,
        n.source_urls_raw,
        n.field_format,
        n.span_number,
        n.claim_text,
        t.alias,
        CASE
            WHEN n.matching_text RLIKE t.negation_regex THEN 3
            WHEN n.matching_text RLIKE t.mention_only_regex THEN 2
            WHEN n.field_format <> 'malformed_array'
                 AND n.source_field IN ('description', 'capability', 'procedure')
                 AND n.matching_text RLIKE t.offering_regex THEN 1
            ELSE 0
        END AS cue_priority
    FROM normalized n
    JOIN workspace.careproof.service_taxonomy t
      ON n.matching_text RLIKE t.alias_regex
    WHERE trim(n.claim_text) <> ''
), claims AS (
    SELECT
        facility_id, source_content_id, service_key, source_field, source_text,
        source_urls_raw, field_format, span_number, claim_text,
        sort_array(collect_set(alias)) AS matched_aliases,
        max(cue_priority) AS cue_priority
    FROM matches
    GROUP BY facility_id, source_content_id, service_key, source_field, source_text,
             source_urls_raw, field_format, span_number, claim_text
), duplicate_ids AS (
    SELECT unique_id
    FROM workspace.careproof.bronze_facilities
    WHERE unique_id IS NOT NULL
    GROUP BY unique_id
    HAVING count(*) > 1
), duplicate_fields AS (
    SELECT
        b.unique_id,
        map(
            'description', b.description,
            'capability', b.capability,
            'procedure', b.procedure,
            'specialties', b.specialties,
            'equipment', b.equipment
        ) AS raw_fields
    FROM workspace.careproof.bronze_facilities b
    JOIN duplicate_ids d ON b.unique_id = d.unique_id
)
SELECT
    c.facility_id,
    c.source_content_id,
    c.service_key,
    c.source_field,
    c.claim_text,
    c.source_text,
    c.span_number,
    c.field_format,
    c.matched_aliases,
    CASE
        WHEN c.cue_priority = 3 THEN 'negated'
        WHEN d.unique_id IS NOT NULL THEN 'uncertain'
        WHEN c.cue_priority = 1 THEN 'candidate_claim'
        ELSE 'mention_only'
    END AS claim_type,
    CASE
        WHEN c.cue_priority = 3 THEN 'negation_cue'
        WHEN d.unique_id IS NOT NULL THEN 'duplicate_facility_id'
        WHEN c.cue_priority = 2 THEN 'contextual_mention'
        WHEN c.cue_priority = 1 THEN 'explicit_offering_cue'
        WHEN c.field_format = 'malformed_array' THEN 'malformed_source_array'
        ELSE 'no_explicit_offering_cue'
    END AS claim_reason,
    c.source_urls_raw,
    CASE
        WHEN c.source_urls_raw IS NULL
             OR lower(trim(c.source_urls_raw)) IN ('', 'null', 'none', 'n/a')
             OR c.source_urls_raw RLIKE r'^\s*\[\s*\]\s*$' THEN 'missing'
        ELSE 'unverified_row_leads'
    END AS source_urls_status
FROM claims c
LEFT JOIN duplicate_ids d ON c.facility_id = d.unique_id
-- The stage gate resolves lineage by unique_id alone. Where Bronze repeats
-- an ID, only emit text shared by every record with that ID in the named field.
LEFT ANTI JOIN duplicate_fields b
    ON c.facility_id = b.unique_id
   AND instr(coalesce(element_at(b.raw_fields, c.source_field), ''), c.claim_text) = 0;
