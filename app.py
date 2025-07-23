import ctypes
import os
import subprocess
import sys
import time
import json
import logging
import webbrowser
import threading
from flask import Flask, render_template, request, redirect, url_for
from pystray import Icon, MenuItem, Menu
from PIL import Image, ImageDraw # Keep Image for potential fallback or future use, but not ImageDraw for .ico
import idlelib.tree # Explicitly import for PyInstaller

# === Constants ===
interface_name = "Wi-Fi"
config_file = "wifi_ip_config.json"
log_file = "wifi_ip_switcher.log"
check_interval = 5  # seconds between SSID checks
icon_path = "wifi_ip_switcher.ico" # Define your icon path here

# === Logging Setup ===
# Ensure the log directory exists if you ever change log_file to a path like "logs/wifi_ip_switcher.log"
# For now, it's in the same directory, so os.path.dirname is '.'
log_dir = os.path.dirname(log_file)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(message)s', encoding='utf-8')
# Also add a console handler for immediate feedback during development/debugging
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(console_handler) # Add to the root logger

# === Flask App ===
app = Flask(__name__)

# === Admin Check ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        logging.error(f"Error checking admin privileges: {e}")
        return False

def relaunch_as_admin():
    logging.info("[ADMIN] Requesting Admin privileges...")
    # Ensure all arguments are correctly quoted if they contain spaces
    params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    try:
        # Use ShellExecuteW for admin elevation
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{sys.argv[0]}" {params}', None, 1
        )
    except Exception as e:
        logging.critical(f"[ADMIN] Failed to relaunch as admin: {e}")
        messagebox.showerror("Error", f"Failed to get admin privileges. Please run as administrator. Error: {e}")
    sys.exit() # Exit the current non-admin process

# === IP Functions ===
def run_netsh_command(command_args):
    """Helper to run netsh commands and suppress console window."""
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
        logging.error(f"Netsh command failed: {e.cmd} - Error: {e.stderr}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred running netsh: {e}")
        return None

def get_connected_ssid():
    output = run_netsh_command(["netsh", "wlan", "show", "interfaces"])
    if output:
        for line in output.splitlines():
            if "SSID" in line and "BSSID" not in line:
                # Use strip() to remove leading/trailing whitespace and split once
                ssid_value = line.split(":", 1)[1].strip()
                # Remove quotes if present
                return ssid_value.strip('"')
    return None

def get_current_ip(interface):
    output = run_netsh_command(["netsh", "interface", "ip", "show", "config", f"name={interface}"])
    if output:
        for line in output.splitlines():
            if "IP Address" in line:
                return line.split(":")[1].strip()
    return None

def set_static_ip(interface, ip, subnet, gateway, preferred_dns, alternate_dns):
    logging.info(f"[NETWORK] Attempting to set static IP for {interface}: {ip}")
    success = True
    if not run_netsh_command(["netsh", "interface", "ip", "set", "address", interface, "static", ip, subnet, gateway]):
        success = False
    if success and not run_netsh_command(["netsh", "interface", "ip", "set", "dns", interface, "static", preferred_dns, "primary"]):
        success = False
    if success and not run_netsh_command(["netsh", "interface", "ip", "add", "dns", interface, alternate_dns, "index=2"]):
        success = False

    if success:
        logging.info(f"[NETWORK] Static IP set for {interface}: {ip}")
    else:
        logging.error(f"[NETWORK] Static IP failed for {interface}. Check logs for details.")
        messagebox.showerror("IP Configuration Error", "Failed to set static IP. Please check log file for details and ensure you have admin privileges.")

def set_dhcp_ip(interface):
    logging.info(f"[NETWORK] Attempting to set DHCP for {interface}")
    success = True
    if not run_netsh_command(["netsh", "interface", "ip", "set", "address", interface, "dhcp"]):
        success = False
    if success and not run_netsh_command(["netsh", "interface", "ip", "set", "dns", interface, "dhcp"]):
        success = False

    if success:
        logging.info(f"[NETWORK] Switched to DHCP for {interface}")
    else:
        logging.error(f"[NETWORK] Switching to DHCP failed for {interface}. Check logs for details.")
        messagebox.showerror("IP Configuration Error", "Failed to set DHCP. Please check log file for details and ensure you have admin privileges.")


