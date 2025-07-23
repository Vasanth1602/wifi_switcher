# import ctypes
# import os
# import subprocess
# import sys
# import time
# import json
# import logging
# import webbrowser
# import threading
# from flask import Flask, render_template, request, redirect, url_for
# from pystray import Icon, MenuItem, Menu
# from PIL import Image, ImageDraw
# import tkinter.messagebox as messagebox

# # === Constants ===
# interface_name = "Wi-Fi"
# config_file = "wifi_ip_config.json"
# log_file = "wifi_ip_switcher.log"
# check_interval = 5  # seconds between SSID checks

# # === Logging Setup ===
# logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(message)s')

# # === Flask App ===
# app = Flask(__name__)

# # === Admin Check ===
# def is_admin():
#     try:
#         return ctypes.windll.shell32.IsUserAnAdmin()
#     except:
#         return False

# def relaunch_as_admin():
#     logging.info("[↑] Requesting Admin privileges...")
#     # Pass command line arguments, and use sys.argv[0] instead of __file__ for safety
#     params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
#     ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{sys.argv[0]}" {params}', None, 1)
#     sys.exit()

# # === IP Functions ===
# def get_connected_ssid():
#     try:
#         output = subprocess.check_output(["netsh", "wlan", "show", "interfaces"], text=True)
#         for line in output.splitlines():
#             if "SSID" in line and "BSSID" not in line:
#                 return line.split(":", 1)[1].strip()
#     except Exception as e:
#         logging.error(f"Error getting SSID: {e}")
#         return None
#     return None

# def get_current_ip(interface):
#     try:
#         output = subprocess.check_output(["netsh", "interface", "ip", "show", "config", f"name={interface}"], text=True)
#         for line in output.splitlines():
#             if "IP Address" in line:
#                 return line.split(":")[1].strip()
#     except Exception as e:
#         logging.error(f"Error getting current IP: {e}")
#         return None
#     return None

# def set_static_ip(interface, ip, subnet, gateway, preferred_dns, alternate_dns):
#     try:
#         subprocess.run(["netsh", "interface", "ip", "set", "address", interface, "static", ip, subnet, gateway],
#                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
#         subprocess.run(["netsh", "interface", "ip", "set", "dns", interface, "static", preferred_dns, "primary"],
#                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
#         subprocess.run(["netsh", "interface", "ip", "add", "dns", interface, alternate_dns, "index=2"],
#                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
#         logging.info(f"[✓] Static IP set for {interface}: {ip}")
#     except subprocess.CalledProcessError as e:
#         logging.error(f"[ERROR] Static IP failed: {e}")

# def set_dhcp_ip(interface):
#     try:
#         subprocess.run(["netsh", "interface", "ip", "set", "address", interface, "dhcp"],
#                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
#         subprocess.run(["netsh", "interface", "ip", "set", "dns", interface, "dhcp"],
#                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
#         logging.info("[✓] Switched to DHCP")
#     except subprocess.CalledProcessError as e:
#         logging.error(f"[ERROR] DHCP failed: {e}")

# # === Config Handling ===
# def load_or_create_config():
#     if os.path.exists(config_file):
#         try:
#             with open(config_file, "r") as f:
#                 return json.load(f)
#         except json.JSONDecodeError:
#             logging.error("[ERROR] Corrupted config file.")
#             os.remove(config_file)
#     return {}

# def save_config(config):
#     with open(config_file, "w") as f:
#         json.dump(config, f, indent=4)

# # === Auto IP Switching Logic ===
# def monitor_ssid_loop():
#     last_ssid = None
#     while True:
#         try:
#             ssid = get_connected_ssid()
#             config = load_or_create_config()

#             if ssid != last_ssid:
#                 last_ssid = ssid
#                 logging.info(f"[INFO] Connected SSID: {ssid}")

