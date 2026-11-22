# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Find the right care (notebook version)
# MAGIC Pick a service and type a location (city, district, 6-digit PIN, or `lat, lon`), then **Run all**.
# MAGIC Every recommendation shows the record text it is based on, cautions about the record, and the exact score arithmetic.
# MAGIC The Databricks App (`app/`) offers the same logic with a friendlier UI.

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

dbutils.widgets.dropdown("service", "dialysis", [s.id for s in services.SERVICES], "Service")
dbutils.widgets.text("location", "Jaipur", "Location (city / district / PIN / lat, lon)")
dbutils.widgets.dropdown("radius_km", "50", ["10", "25", "50", "100", "200"], "Radius (km)")
dbutils.widgets.dropdown("top_k", "5", ["3", "5", "10"], "Results")
dbutils.widgets.text("location_choice", "", "If ambiguous: option number (0,1,2…)")

# COMMAND ----------

TABLES = ["gold_facilities", "gold_facility_service_evidence", "gold_pincode_lookup", "gold_district_lookup",
          "gold_place_index", "gold_district_nfhs_map", "silver_nfhs_long", "gold_evidence_calibration"]
OPTIONAL_TABLES = ["gold_ai_claim_checks"]
_t = {t: read_pdf(t) for t in TABLES}
for _o in OPTIONAL_TABLES:
    try:
        _t[_o] = read_pdf(_o)
    except Exception:
        pass
rt = pipeline.Runtime.from_tables(_t)
print(f"{len(rt.facilities):,} facilities loaded")

# COMMAND ----------

svc = services.SERVICES_BY_ID[dbutils.widgets.get("service")]
choice = dbutils.widgets.get("location_choice").strip()
loc = geo.resolve_location(dbutils.widgets.get("location"), rt.pin_lookup, rt.district_lookup, rt.place_index,
                           choice=int(choice) if choice.isdigit() else None)
if not loc.ok:
    displayHTML(f"<h3 style='color:#a40e26'>{loc.error}</h3>")
else:
    if len(loc.alternatives) > 1:
        print("Other places with this name (set 'location_choice'):")
        for i, a in enumerate(loc.alternatives):
            print(f"  {i}: {a['display']} [{a['kind']}]")
    res = ranking.recommend(rt, svc, loc, radius_km=float(dbutils.widgets.get("radius_km")), top_k=int(dbutils.widgets.get("top_k")))
    ctx = nfhs.district_context(rt.nfhs_long, rt.district_nfhs_map, loc.state_norm, loc.district_norm, svc.nfhs, service_id=svc.id)
    displayHTML(render.result_html(res, ctx))

# COMMAND ----------

# MAGIC %md ### Map

# COMMAND ----------

if loc.ok:
    pts = pd.DataFrame([{"name": r["name"], "lat": r["lat"], "lon": r["lon"], "level": r["evidence_level"], "rank": r["rank"]}
                        for r in res.recommendations + res.leads])
    pts = pd.concat([pts, pd.DataFrame([{"name": "Search location", "lat": loc.lat, "lon": loc.lon, "level": "you", "rank": 0}])])
    display(spark.createDataFrame(pts))  # use the "Map" visualisation (lat/lon) in the results pane
