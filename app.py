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

# === Constants ===
interface_name = "Wi-Fi"
config_file = "wifi_ip_config.json"
log_file = "wifi_ip_switcher.log"

# === Logging Setup ===
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(message)s')

# === Admin Check and Elevation ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def relaunch_as_admin():
    logging.info("[↑] Requesting Admin privileges...")
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{__file__}"', None, 1)
    sys.exit()

# === Get Connected SSID ===
def get_connected_ssid():
    try:
        output = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], text=True)
        for line in output.splitlines():
            if "SSID" in line and "BSSID" not in line:
                return line.split(":", 1)[1].strip()
        return None
    except:
        return None

# === Get Current IP ===
def get_current_ip(interface):
    try:
        output = subprocess.check_output(["netsh", "interface", "ip", "show", "config", f"name={interface}"], text=True)
        for line in output.splitlines():
            if "IP Address" in line:
                return line.split(":")[1].strip()
        return None
    except:
        return None

# === Set Static IP + DNS ===
def set_static_ip(interface, ip, subnet, gateway, preferred_dns, alternate_dns):
    try:
        subprocess.run([
            "netsh", "interface", "ip", "set", "address",
            interface, "static", ip, subnet, gateway
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        subprocess.run([
            "netsh", "interface", "ip", "set", "dns",
            interface, "static", preferred_dns, "primary"
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        subprocess.run([
            "netsh", "interface", "ip", "add", "dns",
            interface, alternate_dns, "index=2"
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        logging.info(f"[✓] Static IP and DNS set: {ip}, DNS: {preferred_dns}, {alternate_dns}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[ERROR] Failed to set static IP/DNS: {e}")

# === Set Dynamic IP (DHCP) ===
def set_dhcp_ip(interface):
    try:
        subprocess.run([
            "netsh", "interface", "ip", "set", "address",
            interface, "dhcp"
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        subprocess.run([
            "netsh", "interface", "ip", "set", "dns",
            interface, "dhcp"
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        logging.info("[✓] Switched to DHCP.")
    except subprocess.CalledProcessError as e:
        logging.error(f"[ERROR] Failed to set DHCP: {e}")

# === Load or Create Config ===
def load_or_create_config():
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error("[ERROR] Config file is corrupted.")
            os.remove(config_file)
            return None
    return None

def save_config(config):
    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)

# === Flask Web App ===
app = Flask(__name__)

@app.route('/')
def index():
    config = load_or_create_config()
    if config:
        return redirect(url_for('apply_config'))  # Skip input if config already exists
    return render_template('index.html')  # Serve HTML form for input

@app.route('/submit', methods=['POST'])
def submit_config():
    ssid = request.form['ssid']
    ip = request.form['ip']
    subnet = request.form['subnet']
    gateway = request.form['gateway']
    preferred_dns = request.form['preferred_dns']
    alternate_dns = request.form['alternate_dns']

    # Validate inputs
    if ssid and ip and subnet and gateway and preferred_dns and alternate_dns:
        ssid_ip_map = load_or_create_config() or {}
        ssid_ip_map[ssid] = {
            "ip": ip,
            "subnet": subnet,
            "gateway": gateway,
            "preferred_dns": preferred_dns,
            "alternate_dns": alternate_dns
        }
        save_config(ssid_ip_map)
        logging.info(f"[INFO] Configuration saved for SSID {ssid}.")
        return redirect(url_for('apply_config'))  # Apply the configuration after saving
    else:
        return "Error: All fields are required."

@app.route('/apply_config')
def apply_config():
    # Apply the configuration to the network settings
    ssid_ip_map = load_or_create_config()
    ssid = get_connected_ssid()

    if ssid and ssid in ssid_ip_map:
        config = ssid_ip_map[ssid]
        current_ip = get_current_ip(interface_name)

        if current_ip != config["ip"]:
            logging.info("[+] Applying static IP configuration...")
            set_static_ip(
                interface_name,
                config["ip"],
                config["subnet"],
                config["gateway"],
                config["preferred_dns"],
                config["alternate_dns"]
            )
        else:
            logging.info("[✓] IP already correctly set.")
    else:
        logging.info("[INFO] Unknown SSID. Switching to DHCP...")
        set_dhcp_ip(interface_name)

    return "Configuration Applied Successfully!"

# === Start Flask and Open Web Browser ===
def start_flask_app():
    app.run(debug=True, use_reloader=False)

def open_browser():
    # Open the Flask app URL in the default web browser
    webbrowser.open("http://127.0.0.1:5000/")

def start_tray_icon():
    icon = Image.new("RGB", (64, 64), (255, 255, 255))
    draw = ImageDraw.Draw(icon)
    draw.rectangle((0, 0, 64, 64), fill="blue")

    def show_logs(icon, item):
        with open(log_file, "r") as log:
            logs = log.read()
        messagebox.showinfo("Log File", logs)

    def on_quit(icon, item):
        logging.info("[INFO] Application Quit.")
        icon.stop()

    menu = Menu(MenuItem("View Log", show_logs), MenuItem("Quit", on_quit))
    tray_icon = Icon("wifi_ip_switcher", icon, menu=menu)
    tray_icon.run()

def main():
    logging.info("=== Wi-Fi Auto IP Switcher Running ===")

    if not is_admin():
        relaunch_as_admin()

    # Start system tray icon
    tray_thread = threading.Thread(target=start_tray_icon)
    tray_thread.daemon = True
    tray_thread.start()

    # Check if config exists
    if not os.path.exists(config_file):
        # If no config file exists, open the Flask app and browser
        open_browser()
        start_flask_app()
    else:
        # If the config file exists, apply the configuration
        apply_config()
    
if __name__ == "__main__":
    main()
