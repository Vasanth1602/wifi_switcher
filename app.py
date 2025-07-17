import ctypes
import subprocess
import time
import sys

TARGET_SSID = "periyar_univ"

STATIC_IP = "192.168.1.100"
SUBNET_MASK = "255.255.255.0"
GATEWAY = "192.168.1.1"

INTERFACE_NAME = "Wi-Fi"

def is_admin():
    """Check if script has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def get_current_ssid():
    """Get the name of the currently connected Wi-Fi SSID."""
    try:
        result = subprocess.check_output(['netsh', 'wlan', 'show', 'interfaces'], text=True)
        for line in result.split('\n'):
            if "SSID" in line and "BSSID" not in line:
                return line.split(":")[1].strip()
    except Exception as e:
        print(f"[ERROR] Unable to get SSID: {e}")
    return None

def set_static_ip():
    """Set static IP configuration."""
    print("[+] Setting Static IP...")
    subprocess.call([
        "netsh", "interface", "ip", "set", "address",
        f"name={INTERFACE_NAME}", "static", STATIC_IP, SUBNET_MASK, GATEWAY
    ])
    print("[✓] Static IP set.")

def set_dhcp():
    """Set IP configuration to DHCP (automatic)."""
    print("[+] Reverting to Dynamic IP (DHCP)...")
    subprocess.call([
        "netsh", "interface", "ip", "set", "address",
        f"name={INTERFACE_NAME}", "source=dhcp"
    ])
    print("[✓] DHCP enabled.")

def main():
    print("=== Wi-Fi Auto IP Switcher ===")

    last_mode = None  # 'static' or 'dhcp'

    while True:
        ssid = get_current_ssid()
        if ssid:
            print(f"[INFO] Connected to SSID: {ssid}")
            if ssid == TARGET_SSID and last_mode != "static":
                set_static_ip()
                last_mode = "static"
            elif ssid != TARGET_SSID and last_mode != "dhcp":
                set_dhcp()
                last_mode = "dhcp"
        else:
            print("[INFO] Not connected to any Wi-Fi.")

        time.sleep(5)  # Check every 5 seconds

if __name__ == "__main__":
    if not is_admin():
        print("[!] Elevating privileges to run as administrator...")
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, ' '.join(sys.argv), None, 1
        )
        sys.exit()
    else:
        main()
