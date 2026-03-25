# Wi-Fi IP Switcher

> Windows automation tool that detects your active Wi-Fi SSID and **automatically switches between static IP and DHCP configurations** — no manual network changes needed when moving between home, office, or campus networks.

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![Flask](https://img.shields.io/badge/Flask-Config%20UI-000000?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6?style=flat-square&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![PyInstaller](https://img.shields.io/badge/PyInstaller-Standalone%20exe-yellow?style=flat-square)](https://pyinstaller.org)

---

## The Problem

Switching between networks with different IP requirements is a repetitive, error-prone manual task:

- **Home network** — DHCP, router assigns IP automatically
- **Office / lab network** — static IP required, specific gateway and DNS
- **University network** — different static IP range entirely

Every time you move, you open Network Settings, change adapter properties, retype the same values. This tool eliminates that entirely.

---

## How It Works

```
Windows startup (Task Scheduler, elevated)
        │
        ▼
Background monitor thread starts
        │
        ▼
┌─────────────────────────────────────────┐
│  Every N seconds:                        │
│  1. Read current Wi-Fi SSID             │
│  2. Look up SSID in config.json         │
│  3. If SSID config = static IP          │
│     └─ Run netsh to set static IP       │
│  4. If SSID config = DHCP               │
│     └─ Run netsh to enable DHCP         │
│  5. If already correct → do nothing     │
└─────────────────────────────────────────┘
        │
        ▼
System tray icon shows current status
        │
        ▼
Flask web UI available for config changes
```

The tool runs silently in the background. The only visible sign it's running is a system tray icon showing the current network mode. Configuration is managed through a local web interface — no editing JSON files by hand.

---

## Features

- **SSID-aware switching** — maps each Wi-Fi network name to its IP configuration
- **Background service** — Python threading keeps the monitor running without blocking
- **System tray integration** — live status indicator, right-click menu to open config or quit
- **Flask config UI** — browser-based interface to add/edit/remove network profiles
- **Automated startup** — Windows Task Scheduler launches at login with elevated privileges (required for `netsh` network changes)
- **JSON config persistence** — all profiles stored in `config.json`, survives restarts
- **Structured logging** — timestamped log file for debugging switching events
- **Standalone `.exe`** — packaged with PyInstaller + Inno Setup installer, no Python required on target machine

---

## Project Structure

```
wifi_switcher/
├── app.py                  # Main application — monitor thread + tray icon + Flask UI
├── templates/
│   └── index.html          # Config UI — add/edit SSID profiles
├── requirements.txt        # Python runtime dependencies
├── requirements-dev.txt    # Build-only dependencies (PyInstaller)
├── WiFiIPSwitcher.iss      # Inno Setup script — builds Windows installer (.exe)
└── wifi_ip_switcher.ico    # System tray icon
```

---

## Quick Start

### Run from source

**Prerequisites:** Python 3.9+, Windows 10/11, admin privileges

```bash
git clone https://github.com/Vasanth1602/wifi_switcher.git
cd wifi_switcher

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run with elevated privileges (required for netsh)
# Right-click terminal → "Run as administrator"
python app.py
```

The system tray icon will appear. Open your browser to `http://localhost:5000` to configure your network profiles.

### Add a network profile

1. Open `http://localhost:5000` in your browser
2. Enter the **SSID** of the network (exactly as it appears in Windows Wi-Fi list)
3. Choose **Static IP** or **DHCP**
4. For static IP — enter IP address, subnet mask, gateway, and DNS servers
5. Save — the monitor picks up the new profile on the next scan cycle

---

## Configuration

Profiles are stored in `%LOCALAPPDATA%\WiFi_IP_Switcher\wifi_ip_config.json`
(e.g. `C:\Users\YourName\AppData\Local\WiFi_IP_Switcher\wifi_ip_config.json`).

Any SSID without a saved profile automatically falls back to **DHCP**.
For static IPs, save a profile via the web UI or edit the JSON directly:

```json
{
    "OfficeWiFi": {
        "ip": "192.168.1.100",
        "subnet": "255.255.255.0",
        "gateway": "192.168.1.1",
        "preferred_dns": "8.8.8.8",
        "alternate_dns": "8.8.4.4"
    },
    "UniversityLab": {
        "ip": "10.0.1.45",
        "subnet": "255.255.0.0",
        "gateway": "10.0.0.1",
        "preferred_dns": "10.0.0.10",
        "alternate_dns": "10.0.0.11"
    }
}
```

> **DHCP networks** do not need an entry — any unknown SSID automatically reverts to DHCP.

You can edit this file directly or use the web UI — both work.

---

## Automated Startup (Task Scheduler)

To run automatically at login with the required elevated privileges:

1. Open **Task Scheduler** → **Create Task**
2. **General tab:**
   - Name: `WiFi IP Switcher`
   - Check **Run with highest privileges**
   - Configure for: **Windows 10/11**
3. **Triggers tab:** → New → **At log on**
4. **Actions tab:** → New
   - Action: **Start a program**
   - Program: path to `wifi_ip_switcher.exe` (or `python.exe`)
   - Arguments: `app.py` (if running from source)
5. **Conditions tab:** Uncheck "Start only if on AC power"
6. Click OK — it will prompt for your Windows password

The tool starts silently at every login. No console window, no UAC prompt after initial setup.

---

## Build Standalone Executable

Requires PyInstaller (install from `requirements-dev.txt`):

```bash
pip install -r requirements-dev.txt

pyinstaller --windowed --icon=wifi_ip_switcher.ico --add-data "templates;templates" --add-data "wifi_ip_switcher.ico;." --name app app.py
# Output: dist/app/   (folder with all files — used by the Inno Setup installer)
```

> **Do not use `--onefile`** — the Inno Setup script expects a folder (`dist\app\*`), not a single exe.

To build the full Windows installer (`.exe` setup file) using the included Inno Setup script:

1. Install [Inno Setup](https://jrsoftware.org/isinfo.php)
2. Open `WiFiIPSwitcher.iss` in Inno Setup Compiler
3. Build → generates a single installer `.exe` in the `Output/` folder

The installer handles file placement, Task Scheduler registration, and Start Menu shortcuts automatically.

---

## Key Technical Details

| Component | Implementation |
|---|---|
| SSID detection | `subprocess` + `netsh wlan show interfaces` output parsing |
| IP switching | `netsh interface ip set address` (static) / `set source=dhcp` |
| Background monitor | Python `threading.Thread` with configurable polling interval |
| System tray | `pystray` library with dynamic icon and right-click menu |
| Config UI | Flask dev server on `localhost:5000`, HTML templates |
| Config storage | JSON file with atomic read/write to prevent corruption |
| Logging | Python `logging` module — rotating file handler |
| Packaging | PyInstaller `--onedir --windowed` + Inno Setup `.iss` script |

---

## Limitations

- **Windows only** — uses `netsh` commands which are Windows-specific
- **Admin required** — network adapter changes require elevated privileges
- **Single adapter** — monitors the primary Wi-Fi adapter only
- **Location Indicator Flashing** — Windows 10/11 treats `netsh wlan show interfaces` as location data because it reads the router MAC address (BSSID). The Windows location icon will flash every 5 seconds (or whatever `check_interval` is set to). Alternative APIs like `Get-NetConnectionProfile` were tested but rejected because they return Windows-generated profile names (e.g. `"SSID 2"`) or `"Unidentified network"`, rather than the true SSID.
- **Cold Boot Delay** — When powering on from a full shutdown, there is an unavoidable delay before IP switching works. The timeline is: Windows boot (~30-60s) + Login time + Task Scheduler startup + wait for Wi-Fi stack to be ready. It takes roughly **70–100 seconds from pressing the power button** until the app is fully running and able to switch IPs.
- **Polling-based** — checks SSID on an interval rather than on connection events

---

## Related Projects

- [ML Deployment Platform](https://github.com/Vasanth1602/ML-Deployment-Platform) — full-stack MLOps platform with AWS, Docker, and Terraform
- [Jenkins CI/CD Pipeline](https://github.com/Vasanth1602/jenkins_workflow) — automated build, test, and deploy pipeline
