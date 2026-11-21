# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Does it work? Validation, robustness, and a planner view
# MAGIC
# MAGIC 1. **Spot-check the evidence extractor:** random samples per level, so a human can judge precision.
# MAGIC 2. **Negation audit:** every mention we *ignored*, so false negatives are visible.
# MAGIC 3. **Benchmark queries:** the scenarios from the brief, top-3 with reasons.
# MAGIC 4. **Weight sensitivity:** does the #1 answer survive reasonable changes to the weights?
# MAGIC 5. **Planner view:** districts where NFHS-5 burden is high and well-evidenced facilities are far away.

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

TABLES = ["gold_facilities", "gold_facility_service_evidence", "gold_pincode_lookup", "gold_district_lookup",
          "gold_place_index", "gold_district_nfhs_map", "silver_nfhs_long"]
tables = {t: read_pdf(t) for t in TABLES}
rt = pipeline.Runtime.from_tables(tables)
ev_all = pd.concat([e.assign(service_id=s) for s, e in rt.evidence.items()]).reset_index()
names = rt.facilities.set_index("facility_id")["name"]

# COMMAND ----------

# MAGIC %md ## 1 · Spot-check: 8 random records per evidence level (service selectable)
# MAGIC Label each row correct / incorrect by reading the quote. Record the precision you observe in the README.

# COMMAND ----------

dbutils.widgets.dropdown("audit_service", "dialysis", [s.id for s in services.SERVICES], "Service to audit")
svc_id = dbutils.widgets.get("audit_service")
sample = (ev_all[ev_all["service_id"] == svc_id].groupby("level", group_keys=False)
          .apply(lambda g: g.sample(min(8, len(g)), random_state=7)))
sample = sample.assign(
    name=sample["facility_id"].map(names),
    quotes=sample["items"].map(lambda it: " | ".join(f"[{i['field']}] {i['text']}" for i in it[:3])),
)
display(sample[["level", "evidence_score", "name", "fields", "quotes"]].sort_values("evidence_score", ascending=False))

# COMMAND ----------

# MAGIC %md ## 2 · Negation audit — mentions excluded as "not offered / planned / referred out"

# COMMAND ----------

neg = ev_all[ev_all["negated"].map(len) > 0]
neg = neg.assign(name=neg["facility_id"].map(names), ignored=neg["negated"].map(lambda n: " | ".join(f"[{i['field']}] {i['text']}" for i in n)))
print(f"{len(neg):,} facility×service pairs had at least one ignored mention")
display(neg[["service_id", "name", "level", "ignored"]].head(50))

# COMMAND ----------

# MAGIC %md ## 3 · Benchmark queries (from the challenge brief)

# COMMAND ----------

BENCH = [
    ("dialysis", "Jaipur"), ("dialysis", "Patna"), ("emergency_csection", "Lucknow"), ("emergency_csection", "Pune"),
    ("nicu", "Patna"), ("trauma", "Agra"), ("cardiac_surgery", "Chennai"), ("mri", "Guwahati"),
    ("burn_care", "Nagpur"), ("dialysis", "Gaya"), ("nicu", "Darbhanga"), ("emergency_csection", "Bahraich"),
]
rows = []
for sid, place in BENCH:
    loc = geo.resolve_location(place, rt.pin_lookup, rt.district_lookup, rt.place_index)
    if not loc.ok:
        rows.append({"service": sid, "place": place, "rank": None, "facility": loc.error})
        continue
    res = ranking.recommend(rt, services.SERVICES_BY_ID[sid], loc, radius_km=50, top_k=3)
    if not res.recommendations:
        rows.append({"service": sid, "place": place, "rank": None, "facility": "(no well-evidenced option)",
                     "radius_km": res.searched_radius_km, "leads": len(res.leads)})
    for r in res.recommendations:
        rows.append({"service": sid, "place": place, "rank": r["rank"], "facility": r["name"], "km": r["distance_km"],
                     "level": r["evidence_level"], "trust": r["trust_score"], "score": r["score"],
                     "radius_km": res.searched_radius_km, "quote": r["citations"][0]["text"] if r["citations"] else "",
                     "n_cautions": len(r["cautions"])})
bench = pd.DataFrame(rows)
display(bench)

# COMMAND ----------

# MAGIC %md ## 4 · Weight sensitivity
# MAGIC For each benchmark query, perturb each weight by ±0.10 (renormalised) and check whether the #1 facility changes.
# MAGIC A stable #1 means the answer is driven by the data, not by our particular choice of weights.

# COMMAND ----------

base_w = dict(ranking.DEFAULT_WEIGHTS)
stab = []
for sid, place in BENCH:
    loc = geo.resolve_location(place, rt.pin_lookup, rt.district_lookup, rt.place_index)
    if not loc.ok:
        continue
    svc = services.SERVICES_BY_ID[sid]
    top = ranking.recommend(rt, svc, loc, radius_km=50, top_k=1).recommendations
    if not top:
        continue
    same, total = 0, 0
    for k in base_w:
        for d in (-0.10, 0.10):
            w = dict(base_w)
            w[k] = max(0.0, w[k] + d)
            alt = ranking.recommend(rt, svc, loc, radius_km=50, top_k=1, weights=w).recommendations
            total += 1
            same += bool(alt) and alt[0]["facility_id"] == top[0]["facility_id"]
    stab.append({"service": sid, "place": place, "top1": top[0]["name"], "top1_unchanged_%": round(100 * same / total)})
stab = pd.DataFrame(stab)
display(stab)
print(f"Mean #1 stability across perturbations: {stab['top1_unchanged_%'].mean():.0f}%")

# COMMAND ----------

# MAGIC %md ## 5 · Planner view: high need, far from well-evidenced care
# MAGIC Example: dialysis demand proxy = NFHS-5 share of adults with high blood sugar; supply = distance from the district
# MAGIC centre to the nearest facility with Strong/Moderate dialysis evidence. *Straight-line distances; NFHS-5 is 2019-21.*

# COMMAND ----------

cov = spark.table(fq("gold_district_service_coverage")).toPandas()
nl, dmap = rt.nfhs_long, rt.district_nfhs_map


def burden(keyword: str) -> pd.DataFrame:
    x = nl[nl["indicator"].str.lower().str.contains(keyword, regex=False) & nl["value"].notna()]
    x = x.groupby(["state_norm", "district_norm"])["value"].mean().rename("burden_%").reset_index()
    x = x.rename(columns={"state_norm": "nfhs_state_norm", "district_norm": "nfhs_district_norm"})
    return dmap.merge(x, on=["nfhs_state_norm", "nfhs_district_norm"], how="inner")


for sid, kw in [("dialysis", "blood sugar"), ("emergency_csection", "institutional birth")]:
    b = burden(kw)
    c = cov[cov["service_id"] == sid].merge(b, on=["state_norm", "district_norm"], how="inner")
    c["nearest_km_from_district_centre"] = pd.to_numeric(c["nearest_km_from_district_centre"])
    asc = kw == "institutional birth"  # low institutional births = high need
    c["need_rank"] = c["burden_%"].rank(ascending=asc, pct=True)
    c["gap_rank"] = c["nearest_km_from_district_centre"].rank(pct=True)
    c["priority"] = (c["need_rank"] + c["gap_rank"]) / 2
    print(f"\n== {sid}: indicator '{kw}' ({'lower = more need' if asc else 'higher = more need'}) ==")
    display(c.sort_values("priority", ascending=False)[["state", "district", "burden_%", "n_facilities_strong_or_moderate",
                                                         "nearest_km_from_district_centre", "match_method", "priority"]].head(20))