#                 if ssid and ssid in config:
#                     ip_config = config[ssid]
#                     current_ip = get_current_ip(interface_name)
#                     if current_ip != ip_config["ip"]:
#                         set_static_ip(
#                             interface_name,
#                             ip_config["ip"],
#                             ip_config["subnet"],
#                             ip_config["gateway"],
#                             ip_config["preferred_dns"],
#                             ip_config["alternate_dns"]
#                         )
#                     else:
#                         logging.info("[✓] IP already set.")
#                 else:
#                     set_dhcp_ip(interface_name)
#             time.sleep(check_interval)
#         except Exception as e:
#             logging.error(f"Exception in monitor_ssid_loop: {e}")
#             time.sleep(check_interval)

# # === Flask Routes ===
# @app.route('/')
# def index():
#     config = load_or_create_config()
#     if config:
#         return redirect(url_for('apply_config'))
#     return render_template('index.html')

# @app.route('/submit', methods=['POST'])
# def submit_config():
#     ssid = request.form['ssid']
#     ip = request.form['ip']
#     subnet = request.form['subnet']
#     gateway = request.form['gateway']
#     preferred_dns = request.form['preferred_dns']
#     alternate_dns = request.form['alternate_dns']

#     if all([ssid, ip, subnet, gateway, preferred_dns, alternate_dns]):
#         config = load_or_create_config()
#         config[ssid] = {
#             "ip": ip,
#             "subnet": subnet,
#             "gateway": gateway,
#             "preferred_dns": preferred_dns,
#             "alternate_dns": alternate_dns
#         }
#         save_config(config)
#         logging.info(f"[INFO] Config saved for SSID: {ssid}")
#         return redirect(url_for('apply_config'))
#     else:
#         return "Error: All fields are required."


# @app.route('/apply_config')
# def apply_config():
#     return "Configuration will be applied automatically in the background."

# # === Web and Tray ===
# def start_flask_app():
#     app.run(debug=False, use_reloader=False)

# def open_browser():
#     webbrowser.open("http://127.0.0.1:5000/")

# def start_tray_icon():
#     icon_img = Image.new("RGB", (64, 64), (0, 102, 204))
#     draw = ImageDraw.Draw(icon_img)
#     draw.text((10, 20), "IP", fill="white")

#     def show_logs(icon, item):
#         try:
#             with open(log_file, "r") as log:
#                 logs = log.read()
#             messagebox.showinfo("Log File", logs)
#         except Exception as e:
#             messagebox.showerror("Error", f"Failed to read logs: {e}")

#     def on_quit(icon, item):
#         logging.info("[INFO] Application quit.")
#         icon.stop()
#         os._exit(0)

#     menu = Menu(MenuItem("View Log", show_logs), MenuItem("Quit", on_quit))
#     tray_icon = Icon("wifi_ip_switcher", icon_img, menu=menu)
#     tray_icon.run()

# # === Main ===
# def main():
#     logging.info("=== Wi-Fi Auto IP Switcher Started ===")

#     if not is_admin():
#         relaunch_as_admin()

#     # Start tray icon in a non-daemon thread so it stays alive
#     tray_thread = threading.Thread(target=start_tray_icon)
#     tray_thread.start()

#     # Start SSID monitoring thread, also non-daemon
#     monitor_thread = threading.Thread(target=monitor_ssid_loop)
#     monitor_thread.start()

#     # Always start Flask app in a thread so it doesn't block
#     flask_thread = threading.Thread(target=start_flask_app)
#     flask_thread.start()

#     # Open browser only once, when config is missing
#     if not os.path.exists(config_file):
#         open_browser()

#     # Keep main thread alive by joining threads (or infinite loop)
#     # Here join threads so main waits for them
#     tray_thread.join()
#     monitor_thread.join()
#     flask_thread.join()

# if __name__ == "__main__":
#     main()


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
import tkinter.messagebox as messagebox

