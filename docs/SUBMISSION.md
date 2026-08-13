# Submission Reference — Air-Gapped Predictive NOC Copilot

**Hackathon:** ISRO PS13  
**Repo root:** `airgap-noc-copilot/`  
**Last updated:** 2026-07-07

This document maps each component of the repository to the four stated evaluation
dimensions, honestly flags what is a live implementation versus a synthetic or
fallback stand-in, and provides an exact command sequence for a self-contained
5-minute demo.

---

## Evaluation Dimension Map

### 1. Technical Merit

**What this repo provides:**

The system implements a two-stage inference pipeline: a gradient-boosted tree
classifier (`ml/`) detects fault *precursors* from 11 rolling telemetry features
60–90 seconds before impact, then a retrieval-augmented LLM (`rag/` + `llm/`)
narrates the prediction using internal runbooks rather than hallucinating a root
cause. The ML model exposes `predict_proba` so every alert carries a calibrated
risk score; the FastAPI backend (`backend/`) streams these scores per-node over a
WebSocket so the React dashboard updates in real time. The containerlab topology
(`sim/topology.clab.yml`) runs real FRR v8.4.0 containers with a full OSPF/LDP/
BGP-VPNv4 control plane and a Python fault injector (`sim/fault_injector.py`) that
ramps `tc netem` and `vtysh` commands over time to produce realistic precursor
sequences.

**Honest flags — synthetic / fallback stand-ins:**

- **Training data is synthetic.** `ml/synthetic_data.py` generates labelled
  telemetry using the same drift formula as `apply_scenario()`, making the classes
  trivially separable. The cross-seed macro-F1 (0.995, eval seed=42) is the
  honest metric; the same-seed score of 1.0 is expected and is *not* a claim of
  real-world accuracy. See [`docs/LIMITATIONS.md`](LIMITATIONS.md) for a full
  explanation and a four-step path to real containerlab validation.
- **LLM uses a mock fallback** when Ollama is not running (`USE_OLLAMA` unset).
  The mock returns a canned explanation string so the API and dashboard remain
  functional. Set `USE_OLLAMA=1` with a pre-pulled `llama3` or `mistral` model
  to activate the real LLM path.
- **Telegraf collector is wired but untested against live FRR containers** in
  this submission. The collector config (`telemetry/telegraf.conf`) is written to
  pull from the containerlab node sockets; validating it requires the full Linux
  + containerlab environment that is not available at the Windows dev machine
  used during development.

---

### 2. Copilot Effectiveness

**What this repo provides:**

The copilot answers operator questions by combining the ML prediction (which fault
class, which node, risk score, time-to-impact) with FAISS vector retrieval over
five purpose-written runbooks (`rag/runbooks/`) covering all four fault classes
plus a topology reference. Each runbook is 1,000–2,500 words of real NOC
documentation — terse, imperative, with exact `docker exec … vtysh` and `tc`
commands, ranked root causes, escalation criteria, and "what not to do" sections.
The RAG index holds 22 chunks (up from 3 in the original stubs); a typical
copilot query retrieves the top-3 most relevant chunks and synthesises a response
that names the specific node, cites the predicted fault class, and quotes the
relevant remediation steps.

**Honest flags:**

