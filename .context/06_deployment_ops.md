# Deployment & Operations Guide

## 1. Overview & Architecture

Play Engine Cloud Stremio Addon is designed as a portable, stateless, containerized streaming server. It can be deployed on local Windows/Linux workstations, containerized environments (Docker, Podman, Kubernetes), and cloud application platforms (Render, Hugging Face Spaces, Koyeb).

```
+-----------------------------------------------------------------------------------+
|                        DEPLOYMENT TOPOLOGY & ENTRYPOINTS                          |
+-----------------------------------------------------------------------------------+
| 1. Docker / OCI Container | Standardized python:3.11-slim, Port 7860, curl-cffi   |
| 2. Render Cloud           | render.yaml blueprint, Python 3.11.0 runtime, $PORT   |
| 3. Local Development      | start.ps1 PowerShell launcher, auto-kill, hot reload  |
| 4. Free-Tier Keepalive    | GET /health polling every 14 min via UptimeRobot/cron  |
| 5. Database Layer         | Multi-path resolve_db_path(), SQLite WAL, 64MB cache  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Docker Container Deployment (`Dockerfile`)

The container build produces an optimized, non-root ready Python 3.11 Linux image pre-configured for both local runs and cloud hosts like Hugging Face Spaces.

### 2.1 Dockerfile Specification

```dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=7860

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose standard port (7860 for Hugging Face Spaces / 10000 for Render)
EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
```

### 2.2 System Packages & Dependencies
- **Base Image**: `python:3.11-slim` ensures minimal image footprint (~180MB base) with full compatibility for C-extension wheels.
- **Debian Packages**:
  - `build-essential`: Required for compiling C extensions if pre-built wheels are unavailable.
  - `curl`: Used for health-check probes and network debugging.
  - `sqlite3`: Provides CLI utilities for database inspection and WAL file maintenance.
- **Python Dependencies (`requirements.txt`)**:
  - `fastapi>=0.115.0`: Core ASGI application framework.
  - `uvicorn[standard]>=0.30.0`: Production ASGI server with `uvloop` and `httptools`.
  - `httpx[http2]>=0.27.0`: High-performance asynchronous HTTP client with HTTP/2 support.
  - `beautifulsoup4>=4.12.0` & `lxml>=5.2.0`: C-accelerated HTML DOM parsing for gateway scrapers.
  - `pydantic>=2.8.0`: Data validation and schema enforcement.
  - `curl-cffi>=0.7.0`: TLS fingerprint spoofing engine for Cloudflare Turnstile resilience.

### 2.3 Build and Execution Commands

```bash
# Build Docker image
docker build -t play-engine-stremio:latest -f Dockerfile .

# Run container locally on port 7860
docker run -d \
  --name play-engine \
  -p 7860:7860 \
  --restart unless-stopped \
  play-engine-stremio:latest

# Verify running container status
curl http://localhost:7860/health
```

---

## 3. Render Cloud Deployment (`render.yaml`)

Play Engine provides a declarative infrastructure-as-code configuration for zero-touch deployment on Render's web services.

### 3.1 `render.yaml` Configuration

```yaml
services:
  - type: web
    name: play-engine-stremio
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    plan: free
    autoDeploy: true
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.0
```

### 3.2 Deployment Parameters
- **Service Name**: `play-engine-stremio`
- **Runtime**: `python` with `PYTHON_VERSION=3.11.0` pinned in environment variables.
- **Build Command**: `pip install -r requirements.txt` builds all dependencies into the execution slug.
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT` binds dynamically to the dynamic `$PORT` environment variable injected by Render (typically port 10000).
- **Plan**: `free` (512MB RAM, 0.1 CPU).
- **Auto-Deploy**: `true` automatically initiates a rolling build whenever changes are pushed to the connected GitHub repository branch.

---

## 4. Local Development Launcher (`start.ps1`)

For local Windows workstations, `start.ps1` provides an automated, one-click launcher handling virtual environments, dependencies, port occupancy conflicts, VLC player detection, and browser automation.

### 4.1 Script Parameters

```powershell
.\start.ps1 [-Port <int>] [-HostAddress <string>] [-NoBrowser] [-Reload]
```

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `-Port` | `7860` | Port to bind the server on |
| `-HostAddress` | `0.0.0.0` | Host address to bind on (allows LAN streaming to Android/Google TV) |
| `-NoBrowser` | `False` | Suppresses automatic launching of the default browser |
| `-Reload` | `False` | Enables Uvicorn live code reloading for development |

### 4.2 Launcher Lifecycle Steps

```
[1/4] Checking Python Environment
      - Verifies `python` or `py` in PATH (requires Python 3.10+)
      - Auto-detects and activates virtual environments (`.venv` or `venv`)
        & "$PSScriptRoot\.venv\Scripts\Activate.ps1"

[2/4] Verifying Required Packages
      - Probes imports: fastapi, uvicorn, httpx, bs4, lxml, pydantic
      - Automatically runs `pip install -r requirements.txt` if any package is missing

[3/4] Checking VLC Media Player
      - Scans default paths:
        * C:\Program Files\VideoLAN\VLC\vlc.exe
        * C:\Program Files (x86)\VideoLAN\VLC\vlc.exe
      - Enables direct desktop playback testing if binary is present

[4/4] Checking Port 7860 Availability
      - Queries active TCP listeners via [System.Net.NetworkInformation.IPGlobalProperties]
      - Automatically finds and terminates conflicting PIDs:
        Get-CimInstance MSFT_NetTCPConnection -Filter "LocalPort = 7860" | Stop-Process -Force
      - Ensures zero port-conflict startup errors

[Background Job] Browser Launcher
      - Spawns background PowerShell job polling 127.0.0.1:7860 via TcpClient
      - Launches default browser to http://localhost:7860/ immediately upon socket connection

[Execution] Server Startup
      - Executes: python -m uvicorn main:app --host 0.0.0.0 --port 7860 [--reload]
```

