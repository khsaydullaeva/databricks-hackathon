# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Gold — trust, evidence, geography
# MAGIC
# MAGIC | Gold table | Grain | Purpose |
# MAGIC |---|---|---|
# MAGIC | `gold_facilities` | facility | cleaned record + effective location, district, **trust score & flags** |
# MAGIC | `gold_facility_service_evidence` | facility × service | **evidence score, level, cited text**, ignored (negated) mentions |
# MAGIC | `gold_pincode_lookup` | PIN | centre (trimmed median), primary district, `n_districts`, spread |
# MAGIC | `gold_district_lookup` | district | centre from post offices |
# MAGIC | `gold_place_index` | place name | city / district / post office names → coordinates (location search) |
# MAGIC | `gold_district_nfhs_map` | district | PIN-directory district → NFHS-5 district (exact / fuzzy / unmatched) |
# MAGIC | `gold_review_queue` | facility | incomplete or contradictory records set aside for later labelling |
# MAGIC | `gold_district_service_coverage` | district × service | # facilities with Strong/Moderate evidence, distance to nearest |
# MAGIC
# MAGIC See `docs/METHODOLOGY.md` for the scoring rules; the code is in `app/findcare/`.

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

silver_fac = pipeline.restore_silver_facilities(read_pdf("silver_facilities"))
silver_pins = pipeline.restore_silver_pins(read_pdf("silver_pincodes"))
nfhs_wide = read_pdf("silver_nfhs")
print(len(silver_fac), len(silver_pins), len(nfhs_wide))

def read_optional(name):
    try:
        return read_pdf(name)
    except Exception:
        print(f"(no {name} table – run 02b_web_factcheck to add website fact-checks)")
        return None


web_status, web_hits = read_optional("web_site_status"), read_optional("web_service_hits")
gold = pipeline.build_gold(silver_fac, silver_pins, nfhs_wide, web_status=web_status, web_hits=web_hits)

# COMMAND ----------

COMMENTS = {
    "gold_facilities": "One row per facility: cleaned fields, effective location (eff_lat/eff_lon + location_basis), district, trust_score in [0.1,1] and trust_flags (JSON list of {code,message,penalty}).",
    "gold_facility_service_evidence": "One row per facility x service with any mention. evidence_score in [0,1] (noisy-OR over fields), level Strong/Moderate/Limited/Indirect, items = cited source text (JSON), negated = mentions ignored because of negation/planned/referral wording.",
    "gold_pincode_lookup": "One row per 6-digit PIN: trimmed-median centre of geocoded post offices, primary district/state, n_districts (>1 = ambiguous PIN), spread_km (p90 distance of offices from centre).",
    "gold_district_lookup": "One row per (state, district) from the PIN directory with a centre point.",
    "gold_place_index": "Searchable place names (city from facility records, district, post office) with coordinates.",
    "gold_district_nfhs_map": "PIN-directory district -> NFHS-5 district. match_method: exact | fuzzy_name | unmatched | unmatched_state.",
    "gold_evidence_calibration": "Share of claims at each record-only evidence level confirmed on the hospital's own website (lower bound on precision).",
    "gold_review_queue": "Facilities to label later: review_priority (+2 per contradiction flag, +1 if no size data, +1 if own website does not mention claimed services), needs_review = priority >= 2, review_reasons (JSON list).",
    "gold_district_service_coverage": "District x service: number of facilities with Strong or Moderate evidence located in the district and straight-line km from district centre to the nearest such facility.",
}
for name, pdf in gold.items():
    write_pdf(pdf, name, COMMENTS.get(name))

# COMMAND ----------

# MAGIC %md ## Flattened evidence view (for Genie / SQL users)

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {fq('v_service_evidence')}
COMMENT 'One row per facility x service with name, location and evidence in readable columns. Use level IN (''Strong'',''Moderate'') for reliable claims.'
AS SELECT
  e.service_id, e.level AS evidence_level, e.evidence_score, e.n_fields, e.fields AS evidence_fields,
  e.web_status, e.web_url, f.needs_review,
  get_json_object(e.items, '$[0].text') AS top_quote,
  get_json_object(e.items, '$[0].field') AS top_quote_field,
  f.facility_id, f.name, f.city, f.state, f.postcode, f.district_norm AS district, f.eff_lat AS lat, f.eff_lon AS lon,
  f.location_basis, f.trust_score, f.trust_flags, f.capacity AS beds, f.num_doctors, f.phone, f.website, f.source_urls_list AS source_urls
FROM {fq('gold_facility_service_evidence')} e
JOIN {fq('gold_facilities')} f USING (facility_id)
""")
display(spark.table(fq("v_service_evidence")).where("service_id = 'dialysis'").orderBy(F.desc("evidence_score")).limit(10))

# COMMAND ----------

# MAGIC %md ## Summary

# COMMAND ----------

ev = gold["gold_facility_service_evidence"]
summary = (ev.pivot_table(index="service_id", columns="level", values="facility_id", aggfunc="count", fill_value=0)
           .reindex(columns=["Strong", "Moderate", "Limited", "Indirect", "None"], fill_value=0))
summary["negated_mentions"] = ev.groupby("service_id")["negated"].apply(lambda s: sum(len(x) for x in s))
display(summary.reset_index())

flags = gold["gold_facilities"]["trust_flags"].explode().dropna()
display(pd.DataFrame({"flag": flags.map(lambda x: x["code"])}).value_counts().rename("facilities").reset_index())
display(gold["gold_facilities"]["location_basis"].value_counts().rename("facilities").reset_index())
display(gold["gold_district_nfhs_map"]["match_method"].value_counts().rename("districts").reset_index())
