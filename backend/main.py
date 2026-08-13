"""FastAPI backend for the air-gapped NOC copilot (production-hardened).

Adds over the MVP: central config, structured logging, SQLite persistence of
alerts, API-key auth, health/readiness, air-gap safety assertion at startup,
and an alert-history endpoint.

Serves:
  GET  /api/health                 liveness
  GET  /api/ready                  readiness (model + index loaded)
  GET  /api/topology               network graph
  GET  /api/risk/current           latest risk per node
  GET  /api/alerts/history         persisted alert log
  POST /api/copilot/query          copilot answer (LLM narrates ML)
  GET  /api/scenarios              demo fault scenarios
  POST /api/scenarios/inject       trigger a fault scenario
  WS   /ws/risk                    pushes risk updates periodically
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend import schemas
from core.auth import require_api_key
from core.config import settings
from core.logging_setup import configure, get_logger
from core.storage import Store
from ml.synthetic_data import generate, NODES
from ml.risk_scorer import RiskScorer
from sim.fault_injector import SCENARIOS, apply_scenario

configure(settings.log_level, settings.log_json)
log = get_logger("noc.backend")

app = FastAPI(title="Air-Gapped NOC Copilot", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_store = Store(settings.db_path)
_state = {"df": generate(minutes=40), "active_scenario": None, "scenario_t": 0}
_scorer: RiskScorer | None = None
_retriever = None


def _get_scorer() -> RiskScorer:
    global _scorer
    if _scorer is None:
        _scorer = RiskScorer(settings.model_dir)
    return _scorer


def _get_retriever():
    global _retriever
    if _retriever is None:
        from rag.retriever import Retriever
        _retriever = Retriever(settings.index_dir)
    return _retriever


@app.on_event("startup")
def _startup() -> None:
    if settings.use_ollama:
        settings.assert_airgap_safe()
    log.info("backend started", extra={"extra_fields": {"airgapped": True, "use_ollama": settings.use_ollama}})


def _advance_stream() -> pd.DataFrame:
    df = _state["df"]
    last_ts = pd.to_datetime(df["ts"]).max() + pd.Timedelta(minutes=1)
    new = generate(minutes=1)
    new["ts"] = last_ts
    if _state["active_scenario"]:
        sc = _state["active_scenario"]
        target = SCENARIOS[sc].target
        _state["scenario_t"] += 60
        mask = new["node"] == target
        for i in new[mask].index:
            row = apply_scenario(new.loc[i].to_dict(), sc, _state["scenario_t"])
            for k, v in row.items():
                new.at[i, k] = v
        if _state["scenario_t"] > SCENARIOS[sc].ramp_seconds + 120:
            _state["active_scenario"] = None
            _state["scenario_t"] = 0
    df = pd.concat([df, new]).groupby("node").tail(settings.window_minutes).reset_index(drop=True)
    _state["df"] = df
    return df


@app.get("/api/health")
def health():
    return {"status": "ok", "airgapped": True, "version": app.version}


@app.get("/api/ready")
def ready():
    try:
        _get_scorer()
        return {"ready": True}
    except Exception as e:
        return {"ready": False, "reason": str(e)}


@app.get("/api/topology")
def topology():
    roles = ["CE", "PE", "P", "PE", "CE"]
    nodes = [{"id": n, "role": r} for n, r in zip(NODES, roles)]
    links = [
        {"source": "CE1", "target": "PE1"},
        {"source": "PE1", "target": "P1"},
        {"source": "P1", "target": "PE2"},
        {"source": "PE2", "target": "CE2"},
    ]
    return {"nodes": nodes, "links": links}


@app.get("/api/risk/current", response_model=schemas.RiskResponse)
def risk_current():
    items = [a.to_dict() for a in _get_scorer().assess(_state["df"])]
    for it in items:
        if it["label"] != 0 and it["confidence"] >= 0.5:
            _store.record_alert(it["node"], it["predicted_issue"], it["confidence"], it["time_to_impact_s"])
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "items": items}


@app.get("/api/alerts/history")
def alerts_history(limit: int = 50, node: str | None = None):
    return {"alerts": _store.history(limit=limit, node=node)}


@app.get("/api/scenarios")
def scenarios():
    return {"scenarios": [{"name": s.name, "target": s.target} for s in SCENARIOS.values()]}


@app.post("/api/scenarios/inject", dependencies=[Depends(require_api_key)])
def inject(req: schemas.ScenarioRequest):
    if req.scenario_name not in SCENARIOS:
        return {"ok": False, "error": "unknown scenario"}
    _state["active_scenario"] = req.scenario_name
    _state["scenario_t"] = 0
    log.info("scenario injected", extra={"extra_fields": {"scenario": req.scenario_name}})
    return {"ok": True, "injected": req.scenario_name}


@app.post("/api/copilot/query", response_model=schemas.CopilotAnswer)
def copilot_query(q: schemas.CopilotQuery):
    from agents.graph import run_pipeline
    assessments = _get_scorer().assess(_state["df"])
    chosen = max(assessments, key=lambda a: a.risk_score)
    if q.node:
        chosen = next((a for a in assessments if a.node == q.node), chosen)
    a = chosen.to_dict()
    try:
        contexts = _get_retriever().search(a["predicted_issue"] + " " + (q.question or ""))
    except Exception as e:
        log.warning("retriever unavailable", extra={"extra_fields": {"err": str(e)}})
        contexts = []
    resp = run_pipeline(a, contexts, q.question)
    if a["label"] != 0:
        _store.record_alert(a["node"], a["predicted_issue"], a["confidence"],
                            a["time_to_impact_s"], resp.get("explanation", ""))
    return resp


@app.websocket("/ws/risk")
async def ws_risk(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            _advance_stream()
            items = [a.to_dict() for a in _get_scorer().assess(_state["df"])]
            await ws.send_json({"generated_at": datetime.now(timezone.utc).isoformat(), "items": items})
            await asyncio.sleep(settings.stream_interval_s)
    except WebSocketDisconnect:
        return
