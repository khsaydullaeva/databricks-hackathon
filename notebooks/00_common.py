# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Common setup (run via `%run ./00_common`)
# MAGIC Makes the shared `findcare` package importable and defines table helpers.
# MAGIC The same package powers the pipeline, the interactive notebook and the Databricks App.

# COMMAND ----------

import os
import sys

_repo_app = os.path.abspath(os.path.join(os.getcwd(), "..", "app"))
if _repo_app not in sys.path:
    sys.path.insert(0, _repo_app)

import importlib

import findcare
from findcare import columns, evidence, geo, nfhs, pipeline, quality, ranking, render, services, text

for _m in (text, columns, services, geo, evidence, quality, nfhs, pipeline, ranking, render):
    importlib.reload(_m)

import pandas as pd
from pyspark.sql import functions as F
from pyspark.sql import types as T

dbutils.widgets.text("catalog", "workspace", "Target catalog")
dbutils.widgets.text("schema", "findcare", "Target schema")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")


def fq(name: str) -> str:
    return f"`{CATALOG}`.`{SCHEMA}`.`{name}`"


def write_pdf(pdf: pd.DataFrame, name: str, comment: str | None = None):
    """pandas -> Delta. Lists become JSON strings; NaN becomes NULL."""
    sdf = spark.createDataFrame(pipeline.to_storable(pdf))
    for c, t in sdf.dtypes:
        if t in ("double", "float"):
            sdf = sdf.withColumn(c, F.when(F.isnan(F.col(c)), None).otherwise(F.col(c)))
    sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq(name))
    if comment:
        spark.sql(f"COMMENT ON TABLE {fq(name)} IS '{comment.replace(chr(39), chr(39) * 2)}'")
    print(f"wrote {fq(name)}: {sdf.count():,} rows")


def read_pdf(name: str) -> pd.DataFrame:
    return spark.table(fq(name)).toPandas()


print(f"findcare {findcare.__version__} | target {CATALOG}.{SCHEMA}")
