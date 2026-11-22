# Databricks notebook source
# MAGIC %md
# MAGIC # 02b · Fact-check claims against each hospital's own website
# MAGIC
# MAGIC The facility records were extracted from directory and listing pages. To get an **independent** check, we fetch each
# MAGIC facility's *official* website (homepage + up to 4 pages such as Services or Departments) and look for each service
# MAGIC there, using the same rules as the evidence extractor (strong terms, with negation handling).
# MAGIC
# MAGIC | Outcome | Meaning | Effect on ranking |
# MAGIC |---|---|---|
# MAGIC | `confirmed_own_site` | the hospital's own site names the service (quote and URL kept) | extra evidence field (weight 1.0), ✔ badge |
# MAGIC | `mentioned_group_site` | a hospital group's site (shared by 3+ facilities) names it | small extra evidence (weight 0.4) |
# MAGIC | `not_found_on_site` | site reachable, service not named | caution only (sites are often incomplete) |
# MAGIC | `site_unreachable` / `no_website` | could not check | none |
# MAGIC
# MAGIC Directory sites (Practo, Justdial, business.site …) are skipped because they are not the hospital's own voice.
# MAGIC Crawling is polite: robots.txt is respected, at most 5 pages per site, with 10 s timeouts.
# MAGIC
# MAGIC **Two ways to run it:**
# MAGIC * **A (default):** crawl on your laptop with `scripts/verify_websites.py`, upload the two parquet files to the volume
# MAGIC   below, and run this notebook to load them.
# MAGIC * **B:** set `mode = crawl` to crawl from Databricks. This needs outbound internet from serverless compute, which
# MAGIC   Free Edition may restrict.

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

dbutils.widgets.dropdown("mode", "load", ["load", "crawl"], "Mode")
dbutils.widgets.text("web_dir", f"/Volumes/{CATALOG}/{SCHEMA}/raw/web", "Folder with web_*.parquet (mode=load)")
MODE, WEB_DIR = dbutils.widgets.get("mode"), dbutils.widgets.get("web_dir").rstrip("/")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.`raw`")

# COMMAND ----------

if MODE == "load":
    status = pd.read_parquet(f"{WEB_DIR}/web_site_status.parquet")
    hits = pd.read_parquet(f"{WEB_DIR}/web_service_hits.parquet")
else:
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone

    from findcare import webcheck

    raw, _ = pipeline.quarantine_malformed(read_pdf("bronze_facilities").drop(columns=["_ingested_at", "_source"], errors="ignore"))
    fac, m = pipeline.clean_facilities(raw)
    parsed = [webcheck.site_of(v) for v in (raw[m["website"][0]] if m["website"] else [None] * len(raw))]
    sites = pd.DataFrame({"facility_id": fac["facility_id"], "url": [u for u, _ in parsed], "domain": [d for _, d in parsed]})
    shared = sites["domain"].value_counts()
    sites["site_kind"] = sites["domain"].map(lambda d: None if not isinstance(d, str) else "directory" if webcheck.is_directory(d)
                                             else "chain" if shared.get(d, 0) >= 3 else "own")
    todo = sites[sites["site_kind"].isin(["own", "chain"])].drop_duplicates("domain")
    with ThreadPoolExecutor(32) as ex:
        results = dict(zip(todo["domain"], ex.map(webcheck.fetch_site, todo["url"])))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    srows, hrows = [], []
    for s in sites.itertuples():
        c = results.get(s.domain)
        ws = ("no_website" if not isinstance(s.domain, str) else "directory_skipped" if s.site_kind == "directory"
              else "not_checked" if c is None else "ok" if c["status"] == "ok" else c["status"])
        srows.append({"facility_id": s.facility_id, "domain": s.domain, "site_kind": s.site_kind, "web_status": ws, "fetched_at": now})
        if c and c["status"] == "ok":
            hrows += [{"facility_id": s.facility_id, "site_kind": s.site_kind, "fetched_at": now, **h} for h in webcheck.find_services(c["pages"])]
    status, hits = pd.DataFrame(srows), pd.DataFrame(hrows)

write_pdf(status, "web_site_status", "Per facility: official website domain, kind (own/chain/directory) and crawl outcome.")
write_pdf(hits, "web_service_hits", "Per facility x service: quote from the hospital's own website that names the service, with page URL and fetch time.")
display(status["web_status"].value_counts().rename("facilities").reset_index())
display(hits.groupby("service_id").size().rename("confirmed_facilities").reset_index().sort_values("confirmed_facilities", ascending=False))
