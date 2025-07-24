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
from PIL import Image, ImageDraw
import idlelib.tree # Explicitly import for PyInstaller
import winreg # For checking/setting startup, though Scheduled Task is better

# --- Dummy MessageBox for headless environment ---
# This class provides dummy methods for showerror and showwarning
# to prevent crashes if tkinter.messagebox is not available (e.g., in --windowed builds).
# In a true background service, logging and tray notifications are preferred for feedback.
class DummyMessageBox:
    def showerror(self, title, message):
        logging.error(f"GUI Error (dummy messagebox): {title} - {message}")
    def showwarning(self, title, message):
        logging.warning(f"GUI Warning (dummy messagebox): {title} - {message}")

# Assign this dummy class to messagebox, as tkinter.messagebox is typically not bundled
# with --windowed builds and causes ImportError if directly imported and used.
messagebox = DummyMessageBox()


# === Constants ===
# Define base directory for user-specific data
# This will typically resolve to C:\Users\<username>\AppData\Local\WiFi_IP_Switcher
APP_DATA_DIR = os.path.join(os.getenv('LOCALAPPDATA'), "WiFi_IP_Switcher")

# Ensure the app data directory exists
# This must be done *before* the logging setup tries to write to it.
if not os.path.exists(APP_DATA_DIR):
    try:
        os.makedirs(APP_DATA_DIR)
        logging.info(f"[MAIN] Created application data directory: {APP_DATA_DIR}")
    except OSError as e:
        # Handle cases where directory creation might fail (e.g., permissions, though unlikely for AppData)
        print(f"ERROR: Could not create application data directory {APP_DATA_DIR}: {e}")
        sys.exit(1) # Critical error, cannot proceed without writable directory

# Define paths for configuration and log files within the application data directory
config_file = os.path.join(APP_DATA_DIR, "wifi_ip_config.json")
log_file = os.path.join(APP_DATA_DIR, "wifi_ip_switcher.log")
check_interval = 5  # seconds between SSID checks
icon_path = "wifi_ip_switcher.ico" # Path to your icon file (bundled by PyInstaller)

# === New Constant for Scheduled Task ===
TASK_NAME = "WiFiIPSwitcherStartupTask"

# === Logging Setup ===
# Configure logging to write to the specified log_file in APP_DATA_DIR
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(message)s', encoding='utf-8')
# Also add a console handler for immediate feedback during development/debugging
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(console_handler) # Add to the root logger


# === Flask App ===
app = Flask(__name__)

