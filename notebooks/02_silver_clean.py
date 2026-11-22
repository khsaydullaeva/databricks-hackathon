# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Silver — clean, normalise, and measure data quality
# MAGIC
# MAGIC * **Facilities:** canonical column names, list fields parsed (JSON / Python-list / delimited strings),
# MAGIC   PIN codes validated (6 digits, first digit 1–9; recovered from address text if the field is empty),
# MAGIC   coordinates validated against India's bounding box (0,0 rejected), implausible numbers nulled
# MAGIC   (beds 1–10,000; doctors 1–5,000; year 1800–2026), state names normalised (Orissa→Odisha …).
# MAGIC * **Malformed rows:** ~88 facility rows are fragments of broken multi-line CSV fields → `quarantine_facilities`.
# MAGIC * **Extraction leftovers** in list fields ("Cancer?", "not specified", "explicitly stated in the text") are dropped and counted.
# MAGIC * **PIN directory:** `NA` coordinates → NULL; 791 swapped lat/lon pairs repaired; placeholders like (1,1) rejected;
# MAGIC   715 rows with state/district `NA` filled from other offices of the same PIN (or state from the postal circle).
# MAGIC   Grain stays *post office* (we aggregate to PIN only in gold, with ambiguity stats).
# MAGIC * **NFHS-5:** `*` → NULL (suppressed — never zero); `(29.5)` → 29.5 with `is_low_n = true`.
# MAGIC   Stored wide (`silver_nfhs`) and long (`silver_nfhs_long`, one row per district × indicator).

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

OVERRIDES = {}  # e.g. {"capacity": "bed_count"} if a field didn't resolve in notebook 01

bronze_fac = read_pdf("bronze_facilities").drop(columns=["_ingested_at", "_source"], errors="ignore")
# ~88 rows are fragments of multi-line descriptions split by an upstream CSV error (text in the id column,
# values shifted across columns). They are kept in a quarantine table, not loaded as facilities.
good_fac, bad_fac = pipeline.quarantine_malformed(bronze_fac, OVERRIDES)
print(f"quarantined {len(bad_fac)} malformed rows of {len(bronze_fac)}")
if len(bad_fac):
    write_pdf(bad_fac.astype(str), "quarantine_facilities", "Malformed facility rows (CSV fragments) excluded from silver.")
silver_fac, mapping = pipeline.clean_facilities(good_fac, OVERRIDES)
print(columns.describe(mapping))
write_pdf(silver_fac, "silver_facilities", "Cleaned Virtue Foundation facilities (canonical columns, parsed lists).")

# COMMAND ----------

bronze_pins = read_pdf("bronze_pincodes")
silver_pins = pipeline.clean_pincodes(bronze_pins)
write_pdf(silver_pins, "silver_pincodes", "India Post PIN directory, one row per post office; geo_valid marks usable coordinates.")

# COMMAND ----------

bronze_nfhs = read_pdf("bronze_nfhs").drop(columns=["_ingested_at", "_source"], errors="ignore")
colmap = read_pdf("bronze_nfhs_columns").set_index("column")["original_name"].to_dict()
nfhs_wide, nfhs_long, _ = nfhs.clean_nfhs(bronze_nfhs, [colmap.get(c, c) for c in bronze_nfhs.columns])
write_pdf(nfhs_wide, "silver_nfhs", "NFHS-5 (2019-21) district indicators, numeric; * -> NULL.")
write_pdf(nfhs_long, "silver_nfhs_long", "NFHS-5 long format: district x indicator with is_suppressed / is_low_n flags.")

# COMMAND ----------

# MAGIC %md ## Data-quality profile
# MAGIC Field coverage should be close to the challenge brief (description 100%, capability 99.7%, procedure 92.5%,
# MAGIC equipment 77%, year 47.8%, doctors 36.4%, capacity 25.2%). Anything very different means a column didn't resolve.

# COMMAND ----------

f = silver_fac
profile = pd.DataFrame({
    "field": ["description", "capability", "procedure", "equipment", "specialties", "year_established", "num_doctors",
              "capacity", "valid coordinates", "valid postcode", "source URL"],
    "coverage_%": [
        (f["description"].str.len() > 0).mean(), f["capability_list"].map(bool).mean(), f["procedure_list"].map(bool).mean(),
        f["equipment_list"].map(bool).mean(), f["specialties_list"].map(bool).mean(), f["year_established"].notna().mean(),
        f["num_doctors"].notna().mean(), f["capacity"].notna().mean(), f["coord_valid"].mean(), f["postcode"].notna().mean(),
        f["source_urls_list"].map(bool).mean()],
})
profile["coverage_%"] = (profile["coverage_%"] * 100).round(1)
display(profile)

p = silver_pins
print(f"PIN directory: {len(p):,} post offices, {p['pincode'].nunique():,} PINs, "
      f"{p.groupby(['state_norm','district_norm']).ngroups:,} districts; "
      f"{(~p['geo_valid']).sum():,} rows without usable coordinates")
multi = p.groupby("pincode")["district_norm"].nunique()
print(f"PINs spanning >1 district: {(multi > 1).sum():,}  (a naive join on pincode would fan out these rows)")
print(f"NFHS: {len(nfhs_wide)} districts, {nfhs_long['indicator_code'].nunique()} indicators, "
      f"{nfhs_long['is_suppressed'].sum():,} suppressed values, {nfhs_long['is_low_n'].sum():,} low-sample estimates")