# === Constants ===
interface_name = "Wi-Fi"
config_file = "wifi_ip_config.json"
log_file = "wifi_ip_switcher.log"
check_interval = 5  # seconds between SSID checks
icon_path = "wifi_ip_switcher.ico" # Define your icon path here

# === Logging Setup ===
logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(message)s')

# === Flask App ===
app = Flask(__name__)

# === Admin Check ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def relaunch_as_admin():
    logging.info("[↑] Requesting Admin privileges...")
    params = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{sys.argv[0]}" {params}', None, 1)
    sys.exit()

# === IP Functions ===
def get_connected_ssid():
    try:
        output = subprocess.check_output(["netsh", "wlan", "show", "interfaces"],
                                         text=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
        for line in output.splitlines():
            if "SSID" in line and "BSSID" not in line:
                return line.split(":", 1)[1].strip()
    except Exception as e:
        logging.error(f"Error getting SSID: {e}")
        return None
    return None

def get_current_ip(interface):
    try:
        output = subprocess.check_output(["netsh", "interface", "ip", "show", "config", f"name={interface}"],
                                         text=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
        for line in output.splitlines():
            if "IP Address" in line:
                return line.split(":")[1].strip()
    except Exception as e:
        logging.error(f"Error getting current IP: {e}")
        return None
    return None

def set_static_ip(interface, ip, subnet, gateway, preferred_dns, alternate_dns):
    try:
        subprocess.run(["netsh", "interface", "ip", "set", "address", interface, "static", ip, subnet, gateway],
                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["netsh", "interface", "ip", "set", "dns", interface, "static", preferred_dns, "primary"],
                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["netsh", "interface", "ip", "add", "dns", interface, alternate_dns, "index=2"],
                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        logging.info(f"[✓] Static IP set for {interface}: {ip}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[ERROR] Static IP failed: {e}")

def set_dhcp_ip(interface):
    try:
        subprocess.run(["netsh", "interface", "ip", "set", "address", interface, "dhcp"],
                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        subprocess.run(["netsh", "interface", "ip", "set", "dns", interface, "dhcp"],
                        check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        logging.info("[✓] Switched to DHCP")
    except subprocess.CalledProcessError as e:
        logging.error(f"[ERROR] DHCP failed: {e}")

# === Config Handling ===
def load_or_create_config():
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error("[ERROR] Corrupted config file.")
            os.remove(config_file)
    return {}

def save_config(config):
    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)

# === Auto IP Switching Logic ===
def monitor_ssid_loop():
    last_ssid = None
    while True:
        try:
            ssid = get_connected_ssid()
            config = load_or_create_config()

            if ssid != last_ssid:
                last_ssid = ssid
                logging.info(f"[INFO] Connected SSID: {ssid}")

                if ssid and ssid in config:
                    ip_config = config[ssid]
                    current_ip = get_current_ip(interface_name)
                    if current_ip != ip_config["ip"]:
                        logging.info(f"[INFO] SSID '{ssid}' detected. Setting static IP.")
                        set_static_ip(
                            interface_name,
                            ip_config["ip"],
                            ip_config["subnet"],
                            ip_config["gateway"],
                            ip_config["preferred_dns"],
                            ip_config["alternate_dns"]
                        )
                    else:
                        logging.info("[✓] IP already set for current SSID.")
                else:
                    logging.info(f"[INFO] SSID '{ssid}' not in config or no SSID. Switching to DHCP.")
                    set_dhcp_ip(interface_name)
            time.sleep(check_interval)
        except Exception as e:
            logging.error(f"Exception in monitor_ssid_loop: {e}")
            time.sleep(check_interval)

# === Flask Routes ===
@app.route('/')
def index():
    config = load_or_create_config()
    return render_template('index.html', existing_config=config)

@app.route('/submit', methods=['POST'])
def submit_config():
    ssid = request.form['ssid']
    ip = request.form['ip']
    subnet = request.form['subnet']
    gateway = request.form['gateway']
    preferred_dns = request.form['preferred_dns']
    alternate_dns = request.form['alternate_dns']

    if all([ssid, ip, subnet, gateway, preferred_dns, alternate_dns]):
        config = load_or_create_config()
        config[ssid] = {
            "ip": ip,
            "subnet": subnet,
            "gateway": gateway,
            "preferred_dns": preferred_dns,
            "alternate_dns": alternate_dns
        }
        save_config(config)
        logging.info(f"[INFO] Config saved (overwritten) for SSID: {ssid}")
        return redirect(url_for('apply_config'))
    else:
        return "Error: All fields are required."

@app.route('/apply_config')
def apply_config():
    return "Configuration has been saved and will be applied automatically in the background when the corresponding Wi-Fi network is connected. You can now close this browser tab."

# === Web and Tray ===
def start_flask_app():
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)

def open_browser():
    webbrowser.open("http://127.0.0.1:5000/")

def start_tray_icon():
    # Load the icon from the .ico file
    try:
        # For pystray, you should load the image using PIL's Image.open
        icon_img = Image.open(icon_path)
    except FileNotFoundError:
        logging.error(f"[ERROR] Icon file not found at {icon_path}. Using a default image.")
        # Fallback to a generated icon if the .ico file isn't found
        icon_img = Image.new("RGB", (64, 64), (0, 102, 204)) # Blue background
        draw = ImageDraw.Draw(icon_img)
        draw.text((10, 20), "IP", fill="white", font=Image.core.getfont("arial.ttf", 30))
    except Exception as e:
        logging.error(f"[ERROR] Failed to load icon from {icon_path}: {e}. Using a default image.")
        icon_img = Image.new("RGB", (64, 64), (0, 102, 204)) # Blue background
        draw = ImageDraw.Draw(icon_img)
        draw.text((10, 20), "IP", fill="white", font=Image.core.getfont("arial.ttf", 30))


    def show_logs(icon, item):
        try:
            subprocess.Popen(["notepad", log_file])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open logs in Notepad: {e}")

    def open_change_ip_page(icon, item):
        if os.path.exists(config_file):
            try:
                os.remove(config_file)
                logging.info("[INFO] Configuration file deleted.")
            except Exception as e:
                logging.error(f"[ERROR] Failed to delete config file: {e}")
                messagebox.showerror("Error", f"Failed to delete config file: {e}")

        webbrowser.open("http://127.0.0.1:5000/")

    def on_quit(icon, item):
        logging.info("[INFO] Application quit initiated.")
        icon.stop()
        os._exit(0)

    menu = Menu(
        MenuItem("View Log", show_logs),
        MenuItem("Change IP Configuration", open_change_ip_page),
        MenuItem("Quit", on_quit)
    )
    tray_icon = Icon("wifi_ip_switcher", icon_img, menu=menu)
    tray_icon.run()

# === Main ===
def main():
    logging.info("=== Wi-Fi Auto IP Switcher Started ===")

    if not is_admin():
        relaunch_as_admin()

    tray_thread = threading.Thread(target=start_tray_icon)
    tray_thread.daemon = False
    tray_thread.start()

    monitor_thread = threading.Thread(target=monitor_ssid_loop)
    monitor_thread.daemon = False
    monitor_thread.start()

    flask_thread = threading.Thread(target=start_flask_app)
    flask_thread.daemon = False
    flask_thread.start()

    if not os.path.exists(config_file):
        logging.info("[INFO] Config file not found, opening browser for initial setup.")
        open_browser()
    else:
        logging.info("[INFO] Config file found. Application running in background.")

    try:
        tray_thread.join()
        monitor_thread.join()
        flask_thread.join()
    except KeyboardInterrupt:
        logging.info("[INFO] KeyboardInterrupt detected, attempting graceful shutdown.")
        pass

if __name__ == "__main__":
    main()