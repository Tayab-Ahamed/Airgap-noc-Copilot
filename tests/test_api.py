"""API smoke tests via FastAPI TestClient. Skipped if fastapi isn't installed."""
import os
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # point the app at a temp DB so tests don't pollute real data
    os.environ["NOC_DB_PATH"] = str(tmp_path_factory.mktemp("db") / "noc.db")
    # NB: requires a trained model in MODEL_DIR; risk endpoints are tested
    # separately in test_pipeline with a stand-in model.
    from backend.main import app
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_topology(client):
    r = client.get("/api/topology")
    body = r.json()
    assert r.status_code == 200
    assert len(body["nodes"]) == 5 and len(body["links"]) == 4


def test_scenarios(client):
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    assert len(r.json()["scenarios"]) == 4