# === Config Handling ===
def load_or_create_config():
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                config_data = json.load(f)
                logging.info("[CONFIG] Configuration loaded successfully.")
                return config_data
        except json.JSONDecodeError:
            logging.error("[CONFIG] Corrupted config file. Deleting and creating new one.")
            try:
                os.remove(config_file)
            except Exception as e:
                logging.error(f"[CONFIG] Error deleting corrupted config file: {e}")
            return {}
        except Exception as e:
            logging.error(f"[CONFIG] Error loading config file: {e}")
            return {}
    logging.info("[CONFIG] Config file not found, returning empty config.")
    return {}

def save_config(config):
    try:
        with open(config_file, "w") as f:
            json.dump(config, f, indent=4)
        logging.info("[CONFIG] Configuration saved successfully.")
    except Exception as e:
        logging.error(f"[CONFIG] Error saving config file: {e}")

# === Auto IP Switching Logic ===
def monitor_ssid_loop():
    last_ssid = None
    logging.info("[MONITOR] Starting SSID monitoring loop.")
    while True:
        try:
            ssid = get_connected_ssid()
            config = load_or_create_config() # Reload config on each loop to pick up web changes

            if ssid != last_ssid:
                logging.info(f"[MONITOR] SSID change detected. Old: '{last_ssid}', New: '{ssid}'")
                last_ssid = ssid

                if ssid and ssid in config:
                    ip_config = config[ssid]
                    current_ip = get_current_ip(interface_name)
                    # Only apply if current IP is different from the desired one
                    if current_ip != ip_config["ip"]:
                        logging.info(f"[MONITOR] SSID '{ssid}' detected and has a configured static IP. Applying config.")
                        set_static_ip(
                            interface_name,
                            ip_config["ip"],
                            ip_config["subnet"],
                            ip_config["gateway"],
                            ip_config["preferred_dns"],
                            ip_config["alternate_dns"]
                        )
                    else:
                        logging.info(f"[MONITOR] IP already correctly set for SSID '{ssid}'. No change needed.")
                else:
                    # If no SSID is connected, or the connected SSID is not in config
                    if current_ip := get_current_ip(interface_name): # Walrus operator for Python 3.8+
                        # Only switch to DHCP if not already DHCP (e.g., has a static IP)
                        # A simple check: if current_ip is not None and not "0.0.0.0" (common for DHCP, but not definitive)
                        # The real check would be to parse `netsh interface ip show config` for "DHCP Enabled: Yes"
                        # For simplicity, if we don't have a config for this SSID, we revert to DHCP.
                        # This avoids unnecessarily running netsh commands if we're already on DHCP.
                        logging.info(f"[MONITOR] SSID '{ssid}' (or no SSID) not in config. Ensuring DHCP is enabled.")
                        set_dhcp_ip(interface_name)
                    else:
                        logging.info("[MONITOR] No IP found for interface or already on DHCP. No change needed.")
            time.sleep(check_interval)
        except Exception as e:
            logging.error(f"[MONITOR] Exception in monitor_ssid_loop: {e}", exc_info=True)
            time.sleep(check_interval)

# === Flask Routes ===
@app.route('/')
def index():
    config = load_or_create_config()
    return render_template('index.html', existing_config=config)

@app.route('/submit', methods=['POST'])
def submit_config():
    ssid = request.form['ssid'].strip()
    ip = request.form['ip'].strip()
    subnet = request.form['subnet'].strip()
    gateway = request.form['gateway'].strip()
    preferred_dns = request.form['preferred_dns'].strip()
    alternate_dns = request.form['alternate_dns'].strip()

    if all([ssid, ip, subnet, gateway, preferred_dns, alternate_dns]):
        # Basic validation (could be more robust with regex)
        # Ensure IP addresses are valid formats, e.g., 192.168.1.1
        # For simplicity, we'll assume valid input for now.
        config = load_or_create_config()
        config[ssid] = {
            "ip": ip,
            "subnet": subnet,
            "gateway": gateway,
            "preferred_dns": preferred_dns,
            "alternate_dns": alternate_dns
        }
        save_config(config)
        logging.info(f"[WEB] Config saved (overwritten) for SSID: {ssid}")
        return redirect(url_for('apply_config'))
    else:
        logging.warning("[WEB] Submission failed: Not all required fields were provided.")
        return "Error: All fields are required. Please go back and fill them."