- **Retrieval quality is limited by embedding model size.** The system uses
  `BAAI/bge-small-en-v1.5` (33 M parameters) rather than a larger model, to stay
  within the air-gap wheelhouse size constraint. Retrieval precision on
  multi-fault queries (e.g. "why is both bgp_instability and congestion alerting
  simultaneously?") has not been systematically evaluated.
- **LLM answer quality depends on the model pulled into Ollama.** The demo uses
  the mock fallback if Ollama is not running; real copilot effectiveness requires
  a pre-pulled instruction-tuned model of at least 7 B parameters.

---

### 3. Security & Offline Compliance

**What this repo provides:**

The backend enforces offline operation at startup: `settings.assert_airgap_safe()`
checks that `OLLAMA_URL` resolves to a local or RFC-1918 address and refuses to
start if it does not. Docker Compose places the backend and Ollama on an
`internal: true` network with no egress route. `scripts/verify_airgap.sh` probes
a set of public IPs and exits non-zero if any succeed, providing a positive
pre-demo assertion. API-key authentication (`NOC_API_KEY` env var) guards all
mutating endpoints. The RAG corpus is strictly local — the FAISS index is built
from `rag/runbooks/*.md` with no external retrieval at query time. All model
weights (embedding model, LLM) are pre-downloaded by `scripts/predownload.sh`
while online and served from the local filesystem at runtime.

**Honest flags:**

- **`scripts/verify_airgap.sh` and `scripts/predownload.sh` are shell scripts
  whose network behaviour has not been tested on the demo machine** (Windows host
  with Docker Desktop). They are written for a Linux bare-metal or VM environment
  as would be used at the actual venue.
- **No mTLS or certificate pinning is implemented** between the frontend and
  backend in this submission. For a government production deployment, the backend
  should sit behind the NOC's internal reverse proxy with mTLS; this is noted in
  `docs/OPERATIONS.md` section 9 but not wired up.
- **The `NOC_API_KEY` is empty by default** (`.env.example` ships with
  `NOC_API_KEY=`). Evaluators should set a non-empty value before demo if they
  wish to verify the auth path.

---

### 4. Documentation Quality

**What this repo provides:**

Documentation is layered for different audiences:

| Document | Audience | Content |
|---|---|---|
| [`README.md`](../README.md) | Anyone | Quickstart, topology diagram, layout table, "going real" runbook |
| [`docs/OPERATIONS.md`](OPERATIONS.md) | NOC engineers / DevOps | Pre-deployment, config, run, air-gap verify, model lifecycle |
| [`docs/LIMITATIONS.md`](LIMITATIONS.md) | Evaluators / procurement | Why scores are high, what is not validated, path to production |
| [`rag/runbooks/`](../rag/runbooks/) | Copilot RAG + NOC operators | Five runbooks: 4 fault classes + topology reference, ~8,000 words total |
| [`sim/configs/`](../sim/configs/) | Network engineers | FRR `frr.conf` + `daemons` + IPSec stubs for all five nodes |
| [`docs/SUBMISSION.md`](SUBMISSION.md) | Judges | This document |

The fault-class runbooks follow real NOC documentation conventions: symptom
timelines with concrete thresholds, root causes ranked by probability, numbered
remediation steps with exact CLI commands referencing the actual container names
from the topology, escalation criteria, and "what not to do" sections. The
topology reference (`rag/runbooks/topology.md`) covers node roles, the full IP
address plan, OSPF/LDP/BGP-VPNv4 control plane, MPLS label-stack data path, and
a fault-class-to-node mapping table so the copilot can answer structural network
questions, not just fault-type questions.

**Honest flags:**

- The runbooks describe the containerlab topology, not a specific customer's
  production network. They are representative of the documentation style and depth
  that the copilot is designed to serve, not verbatim copies of classified NOC
  runbooks.
- `BUILD_PLAN.md` is referenced in `README.md` but lives in an external Notion
  page (not in this repo). The in-repo documentation is self-contained; Notion is
  supplementary planning material only.

---

## 5-Minute Demo Script

> **Prerequisites:** Python 3.10+, `pip install -r requirements.txt`, repo root
> in `PYTHONPATH`. On Windows, replace `export` with `$env:` in PowerShell.
> All commands run from the repo root. Expected total wall time: ~4–5 minutes.

---

### Step 0 — Set PYTHONPATH (once per shell session)

```bash
# Linux / macOS
export PYTHONPATH=.

# Windows PowerShell
$env:PYTHONPATH = "."
```

---

### Step 1 — Generate labelled synthetic telemetry (~10 seconds)

Generates 480 minutes of per-node telemetry with realistic fault windows
scheduled at random positions (seed 7). Writes `data/telemetry.parquet`.

```bash
python -m ml.synthetic_data --minutes 480 --seed 7 --out data/telemetry.parquet
```

Expected output:
```
wrote 2400 rows -> data/telemetry.parquet
{0: 2319, 4: 44, 3: 18, 2: 13, 1: 6}
```

The dict shows the label distribution: class 0 (nominal) dominates, with fault
classes 1–4 present in minority. This matches a realistic NOC telemetry ratio.

---

### Step 2 — Train the fault-class predictor with cross-seed evaluation (~60–90 seconds)

Trains a GradientBoosting classifier on seed-7 data, then evaluates it on an
independently generated seed-42 dataset. The cross-seed metric (not the
same-seed score) is the honest generalisation estimate.

```bash
python -m ml.train \
  --data data/telemetry.parquet \
  --out models/ \
  --eval-seed 42 \
  --eval-minutes 480
```

Expected output (abridged):
```
CV macro-F1 (5-fold): 1.000
trained sklearn-gbdt | same-seed test macro-F1: 1.000
...
[cross-seed eval] macro-F1 on seed-42 held-out set: 0.995
[summary] same-seed test macro-F1 : 1.000
[summary] cross-seed macro-F1      : 0.995  (eval seed=42, n=480 min)
saved model v_<timestamp> (sklearn-gbdt) and updated 'latest' -> models/
```

> **Note for evaluators:** the 1.0 same-seed score is *expected* (see
> [`docs/LIMITATIONS.md`](LIMITATIONS.md) §2). The cross-seed 0.995 is the
> primary metric.

---

### Step 3 — Index the runbooks for RAG retrieval (~30–60 seconds)

Embeds all five runbooks (`rag/runbooks/*.md`) using the local BGE-small model
and writes a FAISS index to `data/faiss_index/`. No internet required once the
model is cached.

```bash
python -m rag.indexer --docs rag/runbooks --out data/faiss_index
```

Expected output:
```
indexed 22 chunks using faiss+bge -> data/faiss_index
```

If `faiss` or `sentence-transformers` are not installed, it falls back to a
bag-of-words index (`bow-fallback`) that still works for retrieval.

---

### Step 4 — Start the backend (~5 seconds to ready)

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Wait for:
```
INFO:     Application startup complete.
```

Then in a second terminal, verify it is ready:
```bash
curl http://localhost:8000/api/ready
# Expected: {"status":"ok","model":"sklearn-gbdt","rag":"faiss+bge"}
```

---

### Step 5 — Open the dashboard

Open `frontend/index.html` in a browser (no build step required — it is a
single HTML file that talks to `http://localhost:8000`). You should see five
nodes (CE1, PE1, P1, PE2, CE2) with live-updating risk scores driven by the
backend's synthetic stream.

---

### Step 6 — Inject a fault scenario and watch the copilot respond (~60 seconds)

In a new terminal, inject the `congestion` scenario against PE1 in dry-run mode
(prints commands without requiring Docker):

```bash
# Dry-run: shows what tc/vtysh commands would be issued
DRY_RUN=1 FAULT_STEPS=5 python -m sim.fault_injector --scenario congestion --target PE1
```

Expected output (compressed):
```
[fault_injector] DRY-RUN injecting 'congestion' on PE1 | ramp=120s steps=5
[dry-run] docker exec clab-airgap-noc-pe1 tc qdisc replace dev eth2 root handle 1: tbf rate 10000kbit ...
[congestion t=  0s] rate=10000kbit delay=0ms loss=0.0%
...
[congestion t=120s] rate=500kbit delay=60ms loss=5.0%
[fault_injector] 'congestion' ramp complete - impairments are ACTIVE on PE1
```

While this runs, the backend's synthetic stream independently models the same
scenario. Watch PE1's risk score rise in the dashboard.

---

### Step 7 — Query the copilot about the active fault

```bash
curl -s -X POST http://localhost:8000/api/copilot \
  -H "Content-Type: application/json" \
  -d '{"question": "PE1 risk score is rising — what is likely happening and what should I check first?"}' \
  | python3 -m json.tool
```

Expected response shape:
```json
{
  "answer": "PE1 is showing elevated risk for the congestion fault class (risk score ~0.8). The most likely cause is ...",
  "sources": ["congestion.md#2", "topology.md#4"],
  "fault_class": "congestion",
  "node": "PE1",
  "risk_score": 0.82
}
```

The answer is grounded in the retrieved runbook chunks; it will cite the symptom
timeline, top root causes ranked by probability, and the first remediation step.

---

### Step 8 — Clear all injected impairments

```bash
DRY_RUN=1 python -m sim.fault_injector --scenario clear
```

Expected:
```
[fault_injector] DRY-RUN clearing all impairments
[dry-run] docker exec clab-airgap-noc-pe1 tc qdisc del dev eth2 root 2>/dev/null || true
...
[fault_injector] clear complete - all nodes back to nominal state
```

Risk scores in the dashboard should return to baseline within one streaming interval.

---

### Step 9 — Run the test suite (optional, ~30 seconds)

```bash
python -m pytest tests/test_pipeline.py -k "not retriever_and_copilot" -q
```

Expected:
```
5 passed, 1 deselected in 0.2s
```

---

## Quick Reference: What Is Real vs. What Is Synthetic/Fallback

| Component | Status | Notes |
|---|---|---|
| ML pipeline (features → train → predict) | **Real** | Runs end-to-end; artifacts in `models/` |
| Cross-seed model evaluation | **Real** | `--eval-seed 42` produces independent eval |
| FastAPI backend + WebSocket stream | **Real** | Streams synthetic telemetry if no live sim |
| RAG retriever (FAISS + BGE-small) | **Real** | 22 indexed chunks from 5 runbooks |
| Runbook content | **Real** (representative) | Written for this topology; not classified NOC docs |
| Frontend dashboard | **Real** | Single-file HTML/JS; no build step |
| LLM narration (Ollama) | **Real if `USE_OLLAMA=1`** | Mock fallback if Ollama not running |
| Fault injector — dry-run mode | **Real commands, not executed** | Remove `DRY_RUN=1` against live containerlab |
| Containerlab topology + FRR configs | **Real, untested in this env** | Requires Linux + containerlab + MPLS kernel modules |
| Training telemetry | **Synthetic** | `ml/synthetic_data.py`; see `docs/LIMITATIONS.md` |
| Telegraf live collection | **Wired, untested** | Requires live containerlab nodes |
| Air-gap verification scripts | **Wired, untested on Windows** | Written for Linux bare-metal venue |
