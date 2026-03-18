# Cool RMM — Windows Agent

Lightweight Python agent for Windows 10 & 11.  
Collects metrics every 60 seconds and POSTs to your Cool RMM server.

---

## What it collects

| Metric | Detail |
|---|---|
| **CPU** | Usage %, core count, clock MHz |
| **Memory** | Total GB, used % |
| **Disk** | C: total GB, used % |
| **Current User** | Interactive desktop user (not service account), domain, logged-in flag |
| **Windows Defender** | Enabled, real-time protection on/off, definition age (days), last quick/full scan age |
| **Power / On-Off** | Always online if agent posts; AC vs battery, battery %, is-laptop flag |
| **OS** | Windows version, hostname, architecture |
| **Uptime** | Seconds since last boot |

---

## Requirements

- Python 3.8+ (3.11 recommended)
- Windows 10 or 11
- PowerShell available (built-in on all Win10/11)

---

## Setup

### 1. Install Python
Download from https://python.org — check "Add Python to PATH" during install.

### 2. Install dependencies
```
pip install psutil requests
```

### 3. Configure the agent
Open `agent.py` and edit the top section:

```python
SERVER_URL    = "http://YOUR_SERVER_IP:8000/checkin"   # ← your server
POLL_INTERVAL = 60   # seconds between check-ins
```

### 4. Run manually (test it)
```
python agent.py
```
You should see log output and a successful POST to your server.

---

## Install as a Windows Service (runs on boot, no login needed)

### Option A — NSSM (recommended, free)

1. Download NSSM from https://nssm.cc
2. Open an admin command prompt and run:
```
nssm install CoolRMMAgent
```
3. In the NSSM GUI:
   - **Path**: `C:\Python311\python.exe`
   - **Arguments**: `C:\CoolRMM\agent.py`
   - **Startup directory**: `C:\CoolRMM`
4. Set the service to start automatically and start it:
```
nssm start CoolRMMAgent
```

### Option B — Task Scheduler (no extra tools)

1. Open Task Scheduler → Create Task
2. **Trigger**: At startup
3. **Action**: Start a program
   - Program: `python.exe`
   - Arguments: `C:\CoolRMM\agent.py`
4. **General**: Run whether user is logged on or not, Run with highest privileges

### Option C — SC command (advanced)

Use a wrapper `.bat` file and register with SC:
```
sc create CoolRMMAgent binPath= "cmd /c python C:\CoolRMM\agent.py" start= auto
```

---

## Sample payload sent to server

```json
{
  "agent_version": "0.1.0",
  "device_id": "a1b2c3d4-...",
  "timestamp": "2026-03-07T14:32:00Z",
  "uptime_sec": 93600,
  "os": {
    "name": "Windows",
    "version": "10.0.22631",
    "release": "11",
    "hostname": "DESKTOP-01"
  },
  "cpu": {
    "usage_pct": 42.1,
    "core_count": 8,
    "freq_mhz": 3600.0
  },
  "memory": {
    "total_gb": 16.0,
    "used_pct": 54.3
  },
  "disk": {
    "total_gb": 512.0,
    "used_pct": 61.0
  },
  "current_user": {
    "username": "jsmith",
    "domain": "WORKGROUP",
    "logged_in": true,
    "source": "wmic"
  },
  "defender": {
    "available": true,
    "enabled": true,
    "realtime_protection": true,
    "definition_age_days": 0.3,
    "last_quick_scan_days": 1,
    "last_full_scan_days": 7,
    "status_ok": true
  },
  "power": {
    "online": true,
    "power_source": "AC",
    "battery_pct": null,
    "is_laptop": false
  }
}
```

---

## Firewall note

The agent makes outbound HTTP (or HTTPS) POST requests to your server.  
- Default port: **8000**
- No inbound ports needed on the agent machine
- For HTTPS, change `SERVER_URL` to `https://...` — your server needs a valid cert or you can set `verify=False` in requests (not recommended for production)

---

## Logs

Agent logs to:
```
%TEMP%\cool_rmm_agent.log
```
Typically: `C:\Users\<user>\AppData\Local\Temp\cool_rmm_agent.log`

---

## Next steps

1. **Server** — a FastAPI endpoint that receives these POSTs, stores them, and serves the dashboard
2. **Authentication** — add a shared secret or token header so only your agents can post
3. **Alerts** — server-side rules (e.g. Defender off → alert, CPU > 85% for 5min → alert)
