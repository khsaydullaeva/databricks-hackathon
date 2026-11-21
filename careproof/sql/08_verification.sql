-- Assess dataset evidence at the original claim grain.
CREATE OR REPLACE TABLE workspace.careproof.verified_claims
USING DELTA
AS
WITH identity_by_facility AS (
    -- Repeated raw IDs can have several reviews. Keep all flags without
    -- multiplying claims or treating any one review as definitive.
    SELECT facility_id,
        count(*) AS identity_review_count,
        count_if(identity_status = 'clear') AS clear_identity_review_count,
        sort_array(array_distinct(flatten(collect_set(reason_codes)))) AS identity_reason_codes
    FROM workspace.careproof.identity_review
    GROUP BY facility_id
), assessment_inputs AS (
    SELECT c.*,
        coalesce(i.identity_review_count, 0) AS identity_review_count,
        coalesce(i.clear_identity_review_count, 0) AS clear_identity_review_count,
        coalesce(i.identity_reason_codes, array()) AS identity_reason_codes,
        count_if(c.claim_type = 'negated') OVER (
            PARTITION BY c.facility_id, c.service_key
        ) > 0 AS has_service_negation
    FROM workspace.careproof.service_claims c
    LEFT JOIN identity_by_facility i ON c.facility_id = i.facility_id
), assessed AS (
    SELECT *,
        CASE
            WHEN claim_type = 'negated' THEN 'explicit_negation'
            WHEN array_contains(identity_reason_codes, 'text_mentions_other_facility_or_branch')
                THEN 'text_mentions_other_facility_or_branch'
            WHEN claim_type = 'candidate_claim' AND has_service_negation
                THEN 'opposing_service_text'
            WHEN identity_review_count = 0 THEN 'missing_identity_review'
            WHEN identity_review_count > 1 THEN 'multiple_identity_reviews'
            WHEN clear_identity_review_count <> 1 OR size(identity_reason_codes) > 0
                THEN 'facility_identity_needs_review'
            WHEN claim_type = 'mention_only' THEN 'mention_without_offering'
            WHEN claim_type IS NULL OR claim_type <> 'candidate_claim'
                THEN 'inconclusive_extraction'
            WHEN lower(claim_text) RLIKE r'\b(formerly|previously|used to|historically|no longer)\b'
                THEN 'possible_historical_offering'
            ELSE 'attributable_dataset_offering_text'
        END AS verification_reason
    FROM assessment_inputs
)
SELECT *,
    CASE verification_reason
        WHEN 'explicit_negation' THEN 'rejected'
        WHEN 'text_mentions_other_facility_or_branch' THEN 'conflicting'
        WHEN 'opposing_service_text' THEN 'conflicting'
        WHEN 'attributable_dataset_offering_text' THEN 'supported_claim'
        ELSE 'uncertain'
    END AS evidence_status,
    CASE source_urls_status
        WHEN 'missing' THEN 'no_source_urls'
        ELSE 'unverified_row_leads'
    END AS source_attribution_status
FROM assessed;
