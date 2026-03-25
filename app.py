import ctypes
import getpass
import os
import socket
import subprocess
import sys
import time
import json
import logging
import webbrowser
import threading
from flask import Flask, render_template, request, redirect, url_for
from pystray import Icon, MenuItem, Menu
from PIL import Image, ImageDraw
import idlelib.tree  # Explicit import required so PyInstaller bundles it

# FIXED #1: Removed "import winreg" — it was imported but never used anywhere
# in the codebase. Dead imports confuse readers and add unnecessary bundle size.


# --- Dummy MessageBox for headless/windowed builds ---
# tkinter.messagebox is not bundled by PyInstaller in --windowed mode.
# This dummy class prevents ImportError crashes while still writing to the log.
class DummyMessageBox:
    def showerror(self, title, message):
        logging.error(f"GUI Error: {title} - {message}")

    def showwarning(self, title, message):
        logging.warning(f"GUI Warning: {title} - {message}")


messagebox = DummyMessageBox()


# === Constants ===
APP_DATA_DIR = os.path.join(os.getenv('LOCALAPPDATA'), "WiFi_IP_Switcher")

if not os.path.exists(APP_DATA_DIR):
    try:
        os.makedirs(APP_DATA_DIR)
    except OSError as e:
        print(f"ERROR: Could not create app data directory {APP_DATA_DIR}: {e}")
        sys.exit(1)

config_file = os.path.join(APP_DATA_DIR, "wifi_ip_config.json")
log_file = os.path.join(APP_DATA_DIR, "wifi_ip_switcher.log")
check_interval = 5
icon_path = "wifi_ip_switcher.ico"
TASK_NAME = "WiFiIPSwitcherStartupTask"
active_port = 5000  # will be updated by start_flask_app() to whichever port binds


# === Logging Setup ===
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    encoding='utf-8'
)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(console_handler)


# === Flask App ===
app = Flask(__name__)


# === Admin Check ===
def is_admin():
    """Returns True if the current process has Windows administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        logging.error(f"Error checking admin privileges: {e}", exc_info=True)
        return False


# === Scheduled Task Functions ===
def create_scheduled_task():
    """
    Creates a Windows Scheduled Task that auto-starts this app at user login
    with highest privileges — so no UAC prompt appears on subsequent boots.

    Key flags used:
      /sc ONLOGON   — trigger: when THIS user logs in
      /ru           — run as the current user (not SYSTEM, not all users)
      /rl HIGHEST   — run with elevated privileges
      /delay 0000:00 — no delay; monitor retries interface detection until ready
      /f            — overwrite if task already exists
    """
    logging.info(f"[TASK] Creating Scheduled Task '{TASK_NAME}'...")
    try:
        exe_path = sys.executable
        current_user = getpass.getuser()

        # FIXED #3: Removed task_args completely when empty.
        # Old code: f'"{exe_path} {task_args}"' produced "app.exe " with a
        # trailing space when task_args="". Some Windows versions reject this.
        # Now we only quote the exe path itself, no trailing space ever.
        command = [
            "schtasks", "/create",
            "/tn", TASK_NAME,
            "/tr", f'"{exe_path}"',        # clean path, no trailing space
            "/sc", "ONLOGON",
            "/ru", current_user,           # FIXED #4: scope task to THIS user only
            "/rl", "HIGHEST",
            "/it",
            "/delay", "0000:00",           # 0s delay — lets Wi-Fi stack initialize
            "/f"
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        logging.info(f"[TASK] Scheduled Task '{TASK_NAME}' created for user '{current_user}'.")
        logging.debug(f"[TASK] schtasks output: {result.stdout.strip()}")
        return True

    except subprocess.CalledProcessError as e:
        logging.error(
            f"[TASK] Failed to create Scheduled Task: {e.stderr.strip()}",
            exc_info=True
        )
        return False
    except Exception as e:
        logging.error(f"[TASK] Unexpected error creating task: {e}", exc_info=True)
        return False


def is_scheduled_task_created():
    """Returns True if the Scheduled Task already exists on this machine."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        exists = result.returncode == 0
        logging.debug(f"[TASK] Task '{TASK_NAME}' exists: {exists}")
        return exists
    except Exception as e:
        logging.error(f"[TASK] Error checking task existence: {e}", exc_info=True)
        return False


