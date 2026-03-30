"""
server.py
─────────
Cool RMM — Server
Receives POST /checkin from agents
Serves  GET  /         → dashboard (rmm-mobile.html)
Serves  GET  /api/devices → live JSON for dashboard polling

Install : pip install fastapi uvicorn
Run     : python server.py
          (or: uvicorn server:app --host 0.0.0.0 --port 8000)
"""

import json
import time
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from collections import deque
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
HOST             = "192.168.40.157" # set to your server's LAN IP or 0.0.0.0"
PORT             = 8000
OFFLINE_AFTER    = 120        # seconds without check-in → device shown as offline
HISTORY_POINTS   = 20         # CPU history points kept per device
DATA_FILE        = "devices.json"   # simple flat-file persistence
DASHBOARD_FILE   = "rmm-mobile_v.0.1.2.html"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cool_rmm_server")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──
    load_persisted()
    log.info(f"Cool RMM Server listening on {HOST}:{PORT}")
    yield
    # ── shutdown (add cleanup here if needed) ──

app = FastAPI(title="Cool RMM Server", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# IN-MEMORY STORE
# key: device_id  →  DeviceRecord dict
# ─────────────────────────────────────────────────────────────────────────────
store: dict[str, dict] = {}


def now_ts() -> float:
    return time.time()


def load_persisted():
    """Load last-known state from disk so a server restart doesn't wipe devices."""
    if not Path(DATA_FILE).exists():
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for dev_id, rec in saved.items():
            # Restore history as deque
            rec["cpu_history"] = deque(rec.get("cpu_history", []), maxlen=HISTORY_POINTS)
            store[dev_id] = rec
        log.info(f"Loaded {len(store)} device(s) from {DATA_FILE}")
    except Exception as e:
        log.warning(f"Could not load {DATA_FILE}: {e}")


def persist():
    """Write current store to disk (best-effort)."""
    try:
        serialisable = {}
        for dev_id, rec in store.items():
            r = dict(rec)
            r["cpu_history"] = list(rec.get("cpu_history", []))
            serialisable[dev_id] = r
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(serialisable, f, indent=2, default=str)
    except Exception as e:
        log.warning(f"Persist failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def calc_status(rec: dict) -> str:
    """online / warning / offline based on last_seen and metrics."""
    age = now_ts() - rec.get("last_seen", 0)
    if age > OFFLINE_AFTER:
        return "offline"

    # Warning conditions
    cpu     = rec.get("cpu", {}).get("usage_pct", 0)
    disk    = rec.get("disk", {}).get("used_pct", 0)
    mem     = rec.get("memory", {}).get("used_pct", 0)
    bd_condition   = rec.get("defender", {}).get("condition", "running")
    snap_condition = rec.get("blackpoint_snap", {}).get("condition", "running")

    if (cpu > 85 or disk > 85 or mem > 90
            or bd_condition in ("stopped", "disabled", "not_installed",
                                "unknown_state", "check_error")
            or snap_condition in ("stopped", "disabled", "not_installed",
                                  "unknown_state", "check_error")):
        return "warning"

    return "online"


def build_alerts(rec: dict) -> list[dict]:
    """Generate alert objects from a device record."""
    alerts   = []
    hostname = rec.get("os", {}).get("hostname", rec.get("device_id", "unknown"))
    cpu      = rec.get("cpu", {}).get("usage_pct", 0)
    disk     = rec.get("disk", {}).get("used_pct", 0)
    mem      = rec.get("memory", {}).get("used_pct", 0)
    bd       = rec.get("defender", {})
    snap     = rec.get("blackpoint_snap", {})

    if cpu > 85:
        alerts.append({"sev": "warn", "device": hostname,
                        "msg": f"CPU at {cpu:.0f}%", "time": "just now"})
    if disk > 85:
        sev = "crit" if disk > 90 else "warn"
        alerts.append({"sev": sev, "device": hostname,
                        "msg": f"Disk C: at {disk:.0f}%", "time": "just now"})
    if mem > 90:
        alerts.append({"sev": "warn", "device": hostname,
                        "msg": f"RAM at {mem:.0f}%", "time": "just now"})

    # Bitdefender alerts — severity follows condition value
    bd_condition = bd.get("condition")
    if bd_condition and bd_condition != "running":
        BD_SEVERITY = {
            "disabled"      : "crit",
            "not_installed" : "crit",
            "stopped"       : "warn",
            "unknown_state" : "warn",
            "check_error"   : "warn",
        }
        BD_MESSAGES = {
            "disabled"      : "Bitdefender is DISABLED",
            "not_installed" : "Bitdefender not found on this machine",
            "stopped"       : "Bitdefender service(s) are stopped",
            "unknown_state" : "Bitdefender: services running but process missing",
            "check_error"   : "Bitdefender status could not be determined",
        }
        sev = BD_SEVERITY.get(bd_condition, "warn")
        msg = BD_MESSAGES.get(bd_condition, f"Bitdefender: {bd_condition}")
        alerts.append({"sev": sev, "device": hostname, "msg": msg, "time": "just now"})

    # Blackpoint Snap alerts
    snap_condition = snap.get("condition")
    if snap_condition and snap_condition != "running":
        SNAP_SEVERITY = {
            "disabled"      : "crit",
            "not_installed" : "crit",
            "stopped"       : "warn",
            "unknown_state" : "warn",
            "check_error"   : "warn",
        }
        SNAP_MESSAGES = {
            "disabled"      : "Blackpoint Snap is DISABLED",
            "not_installed" : "Blackpoint Snap not found on this machine",
            "stopped"       : "Blackpoint Snap service is stopped",
            "unknown_state" : "Blackpoint Snap: service running but process missing",
            "check_error"   : "Blackpoint Snap status could not be determined",
        }
        sev = SNAP_SEVERITY.get(snap_condition, "warn")
        msg = SNAP_MESSAGES.get(snap_condition, f"Blackpoint Snap: {snap_condition}")
        alerts.append({"sev": sev, "device": hostname, "msg": msg, "time": "just now"})

    return alerts


def fmt_age(seconds: float) -> str:
    if seconds < 10:   return "just now"
    if seconds < 60:   return f"{int(seconds)}s ago"
    if seconds < 3600: return f"{int(seconds/60)}m ago"
    return f"{int(seconds/3600)}h ago"


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/checkin")
async def checkin(request: Request):
    """
    Receive a check-in payload from a Cool RMM agent.
    Expected JSON matches the agent's build_payload() output.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    device_id = payload.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Missing device_id")

    # Merge into store
    existing = store.get(device_id, {})
    history  = existing.get("cpu_history", deque(maxlen=HISTORY_POINTS))
    if not isinstance(history, deque):
        history = deque(history, maxlen=HISTORY_POINTS)

    cpu_pct = payload.get("cpu", {}).get("usage_pct")
    if cpu_pct is not None:
        history.append(round(cpu_pct, 1))

    rec = {
        **payload,
        "device_id"   : device_id,
        "last_seen"   : now_ts(),
        "cpu_history" : history,
        "checkin_count": existing.get("checkin_count", 0) + 1,
    }
    store[device_id] = rec
    persist()

    hostname = payload.get("os", {}).get("hostname", device_id[:12])
    log.info(f"Check-in  {hostname:<20}  CPU={cpu_pct}%  "
             f"checkins={rec['checkin_count']}")

    return {"ok": True, "device_id": device_id, "server_time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/devices")
async def api_devices():
    """
    Return all known devices with computed status + alerts.
    The dashboard polls this endpoint every 15s.
    """
    devices_out = []
    all_alerts  = []

    for dev_id, rec in store.items():
        status   = calc_status(rec)
        age      = now_ts() - rec.get("last_seen", 0)
        alerts   = build_alerts(rec) if status != "offline" else []
        all_alerts.extend(alerts)

        defender = rec.get("defender", {})
        bd       = defender   # alias — payload key stays "defender" for compatibility
        user     = rec.get("current_user", {})
        power    = rec.get("power", {})
        cpu_obj  = rec.get("cpu", {})
        mem_obj  = rec.get("memory", {})
        disk_obj = rec.get("disk", {})
        snap     = rec.get("blackpoint_snap", {})

        devices_out.append({
            "device_id"    : dev_id,
            "hostname"     : rec.get("os", {}).get("hostname", dev_id),
            "os_version"   : rec.get("os", {}).get("version", ""),
            "os_release"   : rec.get("os", {}).get("release", ""),
            "status"       : status,
            "last_seen_age": fmt_age(age),
            "uptime_sec"   : rec.get("uptime_sec", 0),
            "cpu_pct"      : cpu_obj.get("usage_pct"),
            "cpu_cores"    : cpu_obj.get("core_count"),
            "ram_pct"      : mem_obj.get("used_pct"),
            "disk_pct"     : disk_obj.get("used_pct"),
            "cpu_history"  : list(rec.get("cpu_history", [])),
            "current_user" : {
                "username"  : user.get("username", "—"),
                "domain"    : user.get("domain", ""),
                "logged_in" : user.get("logged_in", False),
            },
            "defender": {
                "installed"  : bd.get("installed", False),
                "status_ok"  : bd.get("status_ok", False),
                "condition"  : bd.get("condition"),
            },
            "blackpoint_snap": {
                "installed"  : snap.get("installed", False),
                "status_ok"  : snap.get("status_ok", False),
                "condition"  : snap.get("condition"),
            },
            "power": {
                "source"     : power.get("power_source", "AC"),
                "battery_pct": power.get("battery_pct"),
                "is_laptop"  : power.get("is_laptop", False),
            },
            "alert_count"  : len(alerts),
        })

    # Sort: offline last, then by hostname
    devices_out.sort(key=lambda d: (d["status"] == "offline", d["hostname"]))

    online_count = sum(1 for d in devices_out if d["status"] != "offline")
    avg_cpu = None
    online_devs = [d for d in devices_out if d["status"] != "offline" and d["cpu_pct"] is not None]
    if online_devs:
        avg_cpu = round(sum(d["cpu_pct"] for d in online_devs) / len(online_devs), 1)

    return {
        "server_time"  : datetime.now(timezone.utc).isoformat(),
        "device_count" : len(devices_out),
        "online_count" : online_count,
        "alert_count"  : len(all_alerts),
        "avg_cpu"      : avg_cpu,
        "devices"      : devices_out,
        "alerts"       : all_alerts,
    }


@app.get("/api/device/{device_id}")
async def api_device_detail(device_id: str):
    """Full raw record for a single device."""
    rec = store.get(device_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Device not found")
    out = dict(rec)
    out["cpu_history"] = list(rec.get("cpu_history", []))
    out["status"] = calc_status(rec)
    return out


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the dashboard HTML file."""
    path = Path(DASHBOARD_FILE)
    if not path.exists():
        return HTMLResponse(
            "<h2>Dashboard not found</h2>"
            f"<p>Place <code>{DASHBOARD_FILE}</code> in the same folder as server.py</p>",
            status_code=404
        )
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {"ok": True, "devices": len(store), "time": datetime.now(timezone.utc).isoformat()}


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
