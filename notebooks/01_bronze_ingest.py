# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Bronze — ingest raw sources as-is
# MAGIC
# MAGIC | Source | What | Bronze table |
# MAGIC |---|---|---|
# MAGIC | Virtue Foundation facilities (Marketplace) | ~10k facilities, 51 attributes | `bronze_facilities` |
# MAGIC | India Post PIN directory (data.gov.in, GODL) | 165,627 post offices | `bronze_pincodes` |
# MAGIC | NFHS-5 district fact sheets (data.gov.in, GODL) | 706 districts × 109 cols | `bronze_nfhs` + `bronze_nfhs_columns` |
# MAGIC
# MAGIC **Before running:** install the *Virtue Foundation Dataset (DAIS 2026)* from Databricks Marketplace
# MAGIC (Marketplace → search "Virtue Foundation" → *Get instant access*). Leave the source widgets blank to
# MAGIC auto-discover, or paste a table name (`catalog.schema.table`) or a file path (`/Volumes/.../file.csv`).

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

dbutils.widgets.text("facility_source", "", "Facility table / CSV path (blank = auto)")
dbutils.widgets.text("pincode_source", "", "PIN directory table / CSV path (blank = auto)")
dbutils.widgets.text("nfhs_source", "", "NFHS-5 table / CSV path (blank = auto)")

# COMMAND ----------

# MAGIC %md ## Discover sources
# MAGIC Tables are recognised by their column *signature*, not their name, so this works whatever the
# MAGIC Marketplace catalog is called.

# COMMAND ----------

SIGNATURES = {
    "facility": lambda cols: "name" in cols and ({"capability", "procedure", "specialties"} & cols) and ({"latitude", "lat"} & cols),
    "pincode": lambda cols: {"officename", "pincode", "district"} <= cols,
    "nfhs": lambda cols: any("district" in c for c in cols) and len(cols) > 50 and "name" not in cols and "officename" not in cols,
}


def discover_tables():
    found = {}
    try:
        cols = spark.sql("""
            SELECT table_catalog, table_schema, table_name, lower(column_name) AS c
            FROM system.information_schema.columns
            WHERE table_schema <> 'information_schema' AND table_catalog NOT IN ('system', 'samples')
        """).toPandas()
    except Exception as e:  # information_schema not available: fall back to SHOW
        print("information_schema unavailable:", e)
        return found
    for (c, s, t), g in cols.groupby(["table_catalog", "table_schema", "table_name"]):
        if s == SCHEMA and c == CATALOG:
            continue  # our own outputs
        colset = set(g["c"])
        for kind, sig in SIGNATURES.items():
            if kind not in found and sig(colset):
                found[kind] = f"`{c}`.`{s}`.`{t}`"
    return found


def discover_files():
    """Marketplace listings may ship CSVs in a volume instead of tables."""
    found = {}
    try:
        vols = spark.sql("SELECT volume_catalog, volume_schema, volume_name FROM system.information_schema.volumes").collect()
    except Exception:
        return found
    for v in vols:
        base = f"/Volumes/{v.volume_catalog}/{v.volume_schema}/{v.volume_name}"
        try:
            files = dbutils.fs.ls(base)
        except Exception:
            continue
        for f in files:
            n = f.name.lower()
            if n.endswith(".csv"):
                if "pincode" in n or "pin_code" in n:
                    found.setdefault("pincode", f.path.replace("dbfs:", ""))
                elif "nfhs" in n:
                    found.setdefault("nfhs", f.path.replace("dbfs:", ""))
                elif "facilit" in n or "virtue" in n:
                    found.setdefault("facility", f.path.replace("dbfs:", ""))
    return found


sources = {
    "facility": dbutils.widgets.get("facility_source").strip(),
    "pincode": dbutils.widgets.get("pincode_source").strip(),
    "nfhs": dbutils.widgets.get("nfhs_source").strip(),
}
if not all(sources.values()):
    auto = {**discover_files(), **discover_tables()}
    for k in sources:
        sources[k] = sources[k] or auto.get(k, "")
for k, v in sources.items():
    print(f"{k:9s} -> {v or 'NOT FOUND – set the widget'}")
assert all(sources.values()), "Set the missing source widget(s) above and re-run."

# COMMAND ----------


def read_source(src: str):
    if src.startswith("/") or src.startswith("dbfs:") or src.lower().endswith(".csv"):
        return (spark.read.option("header", True).option("multiLine", True).option("escape", '"')
                .option("inferSchema", False).csv(src))
    return spark.table(src)


def safe_columns(sdf):
    """Delta forbids ' ,;{}()\\n\\t=' in column names: snake_case them and keep a mapping."""
    original = sdf.columns
    new = nfhs.snake_columns(original)
    for o, n in zip(original, new):
        if o != n:
            sdf = sdf.withColumnRenamed(o, n)
    return sdf, pd.DataFrame({"column": new, "original_name": original})


meta = lambda sdf, src: sdf.withColumn("_ingested_at", F.current_timestamp()).withColumn("_source", F.lit(src))

fac_raw = read_source(sources["facility"])
print("facility columns:", len(fac_raw.columns))
fac_raw, _ = safe_columns(fac_raw) if any(ch in "".join(fac_raw.columns) for ch in " ,;{}()=") else (fac_raw, None)
meta(fac_raw, sources["facility"]).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq("bronze_facilities"))

pin_raw = read_source(sources["pincode"])
meta(pin_raw, sources["pincode"]).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq("bronze_pincodes"))

nfhs_raw, nfhs_cols = safe_columns(read_source(sources["nfhs"]))
meta(nfhs_raw, sources["nfhs"]).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq("bronze_nfhs"))
write_pdf(nfhs_cols, "bronze_nfhs_columns", "NFHS-5 snake_case column -> original indicator name")

for t in ("bronze_facilities", "bronze_pincodes", "bronze_nfhs"):
    print(f"{t:20s} {spark.table(fq(t)).count():>8,} rows")

# COMMAND ----------

# MAGIC %md ## First look: does the facility schema resolve?
# MAGIC Every canonical field the app relies on should map to a source column. If one shows `(not found)`,
# MAGIC pass an override in notebook 02 (`OVERRIDES = {"capacity": "my_column"}`).

# COMMAND ----------

print(columns.describe(columns.resolve(spark.table(fq("bronze_facilities")).columns)))
display(spark.table(fq("bronze_facilities")).limit(5))
