-- Delta history records the initial snapshot timestamp.
-- Keep the source column order and preserve every row, including duplicates.
CREATE TABLE IF NOT EXISTS workspace.careproof.bronze_facilities
USING DELTA
COMMENT 'Initial raw snapshot of databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities'
AS SELECT *
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities;