# === Netsh Helper ===
def run_netsh_command(command_args):
    """
    Runs a netsh subprocess command and returns stdout, or None on failure.
    CREATE_NO_WINDOW suppresses the console flash on Windows.
    """
    try:
        result = subprocess.run(
            command_args,
            capture_output=True,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        logging.error(
            f"Netsh failed: '{' '.join(e.cmd)}' — {e.stderr.strip()}",
            exc_info=True
        )
        return None
    except Exception as e:
        logging.error(f"Unexpected error running netsh: {e}", exc_info=True)
        return None


# === Wi-Fi Interface Detection ===
def get_wifi_interface_name():
    """
    Reads 'netsh wlan show interfaces' and returns the adapter name.
    Falls back to 'Wi-Fi' if detection fails, but logs a clear warning.

    Why we parse 'Name' specifically:
      The output contains lines like:
        Name                   : Wi-Fi
        Description            : Intel Wireless-AC 9560
      We match the first line that starts with 'Name' and has a colon.
    """
    logging.info("[INTERFACE] Detecting Wi-Fi interface name...")
    output = run_netsh_command(["netsh", "wlan", "show", "interfaces"])
    if output:
        for line in output.splitlines():
            # FIXED #7 (partial): More precise match — must start with 'Name'
            # Old code used 'if "Name" in line' which could match
            # "Interface Name", "Profile Name", etc. on localized Windows.
            stripped = line.strip()
            if stripped.startswith("Name") and ":" in stripped:
                name = stripped.split(":", 1)[1].strip()
                if name:
                    logging.info(f"[INTERFACE] Detected: '{name}'")
                    return name

    # Return None instead of a hardcoded "Wi-Fi" fallback.
    # With 0s Task Scheduler delay, the adapter may not be enumerable yet
    # at process startup. Returning None lets the monitor loop retry
    # detection every check_interval seconds until the adapter is ready,
    # rather than permanently locking in the wrong interface name.
    logging.warning(
        "[INTERFACE] Could not detect Wi-Fi interface name from netsh output. "
        "Returning None — monitor will retry automatically."
    )
    return None


def get_connected_ssid():
    """Returns the SSID of the currently connected Wi-Fi network, or None."""
    output = run_netsh_command(["netsh", "wlan", "show", "interfaces"])
    if output:
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("SSID") and "BSSID" not in stripped:
                return stripped.split(":", 1)[1].strip().strip('"')
    return None


def get_current_ip(interface):
    """Returns the current IP address of the interface, or None."""
    output = run_netsh_command(
        ["netsh", "interface", "ip", "show", "config", f"name={interface}"]
    )
    if output:
        for line in output.splitlines():
            if "IP Address" in line:
                return line.split(":", 1)[1].strip()
    return None


def is_dhcp_enabled(interface):
    """
    FIXED #5 (new function): Returns True if the interface is currently
    using DHCP, by parsing the 'DHCP Enabled' line from netsh output.

    Why this replaces the old '0.0.0.0' check:
      Old code: current_ip != "0.0.0.0"
      Problem:  A DHCP-assigned IP like 192.168.1.50 passes that check,
                so set_dhcp_ip() was being called even when DHCP was
                already active — resetting the network unnecessarily.
      Fix:      Ask netsh directly whether DHCP is enabled. If it says
                "Yes", skip the set_dhcp_ip() call entirely.
    """
    output = run_netsh_command(
        ["netsh", "interface", "ip", "show", "config", f"name={interface}"]
    )
    if output:
        for line in output.splitlines():
            if "DHCP Enabled" in line:
                return "Yes" in line
    return False


def set_static_ip(interface, ip, subnet, gateway, preferred_dns, alternate_dns):
    """Applies a static IP configuration to the named network interface."""
    logging.info(f"[NETWORK] Setting static IP on '{interface}': {ip}")
    success = True

    if not run_netsh_command([
        "netsh", "interface", "ip", "set", "address",
        interface, "static", ip, subnet, gateway
    ]):
        success = False

    if success and not run_netsh_command([
        "netsh", "interface", "ip", "set", "dns",
        interface, "static", preferred_dns, "primary"
    ]):
        success = False

    if success and alternate_dns and not run_netsh_command([
        "netsh", "interface", "ip", "add", "dns",
        interface, alternate_dns, "index=2"
    ]):
        success = False

    if success:
        logging.info(f"[NETWORK] Static IP set successfully on '{interface}': {ip}")
    else:
        logging.error(f"[NETWORK] Failed to set static IP on '{interface}'.")


def set_dhcp_ip(interface):
    """Reverts the interface to automatic IP and DNS via DHCP."""
    logging.info(f"[NETWORK] Setting DHCP on '{interface}'...")
    success = True

    if not run_netsh_command([
        "netsh", "interface", "ip", "set", "address", interface, "dhcp"
    ]):
        success = False

    if success and not run_netsh_command([
        "netsh", "interface", "ip", "set", "dns", interface, "dhcp"
    ]):
        success = False

    if success:
        logging.info(f"[NETWORK] DHCP enabled successfully on '{interface}'.")
    else:
        logging.error(f"[NETWORK] Failed to enable DHCP on '{interface}'.")


# === Config Handling ===
def load_or_create_config():
    """
    Loads the JSON config file. Returns empty dict if missing or corrupted.
    Logs at DEBUG level (not INFO) to avoid 17KB/day log spam from the
    monitor loop calling this every 5 seconds.
    """
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding='utf-8') as f:
                config_data = json.load(f)
                # FIXED #2: Changed INFO → DEBUG.
                # This function is called every 5 seconds by the monitor loop.
                # Logging at INFO was writing ~17KB/day to the log file for
                # one line that provides zero diagnostic value during normal ops.
                logging.debug("[CONFIG] Configuration loaded.")
                return config_data
        except json.JSONDecodeError as e:
            logging.error(
                f"[CONFIG] Corrupted config '{config_file}': {e}. Resetting.",
                exc_info=True
            )
            try:
                os.remove(config_file)
            except Exception as e_del:
                logging.error(f"[CONFIG] Could not delete corrupted file: {e_del}")
            return {}
        except Exception as e:
            logging.error(f"[CONFIG] Error reading config: {e}", exc_info=True)
            return {}

    logging.debug("[CONFIG] Config file not found, returning empty config.")
    return {}


