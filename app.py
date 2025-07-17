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

preferred_dns = "8.8.8.8"
alternate_dns = "4.2.2.2"

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

# === Location Services ===
def is_location_enabled():
    key_path = r"SYSTEM\CurrentControlSet\Services\lfsvc\Service\Configuration"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
            status, _ = winreg.QueryValueEx(key, "Status")
            return status == 1
    except PermissionError:
        print("[ERROR] Cannot access registry. Admin required.")
        return False
    except FileNotFoundError:
        print("[ERROR] Registry key for Location Services not found.")
        return False
    except Exception as e:
        print(f"[ERROR] Unexpected error checking location: {e}")
        return False

def wait_for_location_enable():
    while True:
        if is_location_enabled():
            print("[✓] Location Services are ON.")
            return
        print("[!] Location Services are OFF. Opening settings...")
        os.system("start ms-settings:privacy-location")
        input("[PAUSE] Enable Location Services, then press Enter to retry...")

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

# === Set Static IP + DNS ===
def set_static_ip(interface, ip, subnet, gateway):
    try:
        # Set IP Address
        subprocess.run([
            "netsh", "interface", "ip", "set", "address",
            interface, "static", ip, subnet, gateway
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        # Set Preferred DNS
        subprocess.run([
            "netsh", "interface", "ip", "set", "dns",
            interface, "static", preferred_dns, "primary"
        ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        # Set Alternate DNS
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

# === MAIN ===
def main():
    print("=== Wi-Fi Auto IP Switcher Running in Background ===")

    if not is_admin():
        relaunch_as_admin()

    create_admin_shortcut()
    wait_for_location_enable()

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
                    print("[+] Setting static IP and DNS...")
                    set_static_ip(interface_name, config["ip"], config["subnet"], config["gateway"])
                else:
                    print("[✓] IP already correct for static.")
            else:
                print("[INFO] SSID not in static config. Switching to DHCP...")
                set_dhcp_ip(interface_name)
        elif not ssid:
            print("[INFO] Not connected to Wi-Fi.")

        time.sleep(10)

if __name__ == "__main__":
    main()
