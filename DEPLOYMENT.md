# Cool RMM — Deployment Guide (142 machines via GPO)

This guide covers the complete deployment pipeline:
build the agent → stage on SYSVOL → push via GPO → self-healing on every boot.

---

## Files in this package

| File | Purpose |
|---|---|
| `startup_install.bat` | GPO startup script — runs on every machine at every boot |
| `cool_rmm_agent.spec` | PyInstaller build spec — produces the .exe |
| `version.txt` | Version tracker — bump this to trigger updates across all machines |
| `cool_rmm_agent.py` | Agent source |
| `nssm.exe` | Service manager — download separately (see below) |

---

## Step 1 — Build the agent .exe

On your build machine (needs Python 3.9+):

```
pip install pyinstaller psutil requests
pyinstaller cool_rmm_agent.spec
```

Output: `dist\cool_rmm_agent.exe`

To update all 142 machines later: rebuild the .exe, copy it to the share,
and bump the version number in version.txt. Every machine self-updates on next boot.

---

## Step 2 — Download NSSM

Download from https://nssm.cc/download
Use the 64-bit version: `nssm-2.24\win64\nssm.exe`

---

## Step 3 — Stage files on SYSVOL

SYSVOL is replicated to all domain controllers automatically and is accessible
from every domain machine. It's the right place for GPO scripts.

Create this folder structure on your DC:

```
\\YOUR_DC\SYSVOL\YourDomain.local\CoolRMM\
    cool_rmm_agent.exe      ← built in step 1
    nssm.exe                ← downloaded in step 2
    startup_install.bat     ← from this package
    version.txt             ← from this package (contains: 1.0.0)
```

Replace `YourDomain.local` with your actual domain name.

Make sure the share permissions allow:
- **Domain Computers** — Read (so machines can copy the files)
- **Domain Admins** — Full Control (so you can update files)

---

## Step 4 — Edit the startup script

Open `startup_install.bat` and update line 18:

```bat
set SHARE_PATH=\\YOUR_DC\SYSVOL\YourDomain.local\CoolRMM
```

Replace with your actual DC hostname and domain name.

---

## Step 5 — Create the GPO

1. Open **Group Policy Management Console** (gpmc.msc) on your DC

2. Right-click your machines OU → **Create a GPO in this domain, and Link it here**
   Name it: `Cool RMM Agent Deployment`

3. Right-click the new GPO → **Edit**

4. Navigate to:
   ```
   Computer Configuration
     └── Windows Settings
           └── Scripts (Startup/Shutdown)
                 └── Startup
   ```

5. Double-click **Startup** → **Add**
   - Script Name: `\\YOUR_DC\SYSVOL\YourDomain.local\CoolRMM\startup_install.bat`
   - Script Parameters: *(leave blank)*

6. Click OK → Apply

---

## Step 6 — Force GPO refresh (optional — speeds up rollout)

Instead of waiting for the next reboot of each machine, you can push immediately:

```powershell
# Run from your admin workstation
# Requires PSRemoting to be enabled on target machines

$machines = (Get-ADComputer -Filter * -SearchBase "OU=YourOU,DC=YourDomain,DC=local").Name

Invoke-Command -ComputerName $machines -ScriptBlock {
    gpupdate /force
} -AsJob
```

Or to trigger a reboot on all machines at a scheduled time (e.g. after hours):

```powershell
Invoke-Command -ComputerName $machines -ScriptBlock {
    shutdown /r /t 300 /c "Cool RMM agent deployment reboot"
}
```

---

## How updates work going forward

This is the self-healing update cycle:

1. You build a new `cool_rmm_agent.exe`
2. Copy it to `\\YOUR_DC\SYSVOL\YourDomain.local\CoolRMM\`
3. Update `version.txt` from `1.0.0` to `1.1.0` (or whatever)
4. That's it — every machine checks the version on next boot and self-updates

No touching individual machines. No manual service restarts.

---

## Monitoring the deployment

Each machine writes a local log to `C:\CoolRMM\deploy.log`

You can read these remotely from your admin workstation:

```powershell
# Check deploy log on a specific machine
Get-Content "\\DESKTOP-01\C$\CoolRMM\deploy.log" -Tail 20

# Check service status across all machines
$machines = (Get-ADComputer -Filter * -SearchBase "OU=YourOU,DC=YourDomain,DC=local").Name

Invoke-Command -ComputerName $machines -ScriptBlock {
    $s = Get-Service CoolRMMAgent -ErrorAction SilentlyContinue
    [PSCustomObject]@{
        Machine = $env:COMPUTERNAME
        Status  = if ($s) { $s.Status } else { "NOT INSTALLED" }
    }
} | Sort-Object Machine | Format-Table -AutoSize
```

This gives you a live table of all 142 machines and their agent status — without opening the dashboard.

---

## Troubleshooting

| Problem | Check |
|---|---|
| Service not installing | Is `nssm.exe` on the share? Do Domain Computers have Read access? |
| Agent not checking in | Check `C:\CoolRMM\agent_stderr.log` — usually a wrong SERVER_URL |
| GPO not applying | Run `gpresult /r` on the machine, confirm the GPO is listed |
| Version not updating | Check `C:\CoolRMM\version.txt` — does it match the share? |
| Can't reach share | Test with `net use \\YOUR_DC\SYSVOL` from the machine |

---

## Uninstall (if ever needed)

```bat
net stop CoolRMMAgent
nssm remove CoolRMMAgent confirm
rmdir /s /q C:\CoolRMM
```

Or push via GPO using the same startup script pattern with an uninstall flag.