def save_config(config):
    """
    Writes the config dict to the JSON file atomically.

    Why atomic?
      A plain open(..., "w") truncates the file immediately. If the process
      crashes between truncation and the final write, the config file is left
      empty or partially written — unrecoverable corruption.

      Fix: write to a temp file in the same directory, then os.replace() which
      is atomic on all major OS/FS combinations. The old file is replaced only
      after the new data is fully written and flushed.
    """
    tmp_file = config_file + ".tmp"
    try:
        with open(tmp_file, "w", encoding='utf-8') as f:
            json.dump(config, f, indent=4)
            f.flush()
            os.fsync(f.fileno())  # ensure data hits disk before replacing
        os.replace(tmp_file, config_file)  # atomic on Windows & POSIX
        logging.info("[CONFIG] Configuration saved.")
    except Exception as e:
        logging.error(f"[CONFIG] Error saving config: {e}", exc_info=True)
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass


# === Monitor Loop ===
def monitor_ssid_loop(interface_name):
    """
    Polls the connected SSID every `check_interval` seconds.
    When the SSID changes, applies the matching static IP config
    or reverts to DHCP if no config exists for that SSID.

    Receives interface_name as a parameter — NOT read from global scope.
    This makes the dependency explicit and avoids any startup race condition
    where threads might read the global before main() sets it.
    """
    last_ssid = None
    logging.info("[MONITOR] SSID monitoring started.")

    while True:
        try:
            # --- Lazy interface detection with retry ---
            # With 0s Task Scheduler delay, the Wi-Fi adapter may not be
            # enumerable yet when the app starts. get_wifi_interface_name()
            # returns None in that case. We retry every check_interval seconds
            # until the adapter reports itself — no hardcoded fallback needed.
            if interface_name is None:
                interface_name = get_wifi_interface_name()
                if interface_name is None:
                    logging.info(
                        "[MONITOR] Wi-Fi adapter not ready yet. "
                        f"Retrying in {check_interval}s..."
                    )
                    time.sleep(check_interval)
                    continue
                logging.info(f"[MONITOR] Interface resolved: '{interface_name}'")

            ssid = get_connected_ssid()
            config = load_or_create_config()

            if ssid != last_ssid:
                logging.info(
                    f"[MONITOR] SSID changed: '{last_ssid}' → '{ssid}'"
                )
                last_ssid = ssid

                if ssid and ssid in config:
                    # Known SSID — apply the saved static IP if not already set
                    ip_config = config[ssid]
                    current_ip = get_current_ip(interface_name)
                    if current_ip != ip_config["ip"]:
                        logging.info(
                            f"[MONITOR] Applying static IP for SSID '{ssid}'."
                        )
                        set_static_ip(
                            interface_name,
                            ip_config["ip"],
                            ip_config["subnet"],
                            ip_config["gateway"],
                            ip_config["preferred_dns"],
                            ip_config["alternate_dns"]
                        )
                    else:
                        logging.info(
                            f"[MONITOR] Static IP already correct for '{ssid}'."
                        )
                else:
                    # Unknown SSID (or disconnected) — revert to DHCP
                    # FIXED #5: Use is_dhcp_enabled() instead of "0.0.0.0" check.
                    # Old check: current_ip != "0.0.0.0"
                    # Problem:   DHCP gives real IPs (192.168.x.x), not 0.0.0.0.
                    #            So set_dhcp_ip() was called even when already on DHCP,
                    #            causing an unnecessary network reset every 5 seconds.
                    # Fix:       Ask netsh if DHCP is enabled. Only call set_dhcp_ip()
                    #            if the interface is currently using a static config.
                    if not is_dhcp_enabled(interface_name):
                        logging.info(
                            f"[MONITOR] SSID '{ssid}' not in config. "
                            f"Reverting to DHCP."
                        )
                        set_dhcp_ip(interface_name)
                    else:
                        logging.info(
                            f"[MONITOR] SSID '{ssid}' not in config. "
                            f"Already on DHCP, no action needed."
                        )

            time.sleep(check_interval)

        except Exception as e:
            logging.error(f"[MONITOR] Exception: {e}", exc_info=True)
            time.sleep(check_interval)


