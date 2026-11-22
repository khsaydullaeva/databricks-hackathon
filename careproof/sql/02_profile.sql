-- Materialize measured Bronze metrics for the runner's JSON export.
CREATE OR REPLACE TABLE workspace.careproof.facility_profile AS
WITH prepared AS (
    SELECT *,
        transform_values(
            map(
                    'unique_id', CAST(`unique_id` AS STRING),
                    'source_types', CAST(`source_types` AS STRING),
                    'source_ids', CAST(`source_ids` AS STRING),
                    'source_content_id', CAST(`source_content_id` AS STRING),
                    'name', CAST(`name` AS STRING),
                    'organization_type', CAST(`organization_type` AS STRING),
                    'content_table_id', CAST(`content_table_id` AS STRING),
                    'phone_numbers', CAST(`phone_numbers` AS STRING),
                    'officialPhone', CAST(`officialPhone` AS STRING),
                    'email', CAST(`email` AS STRING),
                    'websites', CAST(`websites` AS STRING),
                    'officialWebsite', CAST(`officialWebsite` AS STRING),
                    'yearEstablished', CAST(`yearEstablished` AS STRING),
                    'acceptsVolunteers', CAST(`acceptsVolunteers` AS STRING),
                    'facebookLink', CAST(`facebookLink` AS STRING),
                    'address_line1', CAST(`address_line1` AS STRING),
                    'address_line2', CAST(`address_line2` AS STRING),
                    'address_line3', CAST(`address_line3` AS STRING),
                    'address_city', CAST(`address_city` AS STRING),
                    'address_stateOrRegion', CAST(`address_stateOrRegion` AS STRING),
                    'address_zipOrPostcode', CAST(`address_zipOrPostcode` AS STRING),
                    'address_country', CAST(`address_country` AS STRING),
                    'address_countryCode', CAST(`address_countryCode` AS STRING),
                    'countries', CAST(`countries` AS STRING),
                    'facilityTypeId', CAST(`facilityTypeId` AS STRING),
                    'operatorTypeId', CAST(`operatorTypeId` AS STRING),
                    'affiliationTypeIds', CAST(`affiliationTypeIds` AS STRING),
                    'description', CAST(`description` AS STRING),
                    'area', CAST(`area` AS STRING),
                    'numberDoctors', CAST(`numberDoctors` AS STRING),
                    'capacity', CAST(`capacity` AS STRING),
                    'specialties', CAST(`specialties` AS STRING),
                    'procedure', CAST(`procedure` AS STRING),
                    'equipment', CAST(`equipment` AS STRING),
                    'capability', CAST(`capability` AS STRING),
                    'recency_of_page_update', CAST(`recency_of_page_update` AS STRING),
                    'distinct_social_media_presence_count', CAST(`distinct_social_media_presence_count` AS STRING),
                    'affiliated_staff_presence', CAST(`affiliated_staff_presence` AS STRING),
                    'custom_logo_presence', CAST(`custom_logo_presence` AS STRING),
                    'number_of_facts_about_the_organization', CAST(`number_of_facts_about_the_organization` AS STRING),
                    'post_metrics_most_recent_social_media_post_date', CAST(`post_metrics_most_recent_social_media_post_date` AS STRING),
                    'post_metrics_post_count', CAST(`post_metrics_post_count` AS STRING),
                    'engagement_metrics_n_followers', CAST(`engagement_metrics_n_followers` AS STRING),
                    'engagement_metrics_n_likes', CAST(`engagement_metrics_n_likes` AS STRING),
                    'engagement_metrics_n_engagements', CAST(`engagement_metrics_n_engagements` AS STRING),
                    'source', CAST(`source` AS STRING),
                    'coordinates', CAST(`coordinates` AS STRING),
                    'latitude', CAST(`latitude` AS STRING),
                    'longitude', CAST(`longitude` AS STRING),
                    'cluster_id', CAST(`cluster_id` AS STRING),
                    'source_urls', CAST(`source_urls` AS STRING)
            ),
            (field, value) -> regexp_replace(value, r'^\s+|\s+$', '')
        ) AS trimmed
    FROM workspace.careproof.bronze_facilities
),
normalized AS (
    SELECT *,
        transform_values(trimmed, (field, value) ->
            CASE WHEN lower(value) NOT IN ('', 'null', 'na', 'n/a') THEN value END
        ) AS clean,
        CASE
            WHEN latitude IS NULL OR longitude IS NULL THEN 'missing'
            WHEN isnan(latitude) OR isnan(longitude)
                OR abs(latitude) = CAST('Infinity' AS DOUBLE)
                OR abs(longitude) = CAST('Infinity' AS DOUBLE) THEN 'non_finite'
            WHEN abs(latitude) > 90 OR abs(longitude) > 180 THEN 'invalid_range'
            WHEN latitude NOT BETWEEN 6 AND 38 OR longitude NOT BETWEEN 68 AND 98
                THEN 'outside_india_bbox'
            ELSE 'inside_india_bbox'
        END AS coordinate_status
    FROM prepared
),
metrics AS (
    SELECT
        COUNT(*) AS total_rows,
        map(
            'capability', COUNT_IF(clean['capability'] IS NOT NULL AND COALESCE(schema_of_variant(try_parse_json(clean['capability'])) LIKE 'ARRAY<%', false) = false),
            'procedure', COUNT_IF(clean['procedure'] IS NOT NULL AND COALESCE(schema_of_variant(try_parse_json(clean['procedure'])) LIKE 'ARRAY<%', false) = false),
            'specialties', COUNT_IF(clean['specialties'] IS NOT NULL AND COALESCE(schema_of_variant(try_parse_json(clean['specialties'])) LIKE 'ARRAY<%', false) = false),
            'equipment', COUNT_IF(clean['equipment'] IS NOT NULL AND COALESCE(schema_of_variant(try_parse_json(clean['equipment'])) LIKE 'ARRAY<%', false) = false),
            'source_urls', COUNT_IF(clean['source_urls'] IS NOT NULL AND COALESCE(schema_of_variant(try_parse_json(clean['source_urls'])) LIKE 'ARRAY<%', false) = false)
        ) AS json_parse_failures,
        named_struct(
            'missing_coordinates', COUNT_IF(coordinate_status = 'missing'),
            'non_finite_coordinates', COUNT_IF(coordinate_status = 'non_finite'),
            'outside_global_range', COUNT_IF(coordinate_status = 'invalid_range'),
            'outside_india_bbox', COUNT_IF(coordinate_status IN ('invalid_range', 'outside_india_bbox')),
            'inside_india_bbox', COUNT_IF(coordinate_status = 'inside_india_bbox'),
            'invalid_pin', COUNT_IF(clean['address_zipOrPostcode'] NOT RLIKE '^[1-9][0-9]{5}$')
        ) AS geography,
        COUNT(clean['unique_id']) - COUNT(DISTINCT CASE WHEN clean['unique_id'] IS NOT NULL THEN unique_id END)
            AS duplicate_ids,
        COUNT(clean['cluster_id']) - COUNT(DISTINCT CASE WHEN clean['cluster_id'] IS NOT NULL THEN cluster_id END)
            AS repeated_cluster_rows,
        map(
            'yearEstablished', COUNT_IF(clean['yearEstablished'] IS NOT NULL AND TRY_CAST(clean['yearEstablished'] AS INT) IS NULL),
            'numberDoctors', COUNT_IF(clean['numberDoctors'] IS NOT NULL AND TRY_CAST(clean['numberDoctors'] AS INT) IS NULL),
            'capacity', COUNT_IF(clean['capacity'] IS NOT NULL AND TRY_CAST(clean['capacity'] AS INT) IS NULL)
        ) AS numeric_parse_failures,
        COUNT_IF(clean['post_metrics_most_recent_social_media_post_date'] IS NOT NULL
            AND TRY_CAST(clean['post_metrics_most_recent_social_media_post_date'] AS TIMESTAMP) IS NULL)
            AS social_post_timestamp_parse_failures
    FROM normalized
),
field_counts AS (
    SELECT field,
        COUNT_IF(value IS NULL OR lower(value) IN ('', 'null', 'na', 'n/a')) AS missing_count,
        COUNT_IF(value IS NULL) AS sql_null_count,
        COUNT_IF(value = '') AS blank_count
    FROM normalized
    LATERAL VIEW explode(trimmed) fields AS field, value
    GROUP BY field
),
missingness AS (
    SELECT
        map_from_entries(collect_list(struct(field, missing_count))) AS null_counts,
        map_from_entries(collect_list(struct(field, sql_null_count))) AS sql_null_counts,
        map_from_entries(collect_list(struct(field, blank_count))) AS blank_counts
    FROM field_counts
),
mixed_clusters AS (
    SELECT cluster_id
    FROM normalized
    WHERE clean['cluster_id'] IS NOT NULL
    GROUP BY cluster_id
    HAVING COUNT(DISTINCT lower(clean['address_city'])) > 1
),
review_rows AS (
    SELECT unique_id, name, address_city, address_stateOrRegion,
        latitude, longitude, cluster_id, coordinate_status
    FROM normalized
    WHERE coordinate_status <> 'inside_india_bbox'
        OR cluster_id IN (SELECT cluster_id FROM mixed_clusters)
    ORDER BY coordinate_status, unique_id, name, address_city, address_stateOrRegion,
        latitude, longitude, cluster_id
    LIMIT 12
)
SELECT to_json(struct(
    metrics.*,
    missingness.*,
    (SELECT COUNT(*) FROM mixed_clusters) AS clusters_with_multiple_cities,
    (SELECT sort_array(collect_list(struct(
        unique_id, name, address_city, address_stateOrRegion,
        latitude, longitude, cluster_id, coordinate_status
    ))) FROM review_rows) AS anomaly_samples
), map('ignoreNullFields', 'false')) AS profile_json
FROM metrics CROSS JOIN missingness;
