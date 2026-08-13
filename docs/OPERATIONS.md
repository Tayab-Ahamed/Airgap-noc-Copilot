# Operations Guide — Air-Gapped Predictive NOC Copilot

## 1. Pre-deployment (while online)

```bash
bash scripts/predownload.sh   # Ollama models, embedding model, FRR image, pip wheelhouse
```

Verify the wheelhouse installs with no internet:
```bash
python -m venv .venv && source .venv/bin/activate
pip install --no-index --find-links wheelhouse -r requirements.txt
```

## 2. Configuration

Copy `.env.example` to `.env` and set values. Key production settings:

| Variable | Purpose |
|---|---|
| `NOC_API_KEY` | Enables API-key auth on mutating endpoints. Leave empty only in dev. |
| `USE_OLLAMA=1` | Use the local LLM; the app refuses non-local `OLLAMA_URL`. |
| `NOC_DB_PATH` | SQLite path for alert history. |
| `STREAM_INTERVAL` | Seconds between live risk pushes. |

## 3. Run

### Bare metal
```bash
export PYTHONPATH=.
python -m ml.synthetic_data --minutes 240 --out data/telemetry.parquet   # or real telemetry
python -m ml.train --data data/telemetry.parquet --out models/
python -m rag.indexer --docs rag/runbooks --out data/faiss_index
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### Docker (offline)
```bash
docker compose up --build   # backend + ollama on an internal (egress-blocked) network
```

## 4. Air-gap verification (run at startup on stage)
```bash
bash scripts/verify_airgap.sh   # confirms no outbound connectivity
```
The backend also calls `settings.assert_airgap_safe()` at startup and refuses to
run if `OLLAMA_URL` is not local/internal.

## 5. Live fault injection (real sim)
```bash
# dry-run prints the exact commands; drop DRY_RUN to execute
DRY_RUN=1 python -m sim.fault_injector --scenario mpls_degradation
DRY_RUN=1 python -m sim.fault_injector --scenario clear     # remove all impairments
```

## 6. Health & monitoring

- `GET /api/health` — liveness
- `GET /api/ready` — readiness (model + index loaded)
- `GET /api/alerts/history?limit=50` — persisted alerts for post-incident review
- Logs are structured JSON (`NOC_LOG_JSON=true`); ship to a local collector.

## 7. Model lifecycle

- Each `ml.train` run writes a versioned artifact `models/v_<timestamp>/` and
  updates the `latest` pointer used by the API.
- Metrics (CV macro-F1, per-class report, confusion matrix) are saved in each
  version's `meta.json` for auditability.

## 8. Security notes (air-gapped govt context)

- No outbound network at runtime; all inference is local.
- API-key auth on mutating endpoints; put the service behind the NOC's internal
  reverse proxy / mTLS if required.
- RAG retrieves only internal artifacts (runbooks/topology). No external corpus.
