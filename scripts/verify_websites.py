"""Crawl each facility's official website and record which services it confirms.

    python scripts/verify_websites.py --facilities facilities.parquet --out web_out/ [--workers 48] [--limit N]

Resumable: fetched sites are appended to <out>/pages.jsonl.gz and skipped on re-run.
Writes <out>/web_site_status.parquet and <out>/web_service_hits.parquet, which notebook 03 / build_local
pick up (upload them to a Unity Catalog volume to use them on Databricks).
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import pandas as pd  # noqa: E402

from findcare import pipeline, webcheck  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facilities", required=True, help="raw facility table (.parquet or .csv)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    raw = pd.read_parquet(a.facilities) if a.facilities.endswith(".parquet") else pd.read_csv(a.facilities, dtype=str)
    raw, _ = pipeline.quarantine_malformed(raw)
    fac, m = pipeline.clean_facilities(raw)
    web_col = raw[m["website"][0]] if m["website"] else pd.Series([None] * len(raw))
    sites = pd.DataFrame({"facility_id": fac["facility_id"].to_numpy(), "official_website": web_col.to_numpy()})
    parsed = [webcheck.site_of(v) for v in sites["official_website"]]
    sites["url"] = [u for u, _ in parsed]
    sites["domain"] = [d for _, d in parsed]
    shared = sites["domain"].value_counts()
    sites["domain"] = sites["domain"].astype(object).where(sites["domain"].notna(), None)
    sites["site_kind"] = sites["domain"].map(lambda d: None if not isinstance(d, str) else "directory" if webcheck.is_directory(d)
                                             else "chain" if shared.get(d, 0) >= 3 else "own")
    todo = sites[sites["site_kind"].isin(["own", "chain"])].drop_duplicates("domain")
    cache_path = os.path.join(a.out, "pages.jsonl.gz")
    done = {r["domain"] for r in webcheck.load_jsonl_gz(cache_path)}
    todo = todo[~todo["domain"].isin(done)]
    if a.limit:
        todo = todo.head(a.limit)
    print(f"{len(sites)} facilities | {sites['domain'].notna().sum()} with website | {len(done)} domains cached | {len(todo)} to fetch", flush=True)

    t0, n = time.time(), 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(webcheck.fetch_site, r.url): r for r in todo.itertuples()}
        batch = []
        for f in as_completed(futs):
            r = futs[f]
            try:
                res = f.result()
            except Exception as e:
                res = {"start_url": r.url, "status": "unreachable", "pages": [], "note": f"error:{type(e).__name__}"}
            res.update(domain=r.domain, fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            batch.append(res)
            n += 1
            if len(batch) >= 50:
                webcheck.save_jsonl_gz(cache_path, batch)
                batch = []
                print(f"  {n}/{len(todo)} sites, {time.time() - t0:.0f}s", flush=True)
        webcheck.save_jsonl_gz(cache_path, batch)

    # ---- score: which services does each site confirm? (re-runnable without re-fetching)
    cache = {r["domain"]: r for r in webcheck.load_jsonl_gz(cache_path)}
    status_rows, hit_rows = [], []
    for s in sites.itertuples():
        if not isinstance(s.domain, str):
            status_rows.append({"facility_id": s.facility_id, "domain": None, "site_kind": None, "web_status": "no_website"})
            continue
        if s.site_kind == "directory":
            status_rows.append({"facility_id": s.facility_id, "domain": s.domain, "site_kind": s.site_kind, "web_status": "directory_skipped"})
            continue
        c = cache.get(s.domain)
        if c is None:
            status_rows.append({"facility_id": s.facility_id, "domain": s.domain, "site_kind": s.site_kind, "web_status": "not_checked"})
            continue
        status_rows.append({"facility_id": s.facility_id, "domain": s.domain, "site_kind": s.site_kind,
                            "web_status": "ok" if c["status"] == "ok" else c["status"], "n_pages": len(c["pages"]),
                            "note": c.get("note", ""), "fetched_at": c["fetched_at"]})
        if c["status"] == "ok":
            for h in webcheck.find_services(c["pages"]):
                hit_rows.append({"facility_id": s.facility_id, "site_kind": s.site_kind, "fetched_at": c["fetched_at"], **h})
    st, hits = pd.DataFrame(status_rows), pd.DataFrame(hit_rows)
    st.to_parquet(os.path.join(a.out, "web_site_status.parquet"), index=False)
    hits.to_parquet(os.path.join(a.out, "web_service_hits.parquet"), index=False)
    print(st["web_status"].value_counts().to_string())
    print(f"service confirmations: {len(hits)} across {hits['facility_id'].nunique() if len(hits) else 0} facilities")


if __name__ == "__main__":
    main()
