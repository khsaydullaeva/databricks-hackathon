-- Claims use Bronze unique_id. Geography uses derived facility keys for
-- repeated IDs, so attach location only when the raw ID is unambiguous.
CREATE OR REPLACE TABLE workspace.careproof.gold_service_index
USING DELTA
AS
WITH best_claim AS (
    SELECT *
    FROM workspace.careproof.verified_claims
    WHERE facility_id IS NOT NULL AND trim(facility_id) <> ''
      AND service_key IS NOT NULL AND trim(service_key) <> ''
      AND claim_text IS NOT NULL AND trim(claim_text) <> ''
      AND instr(source_text, claim_text) > 0
    -- Keep conflict and explicit negation visible even when positive text exists.
    QUALIFY row_number() OVER (
        PARTITION BY facility_id, service_key
        ORDER BY
            CASE evidence_status
                WHEN 'conflicting' THEN 1
                WHEN 'rejected' THEN 2
                WHEN 'supported_claim' THEN 3
                ELSE 4
            END,
            CASE source_field
                WHEN 'capability' THEN 1
                WHEN 'procedure' THEN 2
                WHEN 'description' THEN 3
                WHEN 'specialties' THEN 4
                ELSE 5
            END,
            source_field, source_content_id, span_number, claim_text,
            source_text, source_urls_raw, field_format
    ) = 1
)
SELECT
    c.*,
    c.facility_id AS unique_id,
    g.facility_id AS geo_facility_id,
    g.name,
    g.city_clean,
    g.state_clean,
    g.district_clean,
    g.pin_clean,
    coalesce(g.pin_status, 'unresolved_geography') AS pin_status,
    coalesce(g.geo_status, 'unresolved_geography') AS geo_status,
    coalesce(g.admin_status, 'unresolved_geography') AS admin_status,
    g.pin_state_disagreement,
    g.country_disagreement,
    g.candidate_latitude,
    g.candidate_longitude,
    g.coordinate_provenance,
    g.address_provenance
FROM best_claim c
LEFT JOIN workspace.careproof.geo_facilities g
    ON c.facility_id = g.unique_id
   AND g.source_id_status = 'unique_source_id';
