# Cool RMM — Server

Receives check-ins from Cool RMM agents and serves the mobile dashboard.

## Files

| File | Purpose |
|---|---|
| `server.py` | FastAPI server — receives agent POSTs, serves API + dashboard |
| `rmm-mobile.html` | Dashboard — must live in the same folder as server.py |
| `requirements.txt` | Python dependencies |
| `devices.json` | Auto-created — persists device state across server restarts |

---

## Setup

### 1. Install dependencies
```
pip install fastapi uvicorn
```

### 2. Place files
Put `server.py` and `rmm-mobile.html` in the same folder, e.g. `C:\CoolRMM\server\`

### 3. Run
```
python server.py
```

Server starts on `http://0.0.0.0:8000`

Open the dashboard: `http://YOUR_SERVER_IP:8000`

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/checkin` | Receive agent payload |
| GET | `/api/devices` | All devices + alerts (dashboard polls this) |
| GET | `/api/device/{id}` | Single device full detail |
| GET | `/` | Serve dashboard HTML |
| GET | `/health` | Server health check |

---

## How it works

```
Windows Machine                 Your Server                  Your Phone
─────────────────               ─────────────                ──────────────
agent.py                        server.py                    rmm-mobile.html
  │                               │                               │
  ├─ collect CPU every 60s        │                               │
  ├─ collect Defender status      │                               │
  ├─ collect current user         │                               │
  ├─ collect power state          │                               │
  │                               │                               │
  └─ POST /checkin ──────────────>│                               │
                                  ├─ store in memory              │
                                  ├─ compute status/alerts        │
                                  ├─ persist to devices.json      │
                                  │                               │
                                  │<── GET /api/devices ──────────┤
                                  │─── JSON response ────────────>│
                                  │                  (every 15s)  │
```

## Alert rules (server-side)

| Condition | Severity |
|---|---|
| CPU > 85% | Warning |
| Disk > 85% | Warning |
| Disk > 90% | Critical |
| RAM > 90% | Warning |
| Defender disabled or real-time protection off | Critical |
| Defender definitions > 3 days old | Warning |
| No check-in for > 120 seconds | Offline |

---

## Run as a service (Windows)

Using NSSM:
```
nssm install CoolRMMServer
  Path: C:\Python311\python.exe
  Args: C:\CoolRMM\server\server.py
  Dir:  C:\CoolRMM\server
nssm start CoolRMMServer
```

## Accessing from your phone

1. Make sure your server machine has a static local IP (e.g. 10.0.0.5)
2. Open `http://10.0.0.5:8000` in your phone browser
3. On iPhone: Share → Add to Home Screen for a full-screen app experience
4. For remote access (outside office): put the server behind a VPN or use a reverse proxy (Tailscale is the easiest option)
