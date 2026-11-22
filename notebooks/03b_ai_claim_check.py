# Databricks notebook source
# MAGIC %md
# MAGIC # 03b · AI claim checker (`ai_query`): a second reader for every claim
# MAGIC
# MAGIC Our rules decide whether a record *names* a service. An LLM (Databricks Foundation Model API, Llama 3.3 70B)
# MAGIC re-reads the record's quotes and decides whether they show that **this facility currently offers it**:
# MAGIC
# MAGIC | verdict | meaning |
# MAGIC |---|---|
# MAGIC | `offered` | the quotes clearly say this facility provides the service |
# MAGIC | `not_offered` | says it is not available / patients are referred elsewhere |
# MAGIC | `planned` | future, proposed or under construction |
# MAGIC | `other_facility` | the quote describes a different facility or branch |
# MAGIC | `unclear` | only related terms, or too vague |
# MAGIC
# MAGIC **Guard against invented quotes:** the model must copy its supporting quote verbatim; `quote_verified` is true only if
# MAGIC that text really occurs in the record. Unverified verdicts are not used.
# MAGIC
# MAGIC Scope (Free Edition token budget): record claims for dialysis, emergency C-section and NICU that contain a strong term.

# COMMAND ----------

# MAGIC %run ./00_common

# COMMAND ----------

dbutils.widgets.text("model", "databricks-meta-llama-3-3-70b-instruct", "Model endpoint")
dbutils.widgets.text("services", "dialysis,emergency_csection,nicu", "Services")
MODEL = dbutils.widgets.get("model")
SERVICES_SQL = ",".join(f"'{s.strip()}'" for s in dbutils.widgets.get("services").split(","))

# COMMAND ----------

AI_CHECK_SQL = f"""
CREATE OR REPLACE TABLE {fq('gold_ai_claim_checks')}
COMMENT 'LLM second reader (ai_query, {MODEL}) of record claims: verdict, verbatim supporting quote, quote_verified.'
AS
WITH claims AS (
  SELECT e.facility_id, e.service_id, e.level AS evidence_level, e.web_status, f.name, f.city, f.state,
         filter(from_json(e.items, 'array<struct<field:string,kind:string,term:string,text:string>>'),
                x -> x.field NOT LIKE 'website%') AS rec_items
  FROM {fq('gold_facility_service_evidence')} e
  JOIN {fq('gold_facilities')} f USING (facility_id)
  WHERE e.service_id IN ({SERVICES_SQL})
),
prompts AS (
  SELECT *,
    concat_ws('\\n', transform(slice(rec_items, 1, 5), x -> concat('- [', x.field, '] ', x.text))) AS quotes_text,
    CASE service_id WHEN 'dialysis' THEN 'dialysis (kidney)'
                    WHEN 'emergency_csection' THEN 'emergency caesarean section (C-section)'
                    WHEN 'nicu' THEN 'NICU (newborn intensive care)' ELSE service_id END AS service_label
  FROM claims
  WHERE exists(rec_items, x -> x.kind = 'strong')
),
judged AS (
  SELECT *, ai_query('{MODEL}',
    concat('You are checking one record from a hospital directory that was extracted automatically from web pages and may mix up facilities.\\n',
           'Facility: ', name, ' (', coalesce(city, ''), ', ', coalesce(state, ''), ')\\n',
           'Service asked about: ', service_label, '\\n',
           'Quotes from the record:\\n', quotes_text, '\\n\\n',
           'Do these quotes show that THIS facility currently offers the service? Choose one verdict: ',
           'offered (clearly provides it), not_offered (says not available or referred elsewhere), planned (future or proposed), ',
           'other_facility (the quote is about a different facility or branch), unclear (only related terms or too vague).\\n',
           'Reply with JSON only: {{"verdict": "...", "quote": "<the supporting quote copied exactly from above>", "reason": "<max 20 words>"}}'),
    modelParameters => named_struct('max_tokens', 200, 'temperature', 0.0)) AS raw
  FROM prompts
)
SELECT facility_id, service_id, evidence_level, web_status, name, city, state, quotes_text,
       lower(trim(p.verdict)) AS ai_verdict, p.quote AS ai_quote, p.reason AS ai_reason,
       p.quote IS NOT NULL AND length(trim(p.quote)) > 3
         AND instr(lower(quotes_text), lower(trim(p.quote))) > 0 AS quote_verified,
       raw AS ai_raw, '{MODEL}' AS model, current_timestamp() AS checked_at
FROM (SELECT *, from_json(regexp_extract(raw, '\\\\{{[\\\\s\\\\S]*\\\\}}', 0), 'verdict string, quote string, reason string') AS p FROM judged)
"""
spark.sql(AI_CHECK_SQL)
display(spark.sql(f"SELECT ai_verdict, quote_verified, count(*) n FROM {fq('gold_ai_claim_checks')} GROUP BY ALL ORDER BY n DESC"))

# COMMAND ----------

# MAGIC %md ## Three-way agreement: rules × AI reader × hospital's own website

# COMMAND ----------

display(spark.sql(f"""
SELECT evidence_level AS rule_level,
       count(*) AS claims,
       round(avg(CASE WHEN ai_verdict = 'offered' AND quote_verified THEN 1 ELSE 0 END) * 100, 1) AS pct_ai_says_offered,
       round(avg(CASE WHEN web_status = 'confirmed_own_site' THEN 1.0 WHEN web_status = 'not_found_on_site' THEN 0.0 END) * 100, 1) AS pct_website_confirmed,
       round(avg(CASE WHEN ai_verdict = 'offered' AND quote_verified AND web_status IN ('confirmed_own_site','not_found_on_site')
                      THEN CASE WHEN web_status = 'confirmed_own_site' THEN 1.0 ELSE 0.0 END END) * 100, 1) AS pct_web_confirmed_when_ai_offered,
       round(avg(CASE WHEN NOT (ai_verdict = 'offered' AND quote_verified) AND web_status IN ('confirmed_own_site','not_found_on_site')
                      THEN CASE WHEN web_status = 'confirmed_own_site' THEN 1.0 ELSE 0.0 END END) * 100, 1) AS pct_web_confirmed_when_ai_not_offered
FROM {fq('gold_ai_claim_checks')}
GROUP BY ALL ORDER BY claims DESC
"""))
