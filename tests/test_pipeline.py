"""Core pipeline tests (no heavy ML deps required)."""
import pandas as pd

from ml.synthetic_data import generate, NODES
from ml.feature_engineering import build_features, feature_columns
from parsers.syslog_parser import parse_stream
from sim.fault_injector import apply_scenario, LABEL_IDS, SCENARIOS


def test_synthetic_data_shape():
    df = generate(minutes=30)
    assert len(df) == 30 * len(NODES)
    assert {"ts", "node", "label"}.issubset(df.columns)
    assert df["label"].between(0, 4).all()


def test_feature_engineering_columns():
    df = generate(minutes=20)
    feats = build_features(df)
    for col in feature_columns():
        assert col in feats.columns
    # engineered feature columns must be finite
    assert feats[feature_columns()].fillna(0).notna().all().all()


def test_fault_injector_labels_and_tti():
    base = {f: 1.0 for f in ["if_utilization", "tunnel_loss", "jitter_ms", "rekey_anomaly"]}
    row = apply_scenario(base, "mpls_degradation", elapsed_s=60)
    assert row["label"] == LABEL_IDS["mpls_degradation"]
    assert row["time_to_impact_s"] <= SCENARIOS["mpls_degradation"].ramp_seconds


def test_syslog_parser_matches():
    events = parse_stream([
        ("t", "PE1", "%BGP-5-ADJCHANGE: neighbor 10.0.0.2 Down"),
        ("t", "PE2", "Interface eth2, changed state to down"),
        ("t", "PE3", "nothing interesting here"),
    ])
    kinds = {e["kind"] for e in events}
    assert "bgp_flap" in kinds and "intf_updown" in kinds
    assert len(events) == 2


def test_risk_scorer(model_dir):
    from ml.risk_scorer import RiskScorer
    df = generate(minutes=30)
    out = RiskScorer(model_dir).assess(df)
    assert len(out) == len(NODES)
    for a in out:
        d = a.to_dict()
        assert {"node", "predicted_issue", "confidence", "risk_score"}.issubset(d)
        assert 0.0 <= d["confidence"] <= 1.0


def test_retriever_and_copilot(model_dir, index_dir):
    from rag.retriever import Retriever
    from agents.graph import run_pipeline
    from ml.risk_scorer import RiskScorer
    df = generate(minutes=30)
    a = max(RiskScorer(model_dir).assess(df), key=lambda x: x.risk_score).to_dict()
    ctx = Retriever(index_dir).search("mpls tunnel degradation")
    assert len(ctx) >= 1
    resp = run_pipeline(a, ctx, "what should I do?")
    assert {"predicted_issue", "recommended_actions", "explanation", "sources"}.issubset(resp)
