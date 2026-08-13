# 🛰️ Air-Gapped Predictive NOC Copilot (ISRO PS13)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![FAISS RAG](https://img.shields.io/badge/RAG-FAISS%20%2B%20BGE--Small-orange.svg)](https://github.com/facebookresearch/faiss)
[![Air-Gapped](https://img.shields.io/badge/Security-100%25%20Air--Gapped-red.svg)](#-security--air-gap-compliance)

> 📌 **Evaluators:** See [`docs/SUBMISSION.md`](docs/SUBMISSION.md) for the evaluation-dimension map and the step-by-step 5-minute live demo script. See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for model evaluation metrics & validation details.

An autonomous, **fully offline, air-gapped AI Network Operations Center (NOC) Copilot** that:

1. **Predicts** MPLS & SD-WAN network faults *before* user-visible impact (precursor trend detection, not reactive threshold breaches).
2. **Explains** precursor signals in plain natural language (root cause analysis, confidence scores, affected scope, and priority remediation steps).
3. **Operates 100% Air-Gapped** — strictly zero outbound cloud calls or external dependencies at runtime.

---

## 📐 Architecture & Workflow

```
┌─────────────────┐      ┌─────────────┐      ┌─────────────────┐      ┌────────────────────┐
│ Containerlab    │ ───► │ Telemetry   │ ───► │ Feature         │ ───► │ ML Risk Engine     │
│ FRR Topology    │      │ Collector   │      │ Engineering     │      │ (GradientBoosting) │
└─────────────────┘      └─────────────┘      └─────────────────┘      └─────────┬──────────┘
                                                                                 │ (Risk & Time-to-Impact)
                                              ┌─────────────────┐                ▼
                                              │ Ollama Local    │ ───► ┌────────────────────┐
                                              │ LLM + FAISS RAG │      │ FastAPI Backend    │ ──► Real-Time React
                                              │ (Runbooks DB)   │ ───► └────────────────────┘     Dashboard
                                              └─────────────────┘
```

> 🛡️ **Core Architectural Principle:** The **ML Engine Predicts**, and the **Local LLM Narrates** those predictions over RAG-retrieved internal runbooks. The LLM never hallucinates or invents predictions.

---

## ✨ Key Features

- 🔮 **Precursor Fault Forecasting**: Detects 4 major network fault classes (`congestion`, `bgp_instability`, `mpls_degradation`, `policy_drift`) 60–90 seconds before network degradation.
- 📚 **Retrieval-Augmented Generation (RAG)**: Uses FAISS vector search over NOC internal runbooks to supply actionable, step-by-step remediation procedures with exact `vtysh` and `tc` CLI commands.
- ⚡ **Zero-Cloud Air-Gap Architecture**: Built with local embedding models (`BAAI/bge-small-en-v1.5`), local Ollama LLM, and self-contained Python pipelines.
- 🌐 **Realistic Containerlab Topology**: 5-node FRR network (`CE1 ── PE1 ── P1 ── PE2 ── CE2`) running real OSPF, LDP, iBGP VPNv4, and IPsec configuration stubs.
- 🧪 **Live Fault Injection Framework**: Ramps network impairments (`tc netem/tbf` & `vtysh clear bgp`) with full `DRY_RUN` emulation mode.
- 📊 **Real-Time Operations Dashboard**: WebSocket-driven frontend showing node health, risk trend charts, active alerts, and an interactive AI Copilot query panel.

---

## 🚀 Quickstart (Runs on any laptop without Sim/Ollama)

The repo includes a **synthetic telemetry generator** and a **mock-LLM fallback** so you can test the complete pipeline out-of-the-box on any machine.

```bash
# 1. Clone repository & create virtual environment
git clone https://github.com/Tayab-Ahamed/Airgap-noc-Copilot.git
cd Airgap-noc-Copilot
python3 -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
# .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Generate a synthetic telemetry dataset
python -m ml.synthetic_data --minutes 240 --out data/telemetry.parquet

# 4. Train the prediction engine with cross-seed evaluation
python -m ml.train --data data/telemetry.parquet --out models/ --eval-seed 42

# 5. Index the NOC runbooks for RAG
python -m rag.indexer --docs rag/runbooks --out data/faiss_index

# 6. Launch the backend server
uvicorn backend.main:app --port 8000

# 7. Open the Frontend Dashboard
# Open frontend/index.html directly in any web browser (talks to http://localhost:8000)
```

---

## 🛠️ Deploying on Live Simulation (Containerlab)

For production or air-gapped venue deployment on a Linux machine with Docker:

### 1. Enable Kernel Modules & Prerequisites
```bash
# Load MPLS router kernel modules
sudo modprobe mpls_router
sudo modprobe mpls_iptunnel

# Install Containerlab if not present
bash -c "$(curl -sL https://get.containerlab.dev)"
```

### 2. Deploy FRR Topology
```bash
cd sim/
sudo containerlab deploy -t topology.clab.yml
```

### 3. Verify Router Adjacencies & MPLS Path
```bash
# Check OSPF neighbor on PE1 (Expect FULL state to P1)
sudo docker exec -it clab-airgap-noc-pe1 vtysh -c "show ip ospf neighbor"

# Check LDP label bindings on core router P1
sudo docker exec -it clab-airgap-noc-p1 vtysh -c "show mpls ldp binding"

# Check iBGP VPNv4 status between PE1 and PE2
sudo docker exec -it clab-airgap-noc-pe1 vtysh -c "show bgp summary"

# Test customer end-to-end VPN reachability
sudo docker exec -it clab-airgap-noc-ce2 ping -c 3 10.0.0.1
```

### 4. Run Fault Injection (Dry-Run or Live)
```bash
# Dry-run mode (prints tc / vtysh commands without execution)
DRY_RUN=1 python -m sim.fault_injector --scenario congestion --target PE1

# Clear all active impairments
DRY_RUN=1 python -m sim.fault_injector --scenario clear
```

### 5. Tear Down Simulation
```bash
sudo containerlab destroy -t sim/topology.clab.yml --cleanup
```

---

## 📂 Repository Structure

```
├── backend/            # FastAPI backend, API routes, WebSocket streaming & risk scoring
├── ml/                 # Telemetry generator, feature engineering, XGBoost/GBDT classifier
├── rag/                # FAISS vector indexer, retriever, and Markdown NOC runbooks
├── llm/                # Ollama LLM integration & offline mock fallback
├── sim/                # Containerlab topology, FRR node configs, and live fault injector
├── telemetry/          # Telegraf collector configs & metric parsing
├── parsers/            # TextFSM & regex parsers for router syslog/CLI output
├── frontend/           # Single-file HTML/JS interactive real-time NOC dashboard
├── docs/               # Detailed documentation: SUBMISSION.md, LIMITATIONS.md, OPERATIONS.md
├── tests/              # Pytest automated test suite
├── requirements.txt    # Python dependencies
└── LICENSE             # MIT Open Source License
```

---

## 🔒 Security & Air-Gap Compliance

- **No Outbound Network Traffic**: The backend enforces `settings.assert_airgap_safe()` at startup and refuses non-local `OLLAMA_URL` connections.
- **Air-Gap Verification Script**: Run `bash scripts/verify_airgap.sh` at venue startup to verify zero external IP reachability.
- **Pre-download Utilities**: `scripts/predownload.sh` downloads Ollama models, HuggingFace embeddings, Docker images, and Python wheels prior to air-gapping.

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
