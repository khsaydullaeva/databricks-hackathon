-- Haversine straight-line distance in km. Replace VALUES with an origin and
-- eligible candidate coordinates at search time. Missing inputs give NULL.
WITH pairs AS (
    SELECT * FROM VALUES
        ('same point', 28.6139D, 77.2090D, 28.6139D, 77.2090D, 0D),
        ('equator degree', 0D, 0D, 0D, 1D, 111.19508D),
        ('antimeridian', 0D, 179.5D, 0D, -179.5D, 111.19508D),
        ('antipodal', 0D, 0D, 0D, 180D, 20015.11444D),
        ('missing point', 28.6139D, 77.2090D, NULL, NULL, NULL)
    AS fixture(label, origin_lat, origin_lon, candidate_lat, candidate_lon, expected_km)
), haversine AS (
    SELECT *,
        pow(sin(radians(candidate_lat - origin_lat) / 2), 2)
        + cos(radians(origin_lat)) * cos(radians(candidate_lat))
        * pow(sin(radians(candidate_lon - origin_lon) / 2), 2) AS a
    FROM pairs
), distances AS (
    SELECT *,
        CASE WHEN a IS NOT NULL THEN
            2 * 6371.0088 * asin(sqrt(least(1D, greatest(0D, a))))
        END AS distance_km
    FROM haversine
)
SELECT assert_true(
    count_if(CASE WHEN expected_km IS NULL THEN distance_km IS NOT NULL
        ELSE distance_km IS NULL OR abs(distance_km - expected_km) > 0.001 END) = 0,
    'Great-circle distance fixture failed'
)
FROM distances;

-- Stage 03 is the only prerequisite. Preserve all Silver rows and their source IDs.
CREATE OR REPLACE TABLE workspace.careproof.geo_facilities
USING DELTA
AS
WITH inputs AS (
    SELECT unique_id, name, address_city, address_stateOrRegion,
        address_zipOrPostcode, address_country, address_countryCode,
        city_clean, state_clean, pin_clean, latitude, longitude, geo_status,
        array_contains(quality_flags, 'missing_unique_id') AS missing_id,
        array_contains(quality_flags, 'missing_pin') AS missing_pin,
        lower(trim(address_country)) AS country_key,
        lower(trim(address_countryCode)) AS country_code_key
    FROM workspace.careproof.silver_facilities
), signals AS (
    SELECT *,
        count(*) OVER (PARTITION BY unique_id) AS source_id_count,
        min(state_clean) OVER (PARTITION BY pin_clean) AS first_pin_state,
        max(state_clean) OVER (PARTITION BY pin_clean) AS last_pin_state
    FROM inputs
), keyed AS (
    SELECT *,
        CASE
            WHEN missing_id THEN 'missing_source_id'
            WHEN source_id_count > 1 THEN 'repeated_source_id'
            ELSE 'unique_source_id'
        END AS source_id_status,
        CASE
            WHEN missing_id OR source_id_count > 1 THEN
                row_number() OVER (
                    PARTITION BY unique_id
                    ORDER BY name, address_city, address_stateOrRegion,
                        address_zipOrPostcode, address_country, address_countryCode,
                        city_clean, state_clean, pin_clean, latitude, longitude, geo_status
                )
        END AS source_id_ordinal,
        CASE
            WHEN missing_pin THEN 'missing'
            WHEN pin_clean IS NULL THEN 'invalid_format'
            ELSE 'format_valid_unverified'
        END AS pin_status,
        coalesce(pin_clean IS NOT NULL AND first_pin_state <> last_pin_state, false)
            AS pin_state_disagreement,
        coalesce(country_key NOT IN ('', 'null', 'na', 'n/a', 'in', 'ind', 'india'), false)
            OR coalesce(country_code_key NOT IN ('', 'null', 'na', 'n/a', 'in', 'ind', 'india'), false)
            AS country_disagreement
    FROM signals
)
SELECT
    CASE
        -- Reserve a namespace for derived keys and preserve the raw ID separately.
        WHEN source_id_ordinal IS NOT NULL THEN concat(
            'careproof:row:', to_json(array(unique_id, cast(source_id_ordinal AS STRING)))
        )
        WHEN startswith(unique_id, 'careproof:') THEN concat('careproof:source:', unique_id)
        ELSE unique_id
    END AS facility_id,
    unique_id, source_id_status,
    name, address_city, address_stateOrRegion, address_zipOrPostcode,
    address_country, address_countryCode,
    city_clean, state_clean, cast(NULL AS STRING) AS district_clean,
    pin_clean, pin_status, latitude, longitude, geo_status,
    pin_state_disagreement, country_disagreement,
    CASE
        WHEN country_disagreement OR pin_state_disagreement THEN 'possible_admin_conflict'
        WHEN city_clean IS NULL OR state_clean IS NULL THEN 'incomplete_address'
        ELSE 'dataset_address_unverified'
    END AS admin_status,
    CASE WHEN geo_status = 'bbox_pass' AND NOT country_disagreement
        THEN latitude END AS candidate_latitude,
    CASE WHEN geo_status = 'bbox_pass' AND NOT country_disagreement
        THEN longitude END AS candidate_longitude,
    CASE WHEN geo_status = 'bbox_pass' AND NOT country_disagreement
        THEN 'dataset_coordinates_bbox_only' END AS coordinate_provenance,
    CASE WHEN city_clean IS NOT NULL OR state_clean IS NOT NULL OR pin_clean IS NOT NULL
        THEN 'dataset_address' END AS address_provenance
FROM keyed;
