-- Preserve every Bronze row and field, including duplicate or missing IDs.
CREATE OR REPLACE TABLE workspace.careproof.silver_facilities
USING DELTA
AS
WITH inputs AS (
    SELECT b.*,
        transform_values(
            map(
                'uid', unique_id,
                'name', name,
                'city', address_city,
                'state', address_stateOrRegion,
                'pin', address_zipOrPostcode,
                'beds', capacity,
                'doctors', numberDoctors,
                'year', yearEstablished,
                'capability', capability,
                'procedure', procedure,
                'specialties', specialties,
                'equipment', equipment,
                'source_urls', source_urls
            ),
            (field, value) -> regexp_replace(value, r'^\s+|\s+$', '')
        ) AS silver_trimmed
    FROM workspace.careproof.bronze_facilities AS b
),
normalized AS (
    SELECT * EXCEPT (silver_trimmed),
        transform_values(silver_trimmed, (field, value) ->
            CASE WHEN lower(value) NOT IN ('', 'null', 'na', 'n/a') THEN value END
        ) AS silver_inputs
    FROM inputs
),
parsed AS (
    SELECT *,
        transform_values(
            map_filter(silver_inputs, (field, value) ->
                field IN ('capability', 'procedure', 'specialties', 'equipment', 'source_urls')
            ),
            (field, value) -> CASE
                WHEN schema_of_variant(try_parse_json(value)) LIKE 'ARRAY<%'
                THEN from_json(value, 'ARRAY<STRING>')
            END
        ) AS silver_arrays,
        transform_values(
            map_filter(silver_inputs, (field, value) -> field IN ('beds', 'doctors', 'year')),
            (field, value) -> CASE WHEN value RLIKE '^[0-9]+$' THEN try_cast(value AS INT) END
        ) AS silver_numbers,
        CASE
            WHEN latitude IS NULL OR longitude IS NULL THEN 'missing_coordinates'
            WHEN isnan(latitude) OR isnan(longitude)
                OR latitude NOT BETWEEN 6 AND 38 OR longitude NOT BETWEEN 68 AND 98
                THEN 'outside_india_bbox'
            -- Bounding-box inclusion leaves city and facility location unverified.
            ELSE 'bbox_pass'
        END AS geo_status
    FROM normalized
),
classified AS (
    SELECT * EXCEPT (silver_arrays, silver_numbers),
        regexp_replace(silver_inputs['name'], r'\s+', ' ') AS name_clean,
        lower(regexp_replace(silver_inputs['city'], r'\s+', ' ')) AS city_clean,
        lower(regexp_replace(silver_inputs['state'], r'\s+', ' ')) AS state_clean,
        CASE WHEN silver_inputs['pin'] RLIKE '^[1-9][0-9]{5}$'
            THEN silver_inputs['pin'] END AS pin_clean,
        silver_arrays['capability'] AS capability_array,
        silver_arrays['procedure'] AS procedure_array,
        silver_arrays['specialties'] AS specialties_array,
        silver_arrays['equipment'] AS equipment_array,
        silver_arrays['source_urls'] AS source_url_array,
        silver_numbers['beds'] AS beds_clean,
        silver_numbers['doctors'] AS doctors_clean,
        silver_numbers['year'] AS year_clean,
        transform_values(silver_arrays, (field, parsed_array) ->
            CASE
                WHEN silver_inputs[field] IS NULL THEN 'missing'
                WHEN parsed_array IS NULL THEN 'invalid'
                ELSE 'parsed'
            END
        ) AS array_parse_status
    FROM parsed
)
SELECT * EXCEPT (silver_inputs),
    lower(name_clean) AS name_key,
    CASE
        WHEN array_contains(map_values(array_parse_status), 'invalid') THEN 'invalid'
        WHEN array_contains(map_values(array_parse_status), 'missing') THEN 'missing'
        ELSE 'parsed'
    END AS parse_status,
    filter(array(
        CASE WHEN silver_inputs['uid'] IS NULL THEN 'missing_unique_id' END,
        CASE WHEN name_clean IS NULL THEN 'missing_name' END,
        CASE WHEN city_clean IS NULL THEN 'missing_city' END,
        CASE WHEN state_clean IS NULL THEN 'missing_state' END,
        CASE WHEN silver_inputs['pin'] IS NULL THEN 'missing_pin'
             WHEN pin_clean IS NULL THEN 'invalid_pin' END,
        CASE WHEN geo_status <> 'bbox_pass' THEN geo_status END,
        CASE WHEN isnan(latitude) OR isnan(longitude)
            OR abs(latitude) = CAST('Infinity' AS DOUBLE)
            OR abs(longitude) = CAST('Infinity' AS DOUBLE)
            THEN 'non_finite_coordinate' END,
        CASE WHEN silver_inputs['beds'] IS NOT NULL AND beds_clean IS NULL THEN 'invalid_capacity' END,
        CASE WHEN silver_inputs['doctors'] IS NOT NULL AND doctors_clean IS NULL THEN 'invalid_doctor_count' END,
        CASE WHEN silver_inputs['year'] IS NOT NULL AND year_clean IS NULL THEN 'invalid_year' END,
        CASE WHEN array_contains(map_values(array_parse_status), 'invalid') THEN 'invalid_json_array' END,
        CASE WHEN array_contains(map_values(array_parse_status), 'missing') THEN 'missing_json_array' END
    ), flag -> flag IS NOT NULL) AS quality_flags
FROM classified;
