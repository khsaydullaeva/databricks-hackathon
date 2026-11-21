"""Build the gold tables locally (no Spark) for app development or offline checks.

    python scripts/build_local.py --synthetic                       # tiny fake data
    python scripts/build_local.py --facilities f.csv --pincodes p.csv --nfhs n.csv

Then:  FINDCARE_LOCAL_DIR=local_data streamlit run app/app.py
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import pandas as pd  # noqa: E402

from findcare import pipeline  # noqa: E402
from findcare.nfhs import clean_nfhs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--facilities")
    ap.add_argument("--pincodes")
    ap.add_argument("--nfhs")
    ap.add_argument("--out", default=os.path.join(ROOT, "local_data"))
    a = ap.parse_args()
    if a.synthetic:
        import synthetic
        fac_raw, pin_raw, nfhs_raw = synthetic.facilities(), synthetic.pincodes(), synthetic.nfhs()
    else:
        read = lambda p: pd.read_csv(p, dtype=str, keep_default_na=False)
        fac_raw, pin_raw, nfhs_raw = read(a.facilities), read(a.pincodes), read(a.nfhs)
    fac, mapping = pipeline.clean_facilities(fac_raw)
    pins = pipeline.clean_pincodes(pin_raw)
    wide, long, _ = clean_nfhs(nfhs_raw)
    gold = pipeline.build_gold(fac, pins, wide)
    gold["silver_nfhs_long"] = long
    os.makedirs(a.out, exist_ok=True)
    for name, df in gold.items():
        pipeline.to_storable(df).to_parquet(os.path.join(a.out, f"{name}.parquet"), index=False)
        print(f"{name:35s} {len(df):>8,} rows")


if __name__ == "__main__":
    main()