@app.route('/apply_config')
def apply_config():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Configuration Saved</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 50px; text-align: center; }
            .message { background-color: #e6ffe6; border: 1px solid #00cc00; padding: 20px; border-radius: 8px; display: inline-block; }
            h2 { color: #008000; }
        </style>
    </head>
    <body>
        <div class="message">
            <h2>Configuration Saved!</h2>
            <p>Your configuration has been saved successfully.</p>
            <p>It will be applied automatically in the background when the corresponding Wi-Fi network is connected.</p>
            <p>You can now safely close this browser tab.</p>
        </div>
    </body>
    </html>
    """

# === Web and Tray ===
def start_flask_app():
    logging.info("[FLASK] Starting Flask app on http://127.0.0.1:5000/")
    try:
        # Use 0.0.0.0 to make it accessible from other devices on the network (if firewall allows)
        # or just 127.0.0.1 for local access only. Sticking to 127.0.0.1 for security.
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logging.critical(f"[FLASK] Flask app failed to start: {e}", exc_info=True)
        messagebox.showerror("Application Error", f"Failed to start web interface: {e}\nEnsure port 5000 is free and check log file.")

def open_browser():
    try:
        logging.info("[BROWSER] Opening browser to http://127.0.0.1:5000/")
        webbrowser.open("http://127.0.0.1:5000/")
    except Exception as e:
        logging.error(f"[BROWSER] Failed to open browser: {e}")
        messagebox.showwarning("Browser Error", f"Could not open browser automatically. Please open http://127.0.0.1:5000/ manually. Error: {e}")

def start_tray_icon():
    logging.info("[TRAY] Attempting to start system tray icon...")
    icon_to_use = None
    # Determine the path to the icon file within the PyInstaller bundle
    # sys._MEIPASS is the temporary directory where PyInstaller extracts files
    if hasattr(sys, '_MEIPASS'):
        bundle_dir = sys._MEIPASS
    else:
        bundle_dir = os.path.abspath(".") # For running outside of PyInstaller
    
    full_icon_path = os.path.join(bundle_dir, icon_path)

    try:
        if os.path.exists(full_icon_path):
            icon_to_use = Image.open(full_icon_path)
            logging.info(f"[TRAY] Successfully loaded icon from {full_icon_path}")
        else:
            logging.warning(f"[TRAY] Icon file not found at {full_icon_path}. Falling back to default generated image.")
            icon_to_use = Image.new("RGB", (64, 64), (0, 102, 204)) # Blue background
            draw = ImageDraw.Draw(icon_to_use)
            # Use a default font, checking if 'arial.ttf' is available or a generic one
            try:
                # Attempt to get a common system font, if not, PIL will use its default
                font = Image.core.getfont("arial.ttf", 30)
            except Exception:
                logging.warning("[TRAY] Arial font not found, using PIL's default font for fallback icon.")
                font = None # PIL will use its default font if None
            draw.text((10, 20), "IP", fill="white", font=font)
    except Exception as e:
        logging.error(f"[TRAY] CRITICAL ERROR loading icon from {full_icon_path}: {e}. Falling back to default generated image.", exc_info=True)
        icon_to_use = Image.new("RGB", (64, 64), (0, 102, 204)) # Blue background
        draw = ImageDraw.Draw(icon_to_use)
        try:
            font = Image.core.getfont("arial.ttf", 30)
        except Exception:
            font = None
        draw.text((10, 20), "IP", fill="white", font=font)

    if icon_to_use is None:
        logging.critical("[TRAY] Icon image is None after all attempts. Cannot create tray icon.")
        messagebox.showerror("Application Error", "Failed to create icon image. Check log file.")
        return # Cannot proceed without an icon image

    def show_logs(icon_instance, item):
        logging.info("[TRAY] 'View Log' clicked. Opening log file.")
        try:
            subprocess.Popen(["notepad", log_file])
        except Exception as e:
            logging.error(f"[TRAY] Failed to open logs in Notepad: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to open logs in Notepad: {e}")

    def open_change_ip_page(icon_instance, item):
        logging.info("[TRAY] 'Change IP Configuration' clicked. Deleting config and opening browser.")
        if os.path.exists(config_file):
            try:
                os.remove(config_file)
                logging.info("[TRAY] Configuration file deleted.")
            except Exception as e:
                logging.error(f"[TRAY] Failed to delete config file: {e}", exc_info=True)
                messagebox.showerror("Error", f"Failed to delete config file: {e}")
        webbrowser.open("http://127.0.0.1:5000/")

    def on_quit(icon_instance, item):
        logging.info("[TRAY] 'Quit' clicked. Initiating application shutdown.")
        try:
            # It's good practice to try to stop the Flask server as well,
            # though os._exit(0) will forcefully terminate all threads.
            # This requires a way to signal the Flask thread, e.g., a shared flag.
            # For simplicity with os._exit(0), we rely on forceful termination.
            icon_instance.stop() # This stops the pystray loop
            logging.info("[TRAY] pystray icon stopped.")
        except Exception as e:
            logging.error(f"[TRAY] Error stopping pystray icon: {e}", exc_info=True)
        finally:
            os._exit(0) # Force exit all threads, ensuring app closes

    menu = Menu(
        MenuItem("View Log", show_logs),
        MenuItem("Change IP Configuration", open_change_ip_page),
        MenuItem("Quit", on_quit)
    )
    
    try:
        logging.info("[TRAY] Creating pystray Icon instance with name 'WiFiIPSwitcher'...")
        tray_icon = Icon("WiFiIPSwitcher", icon_to_use, "Wi-Fi IP Switcher", menu)
        logging.info("[TRAY] Calling tray_icon.run(). This will block the thread until icon is stopped.")
        tray_icon.run() # This is the blocking call for the tray thread
        logging.info("[TRAY] tray_icon.run() has returned. This should only happen when icon is stopped (e.g., on quit).")
    except Exception as e:
        logging.critical(f"[TRAY] FATAL ERROR during pystray setup or run: {e}", exc_info=True)
        messagebox.showerror("Application Error", f"Failed to start system tray icon: {e}\nCheck {log_file} for detailed errors.")


# === Main ===
def main():
    logging.info("=== Wi-Fi Auto IP Switcher Application Started ===")

    if not is_admin():
        logging.warning("[MAIN] Not running as admin. Relaunching...")
        relaunch_as_admin()
        # The script exits here if relaunch_as_admin succeeds.
        # If it returns (e.g., if relaunch fails), we log and exit gracefully.
        logging.critical("[MAIN] Relaunch as admin failed or was cancelled. Exiting.")
        sys.exit(1)

    logging.info("[MAIN] Running with administrative privileges.")

    # Start Flask app first (non-daemon)
    flask_thread = threading.Thread(target=start_flask_app, name="FlaskThread")
    flask_thread.daemon = False
    flask_thread.start()
    logging.info("[MAIN] Flask thread started.")

    # Start SSID monitoring thread (non-daemon)
    monitor_thread = threading.Thread(target=monitor_ssid_loop, name="MonitorThread")
    monitor_thread.daemon = False
    monitor_thread.start()
    logging.info("[MAIN] Monitor thread started.")

    # Start tray icon in a non-daemon thread
    # This thread must be started after Flask and monitor threads if there's any dependency,
    # and especially after admin check is complete.
    tray_thread = threading.Thread(target=start_tray_icon, name="TrayThread")
    tray_thread.daemon = False # Essential for the thread to outlive the main thread
    tray_thread.start()
    logging.info("[MAIN] Tray thread started.")

    # Open browser only once, when config is missing
    # Give the Flask app a moment to start before opening the browser
    time.sleep(2) # Small delay to allow Flask server to initialize
    if not os.path.exists(config_file) or load_or_create_config() == {}:
        logging.info("[MAIN] Config file not found or empty, opening browser for initial setup.")
        open_browser()
    else:
        logging.info("[MAIN] Config file found. Application running in background.")

    # Keep main thread alive by joining threads.
    # Joining non-daemon threads will keep the main process alive until they finish.
    # Since these are meant to run indefinitely, the main thread will effectively wait here.
    try:
        logging.info("[MAIN] Main thread waiting for other threads to finish (should run indefinitely).")
        tray_thread.join()
        monitor_thread.join()
        flask_thread.join()
    except KeyboardInterrupt:
        logging.info("[MAIN] KeyboardInterrupt detected in main thread, attempting graceful shutdown (though os._exit(0) is primary).")
    except Exception as e:
        logging.critical(f"[MAIN] Unexpected error in main thread: {e}", exc_info=True)
    finally:
        logging.info("=== Wi-Fi Auto IP Switcher Application Exiting ===")


if __name__ == "__main__":
    # Ensure sys.frozen attribute is set by PyInstaller for conditional logic
    # if not hasattr(sys, 'frozen'):
    #     sys.frozen = False # Or True if running from source as if frozen for testing

    main()

# app.py file

    