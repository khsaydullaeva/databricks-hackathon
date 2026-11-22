-- Check the exact Marketplace baseline before creating the writable schema.
WITH source_profile AS (
  SELECT COUNT(*) AS row_count
  FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
)
SELECT assert_true(
  row_count = 10088,
  concat('Expected 10088 facilities rows, observed ', CAST(row_count AS STRING))
) AS source_count_check
FROM source_profile;

-- The independent gate tests table creation and cleanup in this schema.
CREATE SCHEMA IF NOT EXISTS workspace.careproof;
