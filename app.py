import ctypes
import os
import subprocess
import sys
import time
import json
import logging
from tkinter import Tk, Entry, Label, Button, messagebox, Frame
from pystray import Icon, MenuItem, Menu
from PIL import Image, ImageDraw
import threading

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
    """ 
    Try to load the config. If it doesn't exist or is corrupted, return None 
    to prompt the user for input.
    """
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

# === GUI for User Input (Tkinter) ===
class WifiConfigApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Wi-Fi Configuration")
        self.root.geometry("450x500")  # Increased size for more flexibility
        self.root.resizable(False, False)  # Disable window resizing

        frame = Frame(root)
        frame.pack(padx=20, pady=10, expand=True)

        self.ssid_label = Label(frame, text="Enter Wi-Fi SSID:")
        self.ssid_label.grid(row=0, column=0, sticky="w", pady=5)
        self.ssid_entry = Entry(frame, width=35)
        self.ssid_entry.grid(row=0, column=1, pady=5)

        self.ip_label = Label(frame, text="Enter Static IP Address:")
        self.ip_label.grid(row=1, column=0, sticky="w", pady=5)
        self.ip_entry = Entry(frame, width=35)
        self.ip_entry.grid(row=1, column=1, pady=5)

        self.subnet_label = Label(frame, text="Enter Subnet Mask:")
        self.subnet_label.grid(row=2, column=0, sticky="w", pady=5)
        self.subnet_entry = Entry(frame, width=35)
        self.subnet_entry.grid(row=2, column=1, pady=5)

        self.gateway_label = Label(frame, text="Enter Default Gateway:")
        self.gateway_label.grid(row=3, column=0, sticky="w", pady=5)
        self.gateway_entry = Entry(frame, width=35)
        self.gateway_entry.grid(row=3, column=1, pady=5)

        self.preferred_dns_label = Label(frame, text="Enter Preferred DNS:")
        self.preferred_dns_label.grid(row=4, column=0, sticky="w", pady=5)
        self.preferred_dns_entry = Entry(frame, width=35)
        self.preferred_dns_entry.grid(row=4, column=1, pady=5)

        self.alternate_dns_label = Label(frame, text="Enter Alternate DNS:")
        self.alternate_dns_label.grid(row=5, column=0, sticky="w", pady=5)
        self.alternate_dns_entry = Entry(frame, width=35)
        self.alternate_dns_entry.grid(row=5, column=1, pady=5)

        # Add Submit and Clear buttons below
        self.submit_button = Button(frame, text="Submit", command=self.save_configuration)
        self.submit_button.grid(row=6, column=0, columnspan=2, pady=10)

        self.clear_button = Button(frame, text="Clear", command=self.clear_input)
        self.clear_button.grid(row=7, column=0, columnspan=2, pady=5)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def save_configuration(self):
        """ Save the entered configuration to the JSON file. """
        ssid = self.ssid_entry.get().strip()
        ip = self.ip_entry.get().strip()
        subnet = self.subnet_entry.get().strip()
        gateway = self.gateway_entry.get().strip()
        preferred_dns = self.preferred_dns_entry.get().strip()
        alternate_dns = self.alternate_dns_entry.get().strip()

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
            messagebox.showinfo("Success", "Configuration saved successfully!")
            self.root.quit()  # Close the Tkinter window
        else:
            messagebox.showerror("Error", "Please fill in all fields.")

    def clear_input(self):
        """ Clear all input fields. """
        self.ssid_entry.delete(0, "end")
        self.ip_entry.delete(0, "end")
        self.subnet_entry.delete(0, "end")
        self.gateway_entry.delete(0, "end")
        self.preferred_dns_entry.delete(0, "end")
        self.alternate_dns_entry.delete(0, "end")

    def on_close(self):
        """ Close the Tkinter window properly. """
        logging.info("[INFO] Configuration window closed.")
        self.root.quit()  # Close the app

# === System Tray Icon ===
def create_tray_icon():
    # Create an icon for the system tray
    icon = Image.new("RGB", (64, 64), (255, 255, 255))
    draw = ImageDraw.Draw(icon)
    draw.rectangle((0, 0, 64, 64), fill="blue")

    def show_logs(icon, item):
        with open(log_file, "r") as log:
            logs = log.read()
        messagebox.showinfo("Log File", logs)

    def change_ip(icon, item):
        # Open the Tkinter input window for configuration
        root = Tk()
        app = WifiConfigApp(root)
        root.mainloop()

    def on_quit(icon, item):
        logging.info("[INFO] Application Quit.")
        icon.stop()

    # Create tray menu options
    menu = Menu(MenuItem("Change IP", change_ip), MenuItem("View Log", show_logs), MenuItem("Quit", on_quit))

    tray_icon = Icon("wifi_ip_switcher", icon, menu=menu)
    tray_icon.run()

def main():
    logging.info("=== Wi-Fi Auto IP Switcher Running ===")

    if not is_admin():
        relaunch_as_admin()

    # Start the tray icon in a separate thread to avoid blocking the main loop
    tray_thread = threading.Thread(target=create_tray_icon)
    tray_thread.daemon = True
    tray_thread.start()

    # Load or Create Config
    ssid_ip_map = load_or_create_config()

    if ssid_ip_map is None:
        # Open the Tkinter input window to ask the user for the config
        root = Tk()
        app = WifiConfigApp(root)
        root.mainloop()

    while True:
        ssid = get_connected_ssid()
        if ssid:
            logging.info(f"[INFO] Connected to SSID: {ssid}")
            if ssid_ip_map and ssid in ssid_ip_map:
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
        elif not ssid:
            logging.info("[INFO] Not connected to Wi-Fi.")

        time.sleep(10)

if __name__ == "__main__":
    main()
