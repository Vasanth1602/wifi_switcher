import ctypes
import os
import subprocess
import sys
import time
import json

# === Constants ===
interface_name = "Wi-Fi"
config_file = "wifi_ip_config.json"

# === Admin Check and Elevation ===
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def relaunch_as_admin():
    print("[↑] Requesting Admin privileges...")
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

        print(f"[✓] Static IP and DNS set: {ip}, DNS: {preferred_dns}, {alternate_dns}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to set static IP/DNS: {e}")

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

        print("[✓] Switched to DHCP.")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to set DHCP: {e}")

# === Load or Create Config ===
def load_or_create_config():
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("[ERROR] Config file is corrupted.")
            os.remove(config_file)

    print("=== First Time Setup ===")
    config = {}
    ssid = input("Enter Wi-Fi SSID: ").strip()
    ip = input("Enter static IP address: ").strip()
    subnet = input("Enter subnet mask (e.g., 255.255.255.0): ").strip()
    gateway = input("Enter default gateway: ").strip()
    preferred_dns = input("Enter preferred DNS server (e.g., 8.8.8.8): ").strip()
    alternate_dns = input("Enter alternate DNS server (e.g., 4.2.2.2): ").strip()

    config[ssid] = {
        "ip": ip,
        "subnet": subnet,
        "gateway": gateway,
        "preferred_dns": preferred_dns,
        "alternate_dns": alternate_dns
    }

    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)

    return config

# === MAIN ===
def main():
    print("=== Wi-Fi Auto IP Switcher Running ===")

    if not is_admin():
        relaunch_as_admin()

    ssid_ip_map = load_or_create_config()
    last_ssid = None

    while True:
        ssid = get_connected_ssid()
        if ssid and ssid != last_ssid:
            print(f"[INFO] Connected to SSID: {ssid}")
            last_ssid = ssid

            if ssid in ssid_ip_map:
                config = ssid_ip_map[ssid]
                current_ip = get_current_ip(interface_name)

                if current_ip != config["ip"]:
                    print("[+] Applying static IP configuration...")
                    set_static_ip(
                        interface_name,
                        config["ip"],
                        config["subnet"],
                        config["gateway"],
                        config["preferred_dns"],
                        config["alternate_dns"]
                    )
                else:
                    print("[✓] IP already correctly set.")
            else:
                print("[INFO] Unknown SSID. Switching to DHCP...")
                set_dhcp_ip(interface_name)
        elif not ssid:
            print("[INFO] Not connected to Wi-Fi.")

        time.sleep(10)

if __name__ == "__main__":
    main()
# To do:
# 1.Add to startup with Windows
# 2.create input box (cmd) and add save user input as json file
# 3.If user want to change ip add option for it (system tray)
# 4.Add logging to file