# === Flask Routes ===
@app.route('/')
def index():
    """
    Main config page. Shows existing SSID profiles + the add/edit form.
    Reads ?saved=1 from the URL to decide whether to show the success banner.
    The banner appears only immediately after a save — not on normal page loads.
    """
    config = load_or_create_config()
    saved = request.args.get('saved', '0') == '1'
    return render_template('index.html', existing_config=config, saved=saved)


def is_valid_ipv4(value):
    """
    Server-side IPv4 validation.
    Returns True only for dotted-quad strings where each octet is 0–255.
    Rejects empty strings, hostnames, and values like '999.0.0.1'.
    Mirrors the client-side isValidIP() function in index.html.
    """
    if not value:
        return False
    parts = value.split('.')
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        if not (0 <= int(part) <= 255):
            return False
    return True


@app.route('/submit', methods=['POST'])
def submit_config():
    """Saves a new or updated SSID → IP profile to the config file."""
    ssid = request.form['ssid'].strip()
    ip = request.form['ip'].strip()
    subnet = request.form['subnet'].strip()
    gateway = request.form['gateway'].strip()
    preferred_dns = request.form['preferred_dns'].strip()
    alternate_dns = request.form.get('alternate_dns', '').strip()

    # Server-side validation — required fields must all be present
    if not all([ssid, ip, subnet, gateway, preferred_dns]):
        logging.warning("[WEB] Submit failed: missing required fields.")
        return "Error: SSID, IP, Subnet, Gateway, and Preferred DNS are required.", 400

    # Server-side IP format validation
    # Client-side JS is bypassable (e.g. via curl or modified requests).
    # Reject malformed IPs here before they reach netsh and cause adapter errors.
    invalid_fields = []
    for field_name, field_val in [
        ("IP Address", ip),
        ("Subnet Mask", subnet),
        ("Gateway", gateway),
        ("Preferred DNS", preferred_dns),
    ]:
        if not is_valid_ipv4(field_val):
            invalid_fields.append(field_name)

    if alternate_dns and not is_valid_ipv4(alternate_dns):
        invalid_fields.append("Alternate DNS")

    if invalid_fields:
        logging.warning(
            f"[WEB] Submit failed: invalid IP format in fields: {', '.join(invalid_fields)}"
        )
        return f"Error: Invalid IP format in: {', '.join(invalid_fields)}.", 400

    config = load_or_create_config()
    config[ssid] = {
        "ip": ip,
        "subnet": subnet,
        "gateway": gateway,
        "preferred_dns": preferred_dns,
        "alternate_dns": alternate_dns
    }
    save_config(config)
    logging.info(f"[WEB] Config saved for SSID: '{ssid}'")
    return redirect(url_for('index', saved=1))