---

## 5. Cloud Free-Tier Keepalive Strategy

### 5.1 The Inactivity Problem
Free cloud tiers (Render, Koyeb, Glitch) automatically put web containers into a sleeping/suspended state after **15 minutes** of HTTP inactivity. When a consumer requests a stream in Stremio while the service is asleep:
- The initial cold-start latency ranges between **30 to 50 seconds**.
- Stremio's stream discovery timeout (typically 10–15 seconds) fires before the container finishes waking up, resulting in a false `"No streams found"` error.

### 5.2 The `/health` Keepalive Solution
Play Engine provides a dedicated, zero-cost keepalive endpoint at `GET /health` in `main.py`:

```python
@app.get("/health")
async def health_check():
    """Keepalive ping for free cloud tiers."""
    return JSONResponse({
        "status": "healthy",
        "service": "play_engine_stremio_addon",
        "timestamp": int(time.time()),
        "uptime": "online"
    })
```

- **Execution Cost**: Serves from memory in `< 2ms` with zero database or scraper load.
- **Monitoring Setup**:
  - Configure an external uptime monitor (e.g. **UptimeRobot**, **Cron-Job.org**, or a GitHub Actions schedule).
  - Target URL: `https://<your-render-app>.onrender.com/health`
  - Polling Interval: **Every 14 minutes** (840 seconds).
  - Keeps the container perpetually active in memory without consuming paid compute hours.

---

## 6. Database Resolution & Portability (`media_db.py`)

The local media catalog database (`media_index.db`) provides instant (<3ms) full-text and trigram searches without incurring network roundtrips. To support diverse deployment environments (PyInstaller single-file executables, local dev trees, Claude skills, and cloud containers), `media_db.py` implements a 5-tier resolution cascade.

### 6.1 `resolve_db_path()` Resolution Cascade

```python
def resolve_db_path() -> str:
    """Universal path resolver supporting PyInstaller bundle, dev paths, and custom locations."""
    # 1. PyInstaller one-file extraction temp directory
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass and (Path(meipass) / "media_index.db").exists():
        return str(Path(meipass) / "media_index.db")

    # 2. Frozen binary directory
    exe_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    if (exe_dir / "media_index.db").exists():
        return str(exe_dir / "media_index.db")

    # 3. Explicit local development and skill directories
    for p in [
        Path(r"D:\play skill\media_index.db"),
        Path(__file__).resolve().parent / "media_index.db",
        Path(os.path.expanduser(r"~\.claude\skills\play\tools\media_index.db"))
    ]:
        if p.exists():
            return str(p)

    # 4. Default fallback to project root
    return str(Path(__file__).resolve().parent / "media_index.db")
```

### 6.2 High-Performance SQLite Configuration
Upon opening a connection, `MediaDB._get_connection()` applies low-level SQLite PRAGMAs to maximize throughput:

```python
conn.execute("PRAGMA journal_mode = WAL;")        # Concurrent reads during writes
conn.execute("PRAGMA synchronous = NORMAL;")       # Reduces disk sync frequency in WAL mode
conn.execute("PRAGMA cache_size = -64000;")        # 64MB dedicated in-memory page cache
conn.execute("PRAGMA temp_store = MEMORY;")        # In-memory storage for temp tables & sorts
conn.execute("PRAGMA mmap_size = 268435456;")      # 256MB memory-mapped I/O
```

- **FTS5 Trigram Tokenizer**: Virtual table `fts_titles` uses `tokenize='trigram'` to deliver instant substring and typo-tolerant search across titles, formatted titles, and genres.
- **Latency Outcome**: Query execution averages `< 3ms` for catalog lookups.

---

## 7. Deployment Exclusions & Security Cleanliness

To prevent credential leaks, session hijacking, and image bloat, specific runtime files must never be committed to source control or copied into Docker images.

### 7.1 Sensitive Files to Exclude

| File / Pattern | Risk Level | Reason for Exclusion |
| :--- | :---: | :--- |
| `session_vault.json` | **CRITICAL** | Contains plaintext user tokens, API keys, and session secrets |
| `olamovies_session_cookies.json` | **HIGH** | Active browser cookies and session IDs for OlaMovies |
| `tg_session*` | **HIGH** | Telethon / Telegram user session authentication keys |
| `*.log` (`server_stdout.log`, `server_stderr.log`) | **LOW** | Ephemeral process logs that bloat deployment artifacts |
| `media_index.db-wal`, `media_index.db-shm` | **MEDIUM** | SQLite temporary WAL and shared-memory files (corrupt if copied while DB is active) |

### 7.2 Recommended `.dockerignore` / `.gitignore` Additions

```gitignore
# Credentials and Session Vaults
session_vault.json
olamovies_session_cookies.json
tg_session*
*.session

# Process Logs
*.log
server_stdout.log
server_stderr.log

# SQLite Temporary State
*.db-wal
*.db-shm

# Python Cache & Environments
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
```
