# CareerFlow Local Job Discovery & Application Management Dashboard

A local-first, privacy-respecting dashboard for configurable career discovery, candidate profile alignment, manual vacancy verification, and pipeline tracking.

---

## Architecture & Security Principles

- **Local Loopback Only:** Binds exclusively to `127.0.0.1` (never exposed to public interfaces or `0.0.0.0`).
- **No Autonomous Submissions:** CareerFlow will **never** automatically submit applications, message recruiters, or contact external parties.
- **Strict Quality Safeguards:** Vacancies are never marked `open` or verified without explicit supporting evidence (minimum character constraints and verifiable ATS links).
- **Atomic Persistence & Backup Resilience:** All writes to `seen_jobs.json` and `job_search_tracker.csv` use atomic temporary file replacement with fsync and automatic `.bak` backups to prevent data corruption.
- **Thread Safety:** Mutation endpoints utilize re-entrant synchronization locks (`threading.RLock()`) to guarantee consistency across concurrent requests.
- **Rate-Limited Discovery:** On-demand live search requests enforce rate limits and parameterized subprocess arguments (`shell=False`) to avoid command injection and protect API quotas.

---

## Environment Dependencies

| Component | Minimum Version | Purpose |
| :--- | :--- | :--- |
| **Python** | 3.10+ | FastAPI backend server and relevance engine |
| **FastAPI** | 0.100.0+ | REST API framework |
| **Uvicorn** | 0.20.0+ | ASGI web server |
| **Pydantic** | 2.0.0+ | Request and data validation |
| **Bun** (Optional) | 1.0.0+ | Running LinkedIn and FreeHire CLI search tools |

---

## PowerShell Startup Instructions (Windows)

### 1. Open PowerShell and Navigate to the Repository Root

```powershell
Set-Location "d:\Simpson\Antigravity\AI Job Search\ai-job-search-personal"
```

### 2. Verify Python Installation

```powershell
python --version
# Output: Python 3.10+ (or Python 3.14)
```

### 3. Install Python Dependencies (if not already installed)

```powershell
pip install fastapi uvicorn pydantic
```

*(Optional: If using a virtual environment)*
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi uvicorn pydantic pytest
```

### 4. (Optional) Install Portal CLIs for Live Search

If you plan to use on-demand search via LinkedIn or FreeHire from the dashboard:

```powershell
# Verify Bun is available
bun --version

# Install CLI dependencies
Set-Location ".agents\skills\linkedin-search\cli"
bun install
Set-Location "..\..\..\.."

Set-Location ".agents\skills\freehire-search\cli"
bun install
Set-Location "..\..\..\.."
```

### 5. Launch the CareerFlow Server

You can run the server directly using Python module invocation:

```powershell
python -m dashboard.server
```

Or run via Uvicorn:

```powershell
uvicorn dashboard.server:app --host 127.0.0.1 --port 8000
```

### 6. Access the Dashboard in Your Browser

Open your browser and navigate to:
```
http://127.0.0.1:8000
```

---

## Running the Automated Test Suite

To run all automated dashboard API and hardening tests:

```powershell
python -m pytest tests/test_dashboard_api.py -v
```

To run all generalized matching matrix and relevance engine tests:

```powershell
python -m pytest tests/test_generalized_engine.py tests/test_relevance_engine.py -v
```

To run the entire repository test suite:

```powershell
python -m pytest
```
