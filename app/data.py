"""Load gold tables for the app: from a SQL warehouse (on Databricks) or local parquet (dev)."""

from __future__ import annotations

import os

import pandas as pd

TABLES = ["gold_facilities", "gold_facility_service_evidence", "gold_pincode_lookup", "gold_district_lookup",
          "gold_place_index", "gold_district_nfhs_map", "silver_nfhs_long", "gold_evidence_calibration"]
OPTIONAL = {"gold_district_nfhs_map", "silver_nfhs_long", "gold_evidence_calibration"}


def load_tables() -> dict[str, pd.DataFrame]:
    local = os.getenv("FINDCARE_LOCAL_DIR")
    if local:
        out = {}
        for t in TABLES:
            p = os.path.join(local, f"{t}.parquet")
            if os.path.exists(p):
                out[t] = pd.read_parquet(p)
            elif t not in OPTIONAL:
                raise FileNotFoundError(p)
        return out
    return _load_from_warehouse()


def _load_from_warehouse() -> dict[str, pd.DataFrame]:
    from databricks import sql
    from databricks.sdk.core import Config

    cfg = Config()  # picks up the app's service-principal credentials automatically
    warehouse = os.getenv("DATABRICKS_WAREHOUSE_ID")
    if not warehouse:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set: add a SQL warehouse resource named 'sql-warehouse' to the app.")
    catalog = os.getenv("FINDCARE_CATALOG", "workspace")
    schema = os.getenv("FINDCARE_SCHEMA", "findcare")
    out = {}
    with sql.connect(server_hostname=cfg.host, http_path=f"/sql/1.0/warehouses/{warehouse}",
                     credentials_provider=lambda: cfg.authenticate) as conn:
        with conn.cursor() as cur:
            for t in TABLES:
                try:
                    cur.execute(f"SELECT * FROM `{catalog}`.`{schema}`.`{t}`")
                    out[t] = cur.fetchall_arrow().to_pandas()
                except Exception:
                    if t not in OPTIONAL:
                        raise
    return out