@app.route('/delete', methods=['POST'])
def delete_config():
    """
    Deletes a single SSID profile from the config.
    Called from the existing profiles table in index.html.
    """
    ssid = request.form.get('ssid', '').strip()
    if ssid:
        config = load_or_create_config()
        if ssid in config:
            del config[ssid]
            save_config(config)
            logging.info(f"[WEB] Config deleted for SSID: '{ssid}'")
    return redirect(url_for('index'))


# === Web Server ===
def is_port_free(port):
    """
    FIXED #8 (new function): Checks if a TCP port is available before
    Flask tries to bind. Without this, if port 5000 is already in use,
    Flask raises OSError which is caught and logged, but the web UI is
    silently dead with no user feedback.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def start_flask_app():
    """
    Starts Flask on the first available port between 5000 and 5010.
    Stores the bound port in active_port so open_browser() and the
    tray menu always open the correct URL regardless of which port was used.
    """
    global active_port

    for port in range(5000, 5011):
        if is_port_free(port):
            active_port = port
            logging.info(f"[FLASK] Starting Flask on http://127.0.0.1:{port}/")
            try:
                app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)
            except Exception as e:
                logging.critical(f"[FLASK] Flask failed on port {port}: {e}", exc_info=True)
            return

    logging.error("[FLASK] Ports 5000–5010 all in use. Web UI unavailable.")


def open_browser():
    """Opens the default browser to the web config UI."""
    try:
        logging.info(f"[BROWSER] Opening http://127.0.0.1:{active_port}/")
        webbrowser.open(f"http://127.0.0.1:{active_port}/")
    except Exception as e:
        logging.error(f"[BROWSER] Could not open browser: {e}", exc_info=True)


# === Tray Icon ===
def start_tray_icon(interface_name):
    """
    Creates and runs the system tray icon with its context menu.
    Receives interface_name as a parameter for the same reason as
    monitor_ssid_loop — explicit dependency, no global reads.
    """
    logging.info("[TRAY] Initializing system tray icon...")

    # Resolve the icon path — sys._MEIPASS is the PyInstaller bundle directory
    bundle_dir = getattr(sys, '_MEIPASS', os.path.abspath("."))
    full_icon_path = os.path.join(bundle_dir, icon_path)

    # Load icon or generate a fallback
    icon_to_use = None
    try:
        if os.path.exists(full_icon_path):
            icon_to_use = Image.open(full_icon_path)
            logging.info(f"[TRAY] Icon loaded from {full_icon_path}")
        else:
            raise FileNotFoundError(f"Icon not found at {full_icon_path}")
    except Exception as e:
        logging.warning(f"[TRAY] Icon load failed ({e}). Using generated fallback.")
        icon_to_use = Image.new("RGB", (64, 64), (0, 102, 204))
        draw = ImageDraw.Draw(icon_to_use)
        draw.text((10, 20), "IP", fill="white")

    if icon_to_use is None:
        logging.critical("[TRAY] Could not create icon image. Exiting.")
        os._exit(1)

    # --- Tray menu callbacks ---

    def show_logs(icon_instance, item):
        """Opens the log file in Notepad."""
        logging.info("[TRAY] Opening log file.")
        try:
            subprocess.Popen(["notepad", log_file])
        except Exception as e:
            logging.error(f"[TRAY] Could not open Notepad: {e}", exc_info=True)

    def open_manage_page(icon_instance, item):
        """
        FIXED #6: Opens the web config UI WITHOUT deleting the config first.

        Old behaviour:
          1. os.remove(config_file)    ← config gone
          2. webbrowser.open(...)      ← browser opens
          Between steps 1 and 2 (within 5s), the monitor thread woke up,
          saw no config, and called set_dhcp_ip() — resetting your network
          before you'd typed anything in the browser.

        New behaviour:
          Just open the browser. The / route shows existing profiles in a
          table AND provides the form to add/edit. The user can delete
          individual SSID entries from the table using the /delete route.
          The network is never touched unexpectedly.

        FIXED #9: Use active_port instead of hardcoded 5000.
          If ports 5001–5010 were used (because 5000 was occupied), the old
          hardcoded URL would open a dead page. active_port always reflects
          whichever port Flask actually bound to.
        """
        logging.info(f"[TRAY] Opening config page in browser on port {active_port}.")
        webbrowser.open(f"http://127.0.0.1:{active_port}/")

    def on_quit(icon_instance, item):
        """Stops the tray icon and terminates the process."""
        logging.info("[TRAY] Quit requested. Shutting down.")
        try:
            icon_instance.stop()
        except Exception as e:
            logging.error(f"[TRAY] Error stopping icon: {e}", exc_info=True)
        finally:
            os._exit(0)

    menu = Menu(
        MenuItem("View Log", show_logs),
        MenuItem("Manage IP Profiles", open_manage_page),
        MenuItem("Quit", on_quit)
    )

    try:
        tray_icon = Icon("WiFiIPSwitcher", icon_to_use, "Wi-Fi IP Switcher", menu)
        logging.info("[TRAY] Tray icon running.")
        tray_icon.run()  # blocks this thread until icon.stop() is called
    except Exception as e:
        logging.critical(f"[TRAY] Fatal tray error: {e}", exc_info=True)
        os._exit(1)


# === Main Entry Point ===
def main():
    logging.info("=== Wi-Fi Auto IP Switcher Started ===")

    # --- Step 1: Scheduled task setup (first run only) ---
    if not is_scheduled_task_created():
        logging.info("[MAIN] Scheduled task not found — first run setup required.")
        if not is_admin():
            logging.warning("[MAIN] Not admin. Requesting elevation via UAC.")
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
            try:
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable,
                    f'"{sys.argv[0]}" {params}', None, 1
                )
                logging.info("[MAIN] Elevated process launched. This instance exiting.")
            except Exception as e:
                logging.critical(f"[MAIN] Elevation failed: {e}", exc_info=True)
            sys.exit(0)
        else:
            if create_scheduled_task():
                logging.info("[MAIN] Scheduled task created. Will auto-start on next login.")
            else:
                logging.error("[MAIN] Could not create scheduled task.")
    else:
        logging.info("[MAIN] Scheduled task exists. Proceeding as normal run.")

    # --- Step 2: Attempt Wi-Fi interface detection before starting threads ---
    # Called once here for an early log entry. May return None if the adapter
    # isn't enumerable yet (0s Task Scheduler delay). monitor_ssid_loop
    # retries detection automatically every check_interval seconds, so None
    # here is safe — it does NOT lock in a wrong fallback for the session.
    interface_name = get_wifi_interface_name()
    if interface_name:
        logging.info(f"[MAIN] Using Wi-Fi interface: '{interface_name}'")
    else:
        logging.warning(
            "[MAIN] Wi-Fi adapter not ready at startup. "
            "Monitor thread will retry interface detection automatically."
        )

    # --- Step 3: Start background threads ---
    flask_thread = threading.Thread(
        target=start_flask_app,
        name="FlaskThread",
        daemon=False
    )
    flask_thread.start()
    logging.info("[MAIN] Flask thread started.")

    # interface_name passed as argument — no global dependency
    monitor_thread = threading.Thread(
        target=monitor_ssid_loop,
        args=(interface_name,),   # explicit parameter, not global
        name="MonitorThread",
        daemon=False
    )
    monitor_thread.start()
    logging.info("[MAIN] Monitor thread started.")

    # interface_name passed to tray as well (for future tray notifications)
    tray_thread = threading.Thread(
        target=start_tray_icon,
        args=(interface_name,),
        name="TrayThread",
        daemon=False
    )
    tray_thread.start()
    logging.info("[MAIN] Tray thread started.")

    # --- Step 4: Open browser on first run ---
    # Wait 2s for Flask to bind before opening the browser
    time.sleep(2)
    if not os.path.exists(config_file) or load_or_create_config() == {}:
        logging.info("[MAIN] No config found. Opening browser for initial setup.")
        open_browser()
    else:
        logging.info("[MAIN] Config found. Running silently in background.")

    # --- Step 5: Keep main thread alive ---
    try:
        tray_thread.join()
        monitor_thread.join()
        flask_thread.join()
    except KeyboardInterrupt:
        logging.info("[MAIN] KeyboardInterrupt received.")
    except Exception as e:
        logging.critical(f"[MAIN] Unexpected error: {e}", exc_info=True)
    finally:
        logging.info("=== Wi-Fi Auto IP Switcher Exiting ===")


if __name__ == "__main__":
    main()