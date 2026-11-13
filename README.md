<p align="center">
  <img src="https://github.com/user-attachments/assets/8b6ac69f-b998-4266-bffb-b9187ea6d5da" alt="PAIN Logo" width="180" />
</p>

<h1 align="center">PAIN</h1>

<p align="center">
  <strong>Personal AI Infrastructure &amp; Network</strong><br>
  <em>Autonomous local intelligence engine with tool-calling runtime, real-time web grounding, and persistent memory.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Runtime-FastAPI%20%2B%20Uvicorn-0284c7?style=flat-square" alt="FastAPI">
  <img src="https://img.shields.io/badge/Inference-GGUF%20%2F%20llama.cpp-8b5cf6?style=flat-square" alt="llama.cpp">
  <img src="https://img.shields.io/badge/Storage-SQLite%20FTS5-10b981?style=flat-square" alt="SQLite">
</p>

> [!CAUTION]
> **Use at your own risk.** This software is developed primarily for personal workflows and experimental research. Certain implementations and architectural choices may be considered resource-intensive, expensive, or potentially unsafe outside a trusted personal environment:
>
> - **Host & Filesystem Access**: The agent has tools to traverse, read, and OCR files across your local disk (`tools/files`). It executes with the exact permissions of the user running the server. **Never run PAIN as root.** Be aware of indirect prompt injection risks from untrusted web pages or documents.
> - **Heavy Compute & Thermals**: Running local GGUF models with GPU offloading and up to 16k context budgets generates heavy sustained load on CPU, GPU, VRAM, and RAM. Ensure adequate cooling.
> - **Autonomous Tool Execution**: The agent autonomously triggers web searches, evaluates math, queries files, and mutates persistent memory. Open-weights models can hallucinate or misinterpret prompts.
> - **Unencrypted Storage**: Chat logs, prompt personalities, and persistent memory records are saved in unencrypted SQLite (`data/pain.db`).
> - **External Queries**: While inference is strictly local, tools like `web_search` make outbound internet queries.
>
> Provided "as is" without warranty of any kind. The author assumes no liability for hardware damage, data loss, security exposure, or unintended actions.

---

## Features

- **Local GGUF Runtime**: Direct `llama-server` integration with dynamic model switching.
- **Autonomous Tool Calling**: Built-in web grounding, filesystem access, math, and OCR.
- **Persistent Memory**: SQLite FTS5 storage with live database inspection and CRUD.
- **Real-Time Telemetry**: Llama.cpp context gauge and one-click history compaction.
- **Responsive UI**: Minimal interface tuned for desktop, tablet, and mobile.

## Quickstart

### Prerequisites

- Python 3.10+
- [`llama.cpp`](https://github.com/ggerganov/llama.cpp) with `llama-server` compiled
- GGUF models placed in `models/`

### Setup & Run

```bash
# Clone and setup environment
git clone https://github.com/px7nn/PAIN.git
cd PAIN
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure paths in config.toml (point llama_dir to your llama.cpp build)
# Then start the server:
python3 main.py
```

Access the interface at **`http://localhost:8000`**.


## Remote & Mobile Access

PAIN's interface is fully responsive. To access your local model from your phone, tablet, or another machine:

- **Local Network**: Open `http://<LAN_IP>:8000` from any device on your Wi-Fi/LAN.
- **SSH Tunnel**:
  ```bash
  ssh -N -L 8000:localhost:8000 user@remote-host
  ```
- **Tunnels & Mesh VPNs**: Forward or route port `8000` using [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) (`cloudflared tunnel --url http://localhost:8000`), [Tailscale](https://tailscale.com/), or WireGuard to access your local agent from anywhere securely.
