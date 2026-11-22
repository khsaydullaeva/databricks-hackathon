"""Evaluate the care-finding agent with MLflow GenAI evaluation (logs to a Databricks MLflow experiment).

    DATABRICKS_CONFIG_PROFILE=<profile> python scripts/evaluate_agent.py --data local_data \\
        --experiment /Users/<you>/findcare-agent [--limit N]

Scorers: 8 deterministic checks (findcare.agent_eval.CHECKS) + an LLM judge applying plain-language /
uncertainty / no-medical-advice guidelines. Writes docs/agent_eval_results.md.
"""

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", "3")  # stay under Free Edition model rate limits
import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
from mlflow.entities import Feedback  # noqa: E402
from mlflow.genai.scorers import scorer  # noqa: E402

from findcare import agent_eval  # noqa: E402
from findcare.agent import LLM, run_agent, setup_tracing  # noqa: E402
from findcare.pipeline import Runtime  # noqa: E402


def make_check(name, fn):
    @scorer(name=name)
    def _s(inputs, outputs, expectations):
        v = fn(outputs, expectations or {})
        return None if v is None else Feedback(value=bool(v))
    return _s


def make_judge(llm):
    guide = "\n".join(f"{i + 1}. {g}" for i, g in enumerate(agent_eval.JUDGE_GUIDELINES))

    @scorer(name="judge_guidelines")
    def _judge(inputs, outputs):
        prompt = (f"You are grading an assistant that helps community health workers in India find healthcare facilities.\n"
                  f"Guidelines:\n{guide}\n\nUser request: {inputs['query']}\n\nAssistant response:\n{outputs['answer']}\n\n"
                  'Does the response follow ALL guidelines? Reply with JSON only: {"pass": true|false, "rationale": "<max 30 words>"}')
        txt = llm.chat([{"role": "user", "content": prompt}], max_tokens=150, temperature=0.0).get("content") or ""
        try:
            d = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
            return Feedback(value=bool(d.get("pass")), rationale=str(d.get("rationale", ""))[:300])
        except Exception:
            return Feedback(value=False, rationale=f"unparseable judge output: {txt[:120]}")
    return _judge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "local_data"))
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--judge-endpoint", default="databricks-llama-4-maverick")
    a = ap.parse_args()

    exp_id = setup_tracing(a.experiment)
    print("MLflow experiment:", exp_id)
    tables = {os.path.basename(f)[:-8]: pd.read_parquet(f) for f in glob.glob(os.path.join(a.data, "*.parquet"))}
    rt = Runtime.from_tables(tables)
    llm = LLM()

    def predict_fn(query: str) -> dict:
        try:
            return agent_eval.summarize_run(run_agent(rt, [{"role": "user", "content": query}], llm=llm))
        except Exception as e:  # count failures as failures instead of silently dropping the case
            return {"answer": f"ERROR: {type(e).__name__}: {str(e)[:200]}", "tool_calls": [], "searched_services": [],
                    "searched_locations": [], "search_statuses": [], "public_only": False, "n_recommended": 0,
                    "grounded": False, "grounding_issues": ["agent error"], "fallback": True, "seconds": 0, "error": True}

    data = agent_eval.to_mlflow_dataset()
    if a.limit:
        data = data[: a.limit]
    scorers = [make_check(n, f) for n, f in agent_eval.CHECKS.items()] + [make_judge(LLM(a.judge_endpoint))]
    t0 = time.time()
    with mlflow.start_run(run_name=f"agent-eval-{time.strftime('%Y%m%d-%H%M')}") as run:
        mlflow.log_params({"agent_model": llm.endpoint, "judge_model": a.judge_endpoint, "n_cases": len(data)})
        result = mlflow.genai.evaluate(data=data, predict_fn=predict_fn, scorers=scorers)
    print(f"evaluated {len(data)} cases in {time.time() - t0:.0f}s; run {run.info.run_id}")

    metrics = {k: v for k, v in result.metrics.items() if k.endswith("/mean")}
    lines = ["# Agent evaluation (MLflow GenAI evaluate)", "",
             f"- Agent model: `{llm.endpoint}` · judge model: `{a.judge_endpoint}` · {len(data)} cases · MLflow run `{run.info.run_id}` "
             f"in experiment `{a.experiment}`", "", "| Check | Pass rate |", "|---|---|"]
    for k, v in sorted(metrics.items()):
        lines.append(f"| {k.replace('/mean', '')} | {v * 100:.0f}% |")
    tbl = result.tables.get("eval_results") if hasattr(result, "tables") else None
    if tbl is not None and len(tbl):
        lines += ["", "## Per case", "", "| Request | " + " | ".join(agent_eval.CHECKS) + " | judge |", "|---" * (len(agent_eval.CHECKS) + 2) + "|"]
        for _, row in tbl.iterrows():
            req = next((row.get(c) for c in ("request", "inputs", "request_preview") if c in row and row.get(c) is not None), "")
            if isinstance(req, str):
                try:
                    req = json.loads(req)
                except Exception:
                    pass
            q = (req.get("query") or req.get("request") or req) if isinstance(req, dict) else req
            cells = []
            for n in list(agent_eval.CHECKS) + ["judge_guidelines"]:
                v = row.get(f"{n}/value")
                cells.append("✅" if v is True or v == "yes" else "❌" if v is False or v == "no" else "–")
            lines.append(f"| {str(q)[:70].replace('|', '/')} | " + " | ".join(cells) + " |")
    out = os.path.join(ROOT, "docs", "agent_eval_results.md")
    open(out, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines[:20]))
    print("wrote", out)


if __name__ == "__main__":
    main()
