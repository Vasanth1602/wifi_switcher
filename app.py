import ctypes
import os
import subprocess
import sys
import time
import winreg
import socket

# === Configuration ===
ssid_ip_map = {
    "periyar_univ": {
        "ip": "172.16.36.41",
        "subnet": "255.255.0.0",
        "gateway": "172.16.1.1"
    }
}

interface_name = "Wi-Fi"
shortcut_name = "WiFiAutoIPSwitcher.lnk"

# === Check Location Services ===
def is_location_enabled():
    try:
        key_path = r"SYSTEM\CurrentControlSet\Services\lfsvc\Service\Configuration"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            status, _ = winreg.QueryValueEx(key, "Status")
            return status == 1
    except Exception:
        return False

def prompt_enable_location():
    print("[!] Location Services are OFF. Opening settings...")
    os.system("start ms-settings:privacy-location")
    input("[PAUSE] Enable location, then press Enter to continue...")

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

# === Create Desktop Shortcut Once ===
def create_admin_shortcut():
    import winshell
    from win32com.client import Dispatch

    desktop = winshell.desktop()
    shortcut_path = os.path.join(desktop, shortcut_name)
    target = sys.executable
    script = os.path.abspath(__file__)

    if not os.path.exists(shortcut_path):
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortcut(shortcut_path)
        shortcut.TargetPath = target
        shortcut.Arguments = f'"{script}"'
        shortcut.WorkingDirectory = os.path.dirname(script)
        shortcut.IconLocation = target
        shortcut.WindowStyle = 7
        shortcut.Description = "Wi-Fi Auto IP Switcher (Admin)"
        shortcut.Save()
        print(f"[✓] Shortcut created: {shortcut_path}")

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

# === Set Static IP ===
def set_static_ip(interface, ip, subnet, gateway):
    try:
        subprocess.run([
            "netsh", "interface", "ip", "set", "address",
            interface, "static", ip, subnet, gateway
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        print(f"[✓] Static IP set: {ip}")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to set static IP: {e}")

# === MAIN ===
def main():
    print("=== Wi-Fi Auto IP Switcher Running in Background ===")

    # Relaunch if not Admin
    if not is_admin():
        relaunch_as_admin()

    create_admin_shortcut()

    # Location check only if necessary
    if not is_location_enabled():
        prompt_enable_location()

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
                    print("[+] Setting static IP...")
                    set_static_ip(interface_name, config["ip"], config["subnet"], config["gateway"])
                else:
                    print("[✓] IP already correct.")
            else:
                print("[!] SSID not found in config. Skipping.")
        elif not ssid:
            print("[INFO] Not connected to Wi-Fi.")

        time.sleep(10)

if __name__ == "__main__":
    main()
