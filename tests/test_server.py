"""API tests for the HTML/3D front-end server (synthetic data, scripted LLM: no network)."""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.dirname(__file__))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import synthetic
    from findcare import pipeline
    from findcare.nfhs import clean_nfhs

    out = tmp_path_factory.mktemp("gold")
    fac, _ = pipeline.clean_facilities(synthetic.facilities())
    pins = pipeline.clean_pincodes(synthetic.pincodes())
    wide, long, _ = clean_nfhs(synthetic.nfhs())
    gold = pipeline.build_gold(fac, pins, wide, log=lambda *_: None)
    gold["silver_nfhs_long"] = long
    for k, v in gold.items():
        pipeline.to_storable(v).to_parquet(out / f"{k}.parquet", index=False)
    os.environ["FINDCARE_LOCAL_DIR"] = str(out)
    os.environ["FINDCARE_MLFLOW"] = "0"
    import server

    server._RT = None
    return TestClient(server.app)


def test_index_and_meta(client):
    assert "Find the Right Care" in client.get("/").text
    m = client.get("/api/meta").json()
    assert len(m["services"]) == 20 and m["stats"]["facilities"] == 15 and m["cities"] and m["states"]


def test_search_modes(client):
    r = client.post("/api/search", json={"service": "dialysis", "loc": {"mode": "pin", "pin": "302001"}}).json()
    assert r["ok"] and r["recommendations"][0]["name"] == "Jaipur Kidney Care Hospital"
    assert r["recommendations"][0]["lat"] is not None and r["funnel"] and "calibration" in r
    m = client.get("/api/meta").json()
    jaipur = next(c for c in m["cities"] if c["label"] == "Jaipur, Rajasthan")
    assert client.post("/api/search", json={"service": "dialysis", "loc": {"mode": "city", "city": jaipur["i"]}}).json()["ok"]
    assert client.post("/api/search", json={"service": "dialysis", "loc": {"mode": "district", "state": "bihar", "district": "patna"}}).json()["ok"]
    assert client.post("/api/search", json={"service": "dialysis", "loc": {"mode": "point", "lat": 26.9, "lon": 75.8}}).json()["ok"]
    bad = client.post("/api/search", json={"service": "dialysis", "loc": {"mode": "pin", "pin": "999999"}}).json()
    assert not bad["ok"] and "not in the India Post directory" in bad["error"]


def test_coverage(client):
    pts = client.get("/api/coverage/dialysis").json()["points"]
    assert pts and all(len(p) == 3 for p in pts)


def test_agent_streams_sse(client, monkeypatch):
    from findcare import agent

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        def chat(self, messages, tools=None, **kw):
            self.n += 1
            if self.n == 1:
                return {"content": "", "tool_calls": [{"id": "1", "function": {"name": "search_facilities", "arguments": json.dumps(
                    {"reason": "Dialysis near Jaipur", "service": "dialysis", "location": "Jaipur"})}}]}
            top = json.loads(messages[-1]["content"])["recommendations"][0]
            return {"content": f"{top['name']}: \"{top['key_quote']}\". Please phone the facility to confirm before travelling."}

    monkeypatch.setattr(agent, "LLM", FakeLLM)
    with client.stream("POST", "/api/agent", json={"messages": [{"role": "user", "content": "dialysis Jaipur"}]}) as resp:
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert "tool" in kinds and kinds[-1] == "final"
    final = events[-1]
    assert final["grounding"]["ok"] and final["map"]["recommendations"][0]["lat"] is not None