# === Admin Check ===
def is_admin():
    """Checks if the current process is running with administrative privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        logging.error(f"Error checking admin privileges: {e}", exc_info=True)
        return False

# === Scheduled Task Functions ===
def create_scheduled_task():
    """
    Creates a Windows Scheduled Task to run the application with highest privileges on user logon.
    This task ensures the app starts automatically with admin rights without UAC prompts
    after the initial setup.
    """
    logging.info(f"[TASK] Attempting to create Scheduled Task '{TASK_NAME}'...")
    try:
        # sys.executable gives the path to the current executable (e.g., app.exe when frozen)
        exe_path = sys.executable
        task_args = "" # No special arguments needed for the task

        # Construct the schtasks command to create the task
        command = [
            "schtasks", "/create", "/tn", TASK_NAME,
            "/tr", f'"{exe_path} {task_args}"', # /tr: Task Run (program to execute)
            "/sc", "ONLOGON", # /sc: Schedule type (ONLOGON: runs when any user logs on)
            "/rl", "HIGHEST", # /rl: Run Level (HIGHEST: runs with highest privileges)
            "/f" # /f: Force (overwrite if task already exists)
        ]
        
        # Run the command, suppressing the console window
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True, # Raise CalledProcessError for non-zero exit codes
            creationflags=subprocess.CREATE_NO_WINDOW # Hide console window
        )
        logging.info(f"[TASK] Scheduled Task '{TASK_NAME}' created successfully.")
        logging.debug(f"[TASK] schtasks output: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"[TASK] Failed to create Scheduled Task: Command '{' '.join(e.cmd)}' failed with error: {e.stderr.strip()}", exc_info=True)
        return False
    except Exception as e:
        logging.error(f"[TASK] An unexpected error occurred while creating Scheduled Task: {e}", exc_info=True)
        return False

def is_scheduled_task_created():
    """Checks if the specified Windows Scheduled Task already exists."""
    logging.info(f"[TASK] Checking if Scheduled Task '{TASK_NAME}' exists...")
    try:
        # Command to query for the task
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0: # schtasks /query returns 0 if task exists
            logging.info(f"[TASK] Scheduled Task '{TASK_NAME}' found.")
            return True
        else: # Non-zero return code means task not found or error
            logging.info(f"[TASK] Scheduled Task '{TASK_NAME}' not found (Return Code: {result.returncode}).")
            logging.debug(f"[TASK] schtasks query stderr: {result.stderr.strip()}")
            return False
    except Exception as e:
        logging.error(f"[TASK] Error checking Scheduled Task existence: {e}", exc_info=True)
        return False

# === IP Functions ===
def run_netsh_command(command_args):
    """Helper to run netsh commands and suppress console window."""
    try:
        result = subprocess.run(
            command_args,
            capture_output=True,
            text=True,
            check=True, # Raise CalledProcessError for non-zero exit codes
            creationflags=subprocess.CREATE_NO_WINDOW # Hide console window
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        logging.error(f"Netsh command failed: '{' '.join(e.cmd)}' - Error: {e.stderr.strip()}", exc_info=True)
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred running netsh: {e}", exc_info=True)
        return None

# === Wi-Fi Interface Detection ===
# This variable will be initialized in main() after admin checks
interface_name = None 

def get_wifi_interface_name():
    """Dynamically retrieves the name of the active Wi-Fi interface."""
    logging.info("[INTERFACE] Attempting to detect Wi-Fi interface name...")
    output = run_netsh_command(["netsh", "wlan", "show", "interfaces"])
    if output:
        for line in output.splitlines():
            if "Name" in line and "SSID" not in line: # Avoid matching "SSID name" line
                parts = line.split(":", 1)
                if len(parts) == 2:
                    name = parts[1].strip()
                    if name: # Ensure name is not empty
                        logging.info(f"[INTERFACE] Detected Wi-Fi interface name: '{name}'")
                        return name
    logging.warning("[INTERFACE] Could not detect Wi-Fi interface name. Defaulting to 'Wi-Fi' (may not work).")
    return "Wi-Fi" # Fallback, but often "Wi-Fi" is the default on Windows


def get_connected_ssid():
    """Retrieves the SSID of the currently connected Wi-Fi network."""
    output = run_netsh_command(["netsh", "wlan", "show", "interfaces"])
    if output:
        for line in output.splitlines():
            if "SSID" in line and "BSSID" not in line: # Look for SSID, not BSSID
                ssid_value = line.split(":", 1)[1].strip()
                return ssid_value.strip('"') # Remove quotes if present
    return None

def get_current_ip(interface):
    """Retrieves the current IP address of the specified network interface."""
    output = run_netsh_command(["netsh", "interface", "ip", "show", "config", f"name={interface}"])
    if output:
        for line in output.splitlines():
            if "IP Address" in line:
                return line.split(":")[1].strip()
    return None

def set_static_ip(interface, ip, subnet, gateway, preferred_dns, alternate_dns):
    """Sets a static IP configuration for the specified network interface."""
    logging.info(f"[NETWORK] Attempting to set static IP for '{interface}': {ip}")
    success = True
    # Set IP address, subnet mask, and gateway
    if not run_netsh_command(["netsh", "interface", "ip", "set", "address", interface, "static", ip, subnet, gateway]):
        success = False
    
    # Set primary DNS
    if success and not run_netsh_command(["netsh", "interface", "ip", "set", "dns", interface, "static", preferred_dns, "primary"]):
        success = False
    
    # Add alternate DNS
    if success and alternate_dns and not run_netsh_command(["netsh", "interface", "ip", "add", "dns", interface, alternate_dns, "index=2"]):
        success = False

    if success:
        logging.info(f"[NETWORK] Static IP set successfully for '{interface}': {ip}")
    else:
        logging.error(f"[NETWORK] Failed to set static IP for '{interface}'. Check logs for details.")
        # Consider a tray notification here if critical for user feedback.

def set_dhcp_ip(interface):
    """Sets the network interface to obtain IP and DNS settings automatically via DHCP."""
    logging.info(f"[NETWORK] Attempting to set DHCP for '{interface}'...")
    success = True
    # Set IP address to DHCP
    if not run_netsh_command(["netsh", "interface", "ip", "set", "address", interface, "dhcp"]):
        success = False
    # Set DNS to DHCP
    if success and not run_netsh_command(["netsh", "interface", "ip", "set", "dns", interface, "dhcp"]):
        success = False

    if success:
        logging.info(f"[NETWORK] Switched to DHCP successfully for '{interface}'")
    else:
        logging.error(f"[NETWORK] Failed to switch to DHCP for '{interface}'. Check logs for details.")
        # Consider a tray notification here if critical for user feedback.


# === Config Handling ===
def load_or_create_config():
    """Loads configuration from file, or returns an empty dict if not found/corrupted."""
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding='utf-8') as f:
                config_data = json.load(f)
                logging.info("[CONFIG] Configuration loaded successfully.")
                return config_data
        except json.JSONDecodeError as e:
            logging.error(f"[CONFIG] Corrupted config file '{config_file}': {e}. Deleting and creating new one.", exc_info=True)
            try:
                os.remove(config_file)
            except Exception as e_del:
                logging.error(f"[CONFIG] Error deleting corrupted config file: {e_del}", exc_info=True)
            return {}
        except Exception as e:
            logging.error(f"[CONFIG] Error loading config file '{config_file}': {e}", exc_info=True)
            return {}
    logging.info("[CONFIG] Config file not found, returning empty config.")
    return {}

def save_config(config):
    """Saves the current configuration to file."""
    try:
        with open(config_file, "w", encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        logging.info("[CONFIG] Configuration saved successfully.")
    except Exception as e:
        logging.error(f"[CONFIG] Error saving config file '{config_file}': {e}", exc_info=True)

# === Auto IP Switching Logic ===
def monitor_ssid_loop():
    """
    Main loop that continuously monitors the connected SSID and applies
    the corresponding IP configuration from the loaded config.
    """
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
                    # Check if we currently have a static IP set (not DHCP)
                    # A more robust check for DHCP would involve parsing 'netsh interface ip show config' for "DHCP Enabled: Yes"
                    # For simplicity, if current_ip is not None, we assume it might be static and try to revert.
                    current_ip_check = get_current_ip(interface_name)
                    if current_ip_check and current_ip_check != "0.0.0.0": # "0.0.0.0" is sometimes reported for DHCP but not definitive
                         logging.info(f"[MONITOR] SSID '{ssid}' (or no SSID) not in config. Ensuring DHCP is enabled.")
                         set_dhcp_ip(interface_name)
                    else:
                         logging.info("[MONITOR] No static IP found for interface or already on DHCP. No change needed.")
            time.sleep(check_interval)
        except Exception as e:
            logging.error(f"[MONITOR] Exception in monitor_ssid_loop: {e}", exc_info=True)
            time.sleep(check_interval)

# === Flask Routes ===
@app.route('/')
def index():
    """Renders the main configuration page."""
    config = load_or_create_config()
    return render_template('index.html', existing_config=config)

@app.route('/submit', methods=['POST'])
def submit_config():
    """Handles submission of new/updated IP configurations."""
    ssid = request.form['ssid'].strip()
    ip = request.form['ip'].strip()
    subnet = request.form['subnet'].strip()
    gateway = request.form['gateway'].strip()
    preferred_dns = request.form['preferred_dns'].strip()
    alternate_dns = request.form['alternate_dns'].strip()

    if all([ssid, ip, subnet, gateway, preferred_dns]): # Alternate DNS is optional
        # Basic validation (could be more robust with regex for IP formats)
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
        return "Error: All fields are required (SSID, IP, Subnet, Gateway, Preferred DNS)."

@app.route('/apply_config')
def apply_config():
    """Simple confirmation page after config submission."""
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
    """Starts the Flask web server."""
    logging.info("[FLASK] Starting Flask app on http://127.0.0.1:5000/")
    try:
        # Use 127.0.0.1 for local access only for security.
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logging.critical(f"[FLASK] Flask app failed to start: {e}", exc_info=True)
        # In a production app, you might want to exit or notify user more prominently.

def open_browser():
    """Opens the default web browser to the Flask app URL."""
    try:
        logging.info("[BROWSER] Opening browser to http://127.0.0.1:5000/")
        webbrowser.open("http://127.0.0.1:5000/")
    except Exception as e:
        logging.error(f"[BROWSER] Failed to open browser: {e}", exc_info=True)
        # Use dummy messagebox if needed for user feedback
        messagebox.showwarning("Browser Error", f"Could not open browser automatically. Please open http://127.0.0.1:5000/ manually. Error: {e}")

def start_tray_icon():
    """Initializes and runs the system tray icon."""
    logging.info("[TRAY] Attempting to start system tray icon...")
    icon_to_use = None
    # Determine the path to the icon file within the PyInstaller bundle
    # sys._MEIPASS is the temporary directory where PyInstaller extracts files in onefile mode,
    # or the base directory in onedir mode.
    if hasattr(sys, '_MEIPASS'):
        bundle_dir = sys._MEIPASS
    else:
        bundle_dir = os.path.abspath(".") # For running outside of PyInstaller (e.g., from source)
    
    full_icon_path = os.path.join(bundle_dir, icon_path)

    try:
        if os.path.exists(full_icon_path):
            icon_to_use = Image.open(full_icon_path)
            logging.info(f"[TRAY] Successfully loaded icon from {full_icon_path}")
        else:
            logging.warning(f"[TRAY] Icon file not found at {full_icon_path}. Falling back to default generated image.")
            # Create a simple blue square with "IP" text as a fallback icon
            icon_to_use = Image.new("RGB", (64, 64), (0, 102, 204)) # Blue background
            draw = ImageDraw.Draw(icon_to_use)
            try:
                # Attempt to get a common system font, if not, PIL will use its default
                # This might still fail if font is not found, but PIL will use a fallback.
                # ImageFont.truetype requires font file, Image.core.getfont is simpler.
                font = Image.core.getfont("arial.ttf", 30) 
            except Exception:
                logging.warning("[TRAY] Arial font not found, using PIL's default font for fallback icon text.")
                font = None # PIL will use its default font if None
            draw.text((10, 20), "IP", fill="white", font=font)
    except Exception as e:
        logging.critical(f"[TRAY] CRITICAL ERROR loading icon from {full_icon_path}: {e}. Falling back to default generated image.", exc_info=True)
        # Ensure a fallback icon is always created even if primary loading fails
        icon_to_use = Image.new("RGB", (64, 64), (0, 102, 204))
        draw = ImageDraw.Draw(icon_to_use)
        try:
            font = Image.core.getfont("arial.ttf", 30)
        except Exception:
            font = None
        draw.text((10, 20), "IP", fill="white", font=font)

    if icon_to_use is None:
        logging.critical("[TRAY] Icon image is None after all attempts. Cannot create tray icon. Exiting.")
        messagebox.showerror("Application Error", "Failed to create icon image. Check log file.")
        os._exit(1) # Exit if no icon can be created

    def show_logs(icon_instance, item):
        logging.info("[TRAY] 'View Log' clicked. Opening log file.")
        try:
            subprocess.Popen(["notepad", log_file]) # Open log file in Notepad
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
            icon_instance.stop() # This stops the pystray loop
            logging.info("[TRAY] pystray icon stopped.")
        except Exception as e:
            logging.error(f"[TRAY] Error stopping pystray icon: {e}", exc_info=True)
        finally:
            os._exit(0) # Force exit all threads, ensuring app closes

    # Define the menu items for the tray icon
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
        os._exit(1) # Exit if tray icon fails to start

# === Main Application Entry Point ===
def main():
    logging.info("=== Wi-Fi Auto IP Switcher Application Started ===")

    # Initialize interface_name here, after the APP_DATA_DIR is set up
    # but before any netsh calls that depend on admin privileges.
    # The actual value will be retrieved only if admin is confirmed.
    global interface_name 

    # Logic for initial setup (creating scheduled task) vs. normal run
    if not is_scheduled_task_created():
        logging.info("[MAIN] Scheduled task not found.")
        if not is_admin():
            logging.warning("[MAIN] Not running as admin on first run (scheduled task creation). Requesting elevation for this session.")
            # This is the *only* time we explicitly ask for admin via UAC.
            # Relaunch the current executable with 'runas' verb.
            params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
            try:
                # ShellExecuteW will trigger the UAC prompt
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, f'"{sys.argv[0]}" {params}', None, 1
                )
                logging.info("[MAIN] Relaunching for admin setup. Original process exiting.")
            except Exception as e:
                logging.critical(f"[ADMIN] Failed to relaunch for setup as admin: {e}", exc_info=True)
                print(f"ERROR: Failed to get admin privileges to set up startup task. Please run as administrator once manually. Error: {e}")
                messagebox.showerror("Admin Privileges Required", f"Failed to get admin privileges to set up startup task. Please run as administrator once manually. Error: {e}")
            sys.exit(1) # Exit the non-admin process, the elevated one will continue

        else: # is_admin() is True and task is not created
            logging.info("[MAIN] Running as admin. Creating scheduled task for future startup.")
            if not create_scheduled_task():
                logging.error("[MAIN] Failed to create scheduled task. Application will not start on reboot without UAC prompt.")
                messagebox.showerror("Startup Setup Error", "Failed to create scheduled task for auto-startup. The application will not start automatically with admin privileges on reboot. Please check the log file.")
            else:
                logging.info("[MAIN] Scheduled task created successfully.")
                # After creating the task, we can proceed with the normal app flow.
                # If we've just created the task, it means this is effectively the "installation" run.
                # The browser will be opened if config is missing.
    else:
        logging.info("[MAIN] Scheduled task found. Running with assumed administrative privileges via task.")
        # If the task exists, we assume we were launched by it, and thus are already admin.
        # No UAC prompt for subsequent runs.
        # If the user somehow launches it manually without admin, netsh calls will fail silently.
        # This is the intended behavior for "no UAC prompt" after initial setup.


    # Now that admin privileges are (hopefully) established or handled,
    # we can safely get the interface name which requires admin.
    interface_name = get_wifi_interface_name()
    if not interface_name:
        logging.critical("[MAIN] Could not determine Wi-Fi interface name. Exiting application as network operations will fail.")
        messagebox.showerror("Initialization Error", "Could not determine Wi-Fi interface name. The application cannot function without it. Please check your Wi-Fi adapter and logs.")
        sys.exit(1) # Cannot proceed without interface name

    # Start Flask app (non-daemon)
    flask_thread = threading.Thread(target=start_flask_app, name="FlaskThread")
    flask_thread.daemon = False # Flask thread should not be daemon if it needs to handle requests
    flask_thread.start()
    logging.info("[MAIN] Flask thread started.")

    # Start SSID monitoring thread (non-daemon)
    monitor_thread = threading.Thread(target=monitor_ssid_loop, name="MonitorThread")
    monitor_thread.daemon = False # Monitor thread should not be daemon if it's critical
    monitor_thread.start()
    logging.info("[MAIN] Monitor thread started.")

    # Start tray icon in a non-daemon thread
    tray_thread = threading.Thread(target=start_tray_icon, name="TrayThread")
    tray_thread.daemon = False # Essential for the thread to outlive the main thread
    tray_thread.start()
    logging.info("[MAIN] Tray thread started.")

    # Open browser only once, when config is missing
    # Give the Flask app a moment to start before opening the browser
    time.sleep(2) # Small delay to allow Flask server to initialize
    # The condition for opening the browser:
    # 1. First run ever (config file doesn't exist).
    # 2. User explicitly clicked "Change IP Configuration" from tray (which deletes config file).
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
        # Join the tray thread first, as its stop() method is called on quit,
        # which then leads to os._exit(0) for the whole process.
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
    main()
