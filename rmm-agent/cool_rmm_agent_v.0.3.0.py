"""
cool_rmm_agent.py
─────────────────
Cool RMM — Windows Agent
Supports: Windows 10 & 11
Collects : CPU %, Bitdefender Endpoint Security status, on/off (power) status,
           current user, Blackpoint Snap agent status
Reports  : HTTP POST to your Cool RMM server every POLL_INTERVAL seconds

Install deps  : pip install psutil requests
Run manually  : python cool_rmm_agent.py
Run as service: see README — use NSSM or the built-in SC command
"""

import os
import sys
import json
import time
import socket
import getpass
import platform
import subprocess
import logging
from datetime import datetime, timezone

try:
    import psutil
    import requests
except ImportError:
    print("[FATAL] Missing dependencies. Run:  pip install psutil requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  — edit these before deploying
# ─────────────────────────────────────────────────────────────────────────────
SERVER_URL     = "http://192.168.40.157:8000/checkin"   # ← change this
POLL_INTERVAL  = 60          # seconds between check-ins
AGENT_VERSION  = "0.3.0"
LOG_FILE       = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "cool_rmm_agent.log")
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("cool_rmm")


# ── COLLECTORS ────────────────────────────────────────────────────────────────

def get_device_id() -> str:
    """Stable device ID: machine hostname + Windows MachineGuid from registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography"
        )
        guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return guid
    except Exception:
        # Fallback: hostname-based ID
        return socket.gethostname().lower()


def get_cpu() -> dict:
    """CPU usage percent (1-second sample) and core count."""
    usage = psutil.cpu_percent(interval=1)
    freq  = psutil.cpu_freq()
    return {
        "usage_pct"  : usage,
        "core_count" : psutil.cpu_count(logical=True),
        "freq_mhz"   : round(freq.current, 1) if freq else None,
    }


def get_memory() -> dict:
    """RAM usage."""
    m = psutil.virtual_memory()
    return {
        "total_gb"  : round(m.total / 1e9, 2),
        "used_pct"  : m.percent,
    }


def get_disk() -> dict:
    """C: drive usage."""
    try:
        d = psutil.disk_usage("C:\\")
        return {
            "total_gb" : round(d.total / 1e9, 1),
            "used_pct" : d.percent,
        }
    except Exception as e:
        return {"error": str(e)}


def get_current_user() -> dict:
    """
    Returns the interactive desktop user (the person sitting at the machine),
    not necessarily the service account running this agent.
    Falls back to getpass if WMI query fails.
    """
    # Method 1: query Win32_ComputerSystem via WMIC (works on Win10/11, no extra deps)
    try:
        result = subprocess.run(
            ["wmic", "computersystem", "get", "UserName", "/value"],
            capture_output=True, text=True, timeout=8
        )
        for line in result.stdout.splitlines():
            if line.strip().lower().startswith("username="):
                value = line.split("=", 1)[1].strip()
                if value:
                    # Format is DOMAIN\user or just user
                    parts = value.split("\\")
                    return {
                        "username"   : parts[-1],
                        "domain"     : parts[0] if len(parts) > 1 else "",
                        "logged_in"  : True,
                        "source"     : "wmic",
                    }
    except Exception:
        pass

    # Method 2: query active sessions via query user
    try:
        result = subprocess.run(
            ["query", "user"],
            capture_output=True, text=True, timeout=8
        )
        lines = [l for l in result.stdout.splitlines() if "Active" in l]
        if lines:
            username = lines[0].split()[0].lstrip(">")
            return {
                "username"  : username,
                "domain"    : "",
                "logged_in" : True,
                "source"    : "query_user",
            }
    except Exception:
        pass

    # Method 3: process owner of explorer.exe
    try:
        for proc in psutil.process_iter(["name", "username"]):
            if proc.info["name"] and proc.info["name"].lower() == "explorer.exe":
                uname = proc.info["username"] or ""
                parts = uname.split("\\")
                return {
                    "username"  : parts[-1],
                    "domain"    : parts[0] if len(parts) > 1 else "",
                    "logged_in" : True,
                    "source"    : "explorer_proc",
                }
    except Exception:
        pass

    # Fallback: service account running this script
    return {
        "username"  : getpass.getuser(),
        "domain"    : os.environ.get("USERDOMAIN", ""),
        "logged_in" : False,
        "source"    : "fallback",
    }


def get_bitdefender_status() -> dict:
    """
    Check Bitdefender Endpoint Security health.

    Strategy (lightweight-first):
      1. psutil process scan for bdredline.exe — primary health indicator.
         If the process is running, Bitdefender is active. Skip PowerShell.
      2. PowerShell service check — only runs if the process is NOT found.
         Checks all 5 BD services and reports which ones are stopped/missing
         so the server can alert with the right severity.

    Confirmed service names (from your environment):
        EPIntegrationService  — Bitdefender Endpoint Integration Service
        EPProtectedService    — Bitdefender Endpoint Protected Service
        EPRedline             — Bitdefender Endpoint Redline Service
        EPSecurityService     — Bitdefender Endpoint Security Service
        EPUpdateService       — Bitdefender Endpoint Update Service

    Confirmed process name:
        bdredline.exe
    """

    BD_PROCESS_NAMES = {
        "bdredline.exe",
        "epprotectedservice.exe",
        "epsecurityservice.exe",
    }

    BD_SERVICE_NAMES = [
        "EPIntegrationService",
        "EPProtectedService",
        "EPRedline",
        "EPSecurityService",
        "EPUpdateService",
    ]

    # ── STEP 1: psutil process scan (primary, free) ───────────────────────────
    process_found = False
    process_info  = {"found": False, "name": None, "pid": None}

    try:
        for proc in psutil.process_iter(["name", "pid"]):
            pname = (proc.info["name"] or "").lower()
            if pname in BD_PROCESS_NAMES:
                process_found = True
                process_info  = {
                    "found" : True,
                    "name"  : proc.info["name"],
                    "pid"   : proc.info["pid"],
                }
                break
    except Exception as e:
        process_info["error"] = str(e)

    # Process alive — Bitdefender is healthy, skip PowerShell entirely
    if process_found:
        return {
            "installed"  : True,
            "status_ok"  : True,
            "condition"  : "running",
            "process"    : process_info,
            "services"   : {"checked": False, "reason": "not needed — process found"},
        }

    # ── STEP 2: Process not found — check all 5 services via PowerShell ───────
    ps_script = """
    $names = @('EPIntegrationService','EPProtectedService','EPRedline','EPSecurityService','EPUpdateService')
    $results = foreach ($n in $names) {
        $svc = Get-Service -Name $n -ErrorAction SilentlyContinue
        if ($svc) {
            [PSCustomObject]@{
                name       = $svc.Name
                display    = $svc.DisplayName
                state      = $svc.Status.ToString()
                start_type = $svc.StartType.ToString()
                found      = $true
            }
        } else {
            [PSCustomObject]@{
                name       = $n
                display    = $null
                state      = $null
                start_type = $null
                found      = $false
            }
        }
    }
    $results | ConvertTo-Json
    """

    services_info  = []
    condition      = "not_installed"

    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15
        )
        raw = result.stdout.strip()

        # PowerShell returns a single object instead of array when only 1 result
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]

        found_any  = False
        stopped    = []
        disabled   = []
        missing    = []

        for svc in data:
            services_info.append(svc)
            if not svc.get("found"):
                missing.append(svc["name"])
                continue
            found_any = True
            state      = (svc.get("state") or "").lower()
            start_type = (svc.get("start_type") or "").lower()
            if start_type == "disabled":
                disabled.append(svc["name"])
            elif state != "running":
                stopped.append(svc["name"])

        if not found_any:
            condition = "not_installed"
        elif disabled:
            condition = "disabled"       # at least one service deliberately disabled → critical
        elif stopped or missing:
            condition = "stopped"        # at least one service down → warning
        else:
            condition = "unknown_state"  # all services look ok but process missing → warning

    except json.JSONDecodeError:
        condition = "check_error"
        services_info = [{"error": "bad JSON from PowerShell"}]
    except Exception as e:
        condition = "check_error"
        services_info = [{"error": str(e)}]

    installed = condition not in ("not_installed",)
    status_ok = False

    return {
        "installed"  : installed,
        "status_ok"  : status_ok,
        "condition"  : condition,
        # condition values:
        #   "running"       → process alive, all good (status_ok=True)
        #   "stopped"       → one or more services stopped → warning alert
        #   "disabled"      → one or more services disabled → critical alert
        #   "not_installed" → no services found at all → critical alert
        #   "unknown_state" → services ok but no process → warning alert
        #   "check_error"   → couldn't determine state → warning alert
        "process"    : process_info,
        "services"   : services_info,
    }


def get_power_status() -> dict:
    """
    On/Off status: is the machine awake, what power source, and battery if laptop.
    'online' means the agent can reach out — if we got here, it's on.
    """
    battery = psutil.sensors_battery()
    status = {
        "online"        : True,
        "power_source"  : "unknown",
        "battery_pct"   : None,
        "is_laptop"     : battery is not None,
    }
    if battery:
        status["power_source"] = "AC" if battery.power_plugged else "battery"
        status["battery_pct"]  = round(battery.percent, 1)
    else:
        status["power_source"] = "AC"   # desktop — always plugged in
    return status


def get_blackpoint_snap_status() -> dict:
    """
    Check the health of the Blackpoint Snap agent.

    Strategy (lightweight-first):
      1. psutil process scan — primary check, near-zero cost, completely invisible
         to the user. If the process is found, Snap is running. Done.
      2. PowerShell service check — only runs if the process is NOT found.
         Distinguishes between four failure states so the server can alert
         with the right severity:
           - Disabled   → someone turned it off intentionally (critical)
           - Stopped    → installed but crashed / not yet restarted (warning)
           - Not found  → not installed at all (critical)
           - Error      → couldn't determine state (warning)

    Add your environment's exact service/process names to the lists below
    if they differ. Run on a Snap machine to find them:
        Get-Service | Where-Object { $_.DisplayName -like '*snap*' -or $_.DisplayName -like '*blackpoint*' }
        Get-Process | Where-Object { $_.Name -like '*snap*' -or $_.Name -like '*bp*' }
    """

    SNAP_PROCESS_NAMES = {
        "snapagent.exe",
        "snapw.exe",
        "snap.exe",
        "bpagent.exe",
        "snap_agent.exe",
        "blackpointsnap.exe",
    }
    SNAP_SERVICE_NAMES = [
        "Snap",               # ← confirmed name on your machines
        "SnapService",
        "BlackpointSnap",
        "BPAgent",
        "snap-agent",
    ]

    # ── STEP 1: psutil process scan (primary, free) ───────────────────────────
    process_found = False
    process_info  = {"found": False, "name": None, "pid": None}

    try:
        for proc in psutil.process_iter(["name", "pid"]):
            pname = (proc.info["name"] or "").lower()
            if pname in SNAP_PROCESS_NAMES:
                process_found = True
                process_info  = {
                    "found" : True,
                    "name"  : proc.info["name"],
                    "pid"   : proc.info["pid"],
                }
                break
    except Exception as e:
        process_info["error"] = str(e)

    # Process is alive — best-case, return immediately, skip PowerShell entirely
    if process_found:
        return {
            "installed"    : True,
            "status_ok"    : True,
            "condition"    : "running",
            "process"      : process_info,
            "service"      : {"checked": False, "reason": "not needed — process found"},
        }

    # ── STEP 2: process not found — use PowerShell to find out why ────────────
    ps_script = """
    $names = @('Snap','SnapService','BlackpointSnap','BPAgent','snap-agent')
    foreach ($n in $names) {
        $svc = Get-Service -Name $n -ErrorAction SilentlyContinue
        if ($svc) {
            [PSCustomObject]@{
                found      = $true
                name       = $svc.Name
                state      = $svc.Status.ToString()
                start_type = $svc.StartType.ToString()
            } | ConvertTo-Json
            exit
        }
    }
    @{ found = $false } | ConvertTo-Json
    """
    service_info = {"checked": True, "found": False}
    condition    = "not_installed"   # default assumption

    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=12
        )
        data = json.loads(result.stdout.strip())

        if data.get("found"):
            state      = data.get("state", "").lower()       # running/stopped/paused
            start_type = data.get("start_type", "").lower()  # automatic/manual/disabled

            service_info = {
                "checked"    : True,
                "found"      : True,
                "name"       : data.get("name"),
                "state"      : data.get("state"),
                "start_type" : data.get("start_type"),
            }

            # Map service state to a meaningful condition
            if start_type == "disabled":
                condition = "disabled"       # intentionally turned off → critical
            elif state == "stopped":
                condition = "stopped"        # installed but not running → warning
            else:
                condition = "unknown_state"  # running per SCM but no process? → warning
        else:
            service_info["found"] = False
            condition = "not_installed"      # nothing found anywhere → critical

    except json.JSONDecodeError:
        service_info["error"] = "bad JSON from PowerShell"
        condition = "check_error"
    except Exception as e:
        service_info["error"] = str(e)
        condition = "check_error"

    # condition → status_ok + installed flags
    installed = condition not in ("not_installed",)
    status_ok = False   # process was not found, so never OK

    return {
        "installed"  : installed,
        "status_ok"  : status_ok,
        "condition"  : condition,
        # condition values and their meaning for the server alert engine:
        #   "running"       → healthy (status_ok=True)
        #   "stopped"       → crashed or mid-restart → warning alert
        #   "disabled"      → deliberately disabled → critical alert
        #   "not_installed" → missing entirely → critical alert
        #   "unknown_state" → SCM says running but no process → warning alert
        #   "check_error"   → couldn't determine state → warning alert
        "process"    : process_info,
        "service"    : service_info,
    }


def get_os_info() -> dict:
    return {
        "name"     : platform.system(),
        "version"  : platform.version(),
        "release"  : platform.release(),
        "machine"  : platform.machine(),
        "hostname" : socket.gethostname(),
    }


def get_uptime_seconds() -> int:
    return int(time.time() - psutil.boot_time())


# ── PAYLOAD BUILDER ───────────────────────────────────────────────────────────

def build_payload() -> dict:
    log.info("Collecting metrics…")
    return {
        "agent_version" : AGENT_VERSION,
        "device_id"     : get_device_id(),
        "timestamp"     : datetime.now(timezone.utc).isoformat(),
        "uptime_sec"    : get_uptime_seconds(),
        "os"            : get_os_info(),
        "cpu"           : get_cpu(),
        "memory"        : get_memory(),
        "disk"          : get_disk(),
        "current_user"  : get_current_user(),
        "defender"      : get_bitdefender_status(),
        "blackpoint_snap": get_blackpoint_snap_status(),
        "power"         : get_power_status(),
    }


# ── SEND ──────────────────────────────────────────────────────────────────────

def send_checkin(payload: dict) -> bool:
    try:
        resp = requests.post(
            SERVER_URL,
            json=payload,
            timeout=10,
            headers={
                "Content-Type"  : "application/json",
                "User-Agent"    : f"CoolRMM-Agent/{AGENT_VERSION}",
                "X-Device-ID"   : payload["device_id"],
            }
        )
        if resp.status_code == 200:
            log.info(f"Check-in OK  [{resp.status_code}]  device={payload['device_id'][:12]}…")
            return True
        else:
            log.warning(f"Server returned {resp.status_code}: {resp.text[:120]}")
            return False
    except requests.exceptions.ConnectionError:
        log.error(f"Cannot reach server at {SERVER_URL} — will retry in {POLL_INTERVAL}s")
        return False
    except Exception as e:
        log.error(f"Send failed: {e}")
        return False


# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info(f"  Cool RMM Agent v{AGENT_VERSION} starting")
    log.info(f"  Host    : {socket.gethostname()}")
    log.info(f"  Server  : {SERVER_URL}")
    log.info(f"  Poll    : every {POLL_INTERVAL}s")
    log.info(f"  Log     : {LOG_FILE}")
    log.info("=" * 60)

    while True:
        try:
            payload = build_payload()
            send_checkin(payload)
        except Exception as e:
            log.error(f"Unhandled error in poll loop: {e}")

        log.info(f"Sleeping {POLL_INTERVAL}s…")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
