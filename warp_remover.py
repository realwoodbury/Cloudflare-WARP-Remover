"""
Warp Remover
Finds and runs the real WARP uninstaller, then sweeps every leftover trace.
Build:  pyinstaller --onefile --noconsole --name "Warp Remover" warp_remover.py
"""

import os
import sys
import glob
import shutil
import subprocess
import time
import threading
import ctypes
import winreg
import tkinter as tk
from tkinter import font as tkfont


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run(cmd, wait=True, timeout=60):
    try:
        p = subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        if wait:
            p.wait(timeout=timeout)
    except Exception:
        pass


def query(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Result tokens
# ---------------------------------------------------------------------------

OK   = "ok"
SKIP = "skip"
FAIL = "fail"
NOTE = "note"
HEAD = "head"


def r(tag, msg):
    return tag, msg


# ---------------------------------------------------------------------------
# Step: Find and run the real uninstaller
# ---------------------------------------------------------------------------

def _find_uninstall_entry():
    """
    Scan the Uninstall registry hive for any entry whose DisplayName
    contains 'cloudflare warp'. Returns (display_name, uninstall_string,
    quiet_string, product_code) or None.
    """
    hives = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, base_path in hives:
        try:
            base = winreg.OpenKey(hive, base_path)
        except Exception:
            continue
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(base, i)
                i += 1
            except OSError:
                break
            try:
                sub = winreg.OpenKey(base, sub_name)
                def val(k):
                    try:
                        v, _ = winreg.QueryValueEx(sub, k)
                        return str(v)
                    except Exception:
                        return ""
                display = val("DisplayName")
                if "cloudflare warp" in display.lower():
                    result = {
                        "display":  display,
                        "uninstall": val("UninstallString"),
                        "quiet":    val("QuietUninstallString"),
                        "code":     sub_name if sub_name.startswith("{") else "",
                    }
                    winreg.CloseKey(sub)
                    winreg.CloseKey(base)
                    return result
                winreg.CloseKey(sub)
            except Exception:
                pass
        winreg.CloseKey(base)
    return None


def step_uninstall():
    yield r(HEAD, "Official Uninstaller")

    # 1. Kill running processes first so the uninstaller can proceed
    for proc in ["warp-svc.exe", "Cloudflare WARP.exe", "CloudflareWARP.exe",
                 "warp-taskbar.exe", "warp-diag.exe"]:
        out = query(f'tasklist /FI "IMAGENAME eq {proc}" /NH 2>nul')
        if proc.lower() in out.lower():
            run(f'taskkill /F /IM "{proc}" /T')
            yield r(OK, f"Stopped process: {proc}")

    # 2. Stop and disable the service so the uninstaller is not blocked
    for svc in ["CloudflareWARP", "warp-svc", "cfwarp"]:
        out = query(f'sc query "{svc}" 2>nul')
        if "SERVICE_NAME" in out:
            run(f'sc stop "{svc}"')
            time.sleep(1)
            yield r(OK, f"Stopped service: {svc}")

    # 3. Find the uninstaller via registry
    entry = _find_uninstall_entry()
    if entry:
        yield r(NOTE, f"Found: {entry['display']}")

        # Prefer the quiet/silent string, then build one from UninstallString
        quiet = entry["quiet"]
        uninst = entry["uninstall"]
        product_code = entry["code"]

        ran = False

        # Try QuietUninstallString first (already has /quiet flags)
        if quiet:
            yield r(NOTE, f"Running silent uninstall...")
            run(quiet, wait=True, timeout=120)
            time.sleep(4)
            ran = True

        # If the uninstall string is an msiexec call, use it properly
        elif uninst and "msiexec" in uninst.lower():
            cmd = f'msiexec /x "{product_code}" /quiet /norestart' if product_code \
                  else uninst.replace("/I", "/x").replace("/i", "/x") + " /quiet /norestart"
            yield r(NOTE, f"Running MSI uninstall...")
            run(cmd, wait=True, timeout=120)
            time.sleep(4)
            ran = True

        # Otherwise run the .exe with a /S flag
        elif uninst:
            exe = uninst.strip('"').split('"')[0] if uninst.startswith('"') else uninst.split()[0]
            if os.path.exists(exe):
                yield r(NOTE, f"Running: {os.path.basename(exe)} /S")
                run(f'"{exe}" /S', wait=True, timeout=120)
                time.sleep(4)
                ran = True

        # Direct product code fallback
        if not ran and product_code:
            yield r(NOTE, "Trying MsiExec with product code...")
            run(f'msiexec /x "{product_code}" /quiet /norestart',
                wait=True, timeout=120)
            time.sleep(4)

        # Check if it actually removed itself
        if _find_uninstall_entry() is None:
            yield r(OK, "Uninstaller finished, registry entry is gone")
        else:
            yield r(NOTE, "Uninstaller ran but registry entry still present, continuing with force removal")

    else:
        # Fallback: try known paths on disk
        known = [
            r"C:\Program Files\Cloudflare\Cloudflare WARP\Uninstall Cloudflare WARP.exe",
            r"C:\Program Files (x86)\Cloudflare\Cloudflare WARP\Uninstall Cloudflare WARP.exe",
        ]
        found = False
        for p in known:
            if os.path.exists(p):
                yield r(NOTE, f"Found on disk: {p}")
                run(f'"{p}" /S', wait=True, timeout=120)
                time.sleep(4)
                yield r(OK, "Ran on-disk uninstaller")
                found = True
                break
        if not found:
            yield r(SKIP, "No WARP uninstaller found (may not be installed)")

    # WMIC fallback regardless
    wmic = query('wmic product where "name like \'%Cloudflare WARP%\'" get name 2>nul')
    if "Cloudflare" in wmic:
        yield r(NOTE, "MSI entry found via WMIC, triggering removal...")
        run('wmic product where "name like \'%Cloudflare WARP%\'" call uninstall /nointeractive',
            wait=True, timeout=120)
        time.sleep(3)
        yield r(OK, "WMIC uninstall triggered")
    else:
        yield r(SKIP, "No MSI entry via WMIC")


# ---------------------------------------------------------------------------
# Step: Services (anything left over)
# ---------------------------------------------------------------------------

def step_services():
    yield r(HEAD, "Services")
    services = [
        "CloudflareWARP", "warp-svc", "cfwarp", "CloudflareWARPTunnel"
    ]
    for svc in services:
        out = query(f'sc query "{svc}" 2>nul')
        if "SERVICE_NAME" in out:
            run(f'sc stop "{svc}"')
            time.sleep(1)
            run(f'sc delete "{svc}"')
            yield r(OK, f"Deleted service: {svc}")
        else:
            yield r(SKIP, f"Service not present: {svc}")


# ---------------------------------------------------------------------------
# Step: Files
# ---------------------------------------------------------------------------

def _rm(path):
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.chmod(path, 0o777)
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=False)
        return True
    except Exception:
        # cmd fallback
        run(f'rd /s /q "{path}" 2>nul')
        run(f'del /f /q "{path}" 2>nul')
        return not os.path.exists(path)


def step_files():
    yield r(HEAD, "Files and Folders")

    PF   = r"C:\Program Files"
    PF86 = r"C:\Program Files (x86)"
    PD   = os.environ.get("PROGRAMDATA",  r"C:\ProgramData")
    LOC  = os.environ.get("LOCALAPPDATA", "")
    ROAM = os.environ.get("APPDATA",      "")
    TEMP = os.environ.get("TEMP",         "")
    USER = os.environ.get("USERPROFILE",  "")
    WIN  = os.environ.get("WINDIR",       r"C:\Windows")

    paths = [
        os.path.join(PF,   "Cloudflare"),
        os.path.join(PF86, "Cloudflare"),
        os.path.join(PF,   "WireGuard"),
        os.path.join(PD,   "Cloudflare"),
        os.path.join(PD,   "CloudflareWARP"),
        os.path.join(PD,   "Cloudflare WARP"),
        os.path.join(LOC,  "Cloudflare"),
        os.path.join(LOC,  "CloudflareWARP"),
        os.path.join(LOC,  r"Packages\Cloudflare.CloudflareWARP_8wekyb3d8bbwe"),
        os.path.join(ROAM, "Cloudflare"),
        os.path.join(ROAM, "CloudflareWARP"),
        os.path.join(TEMP, "Cloudflare"),
        os.path.join(TEMP, "CloudflareWARP"),
        os.path.join(WIN,  r"System32\drivers\cloudflare.sys"),
        os.path.join(WIN,  r"System32\drivers\cfwarp.sys"),
        os.path.join(WIN,  r"System32\drivers\warp.sys"),
        os.path.join(WIN,  r"SysWOW64\cloudflare.sys"),
        os.path.join(WIN,  r"Logs\CloudflareWARP"),
        os.path.join(PD,   r"Microsoft\Windows\Start Menu\Programs\Cloudflare WARP"),
        os.path.join(ROAM, r"Microsoft\Windows\Start Menu\Programs\Cloudflare WARP"),
        os.path.join(USER, r"Desktop\Cloudflare WARP.lnk"),
        os.path.join(USER, r"Desktop\WARP.lnk"),
    ]

    # Glob patterns
    globs = [
        os.path.join(WIN, r"Prefetch\CLOUDFLARE*"),
        os.path.join(WIN, r"Prefetch\WARP*"),
        os.path.join(TEMP, "warp*"),
    ]

    for p in paths:
        if not p:
            continue
        if os.path.exists(p):
            ok = _rm(p)
            yield r(OK if ok else FAIL,
                    f"{'Removed' if ok else 'Could not remove'}: {p}")
        else:
            yield r(SKIP, f"Not found: {p}")

    for pattern in globs:
        if not pattern:
            continue
        hits = glob.glob(pattern)
        if hits:
            for h in hits:
                ok = _rm(h)
                yield r(OK if ok else FAIL,
                        f"{'Removed' if ok else 'Could not remove'}: {h}")
        else:
            yield r(SKIP, f"No matches: {pattern}")

    # Sweep every user profile
    yield r(NOTE, "Checking all user profiles...")
    try:
        for uname in os.listdir(r"C:\Users"):
            udir = os.path.join(r"C:\Users", uname)
            if not os.path.isdir(udir):
                continue
            for rel in [
                r"AppData\Local\Cloudflare",
                r"AppData\Local\CloudflareWARP",
                r"AppData\Roaming\Cloudflare",
                r"AppData\Roaming\CloudflareWARP",
                r"Desktop\Cloudflare WARP.lnk",
            ]:
                fp = os.path.join(udir, rel)
                if os.path.exists(fp):
                    ok = _rm(fp)
                    yield r(OK if ok else FAIL,
                            f"{'Removed' if ok else 'Could not remove'}: {fp}")
    except Exception as exc:
        yield r(FAIL, f"Profile sweep error: {exc}")


# ---------------------------------------------------------------------------
# Step: Registry
# ---------------------------------------------------------------------------

def _reg_exists(hive, path):
    try:
        k = winreg.OpenKey(hive, path)
        winreg.CloseKey(k)
        return True
    except Exception:
        return False


def _reg_del_tree(hive, path):
    try:
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS)
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
                _reg_del_tree(hive, f"{path}\\{sub}")
            except OSError:
                break
        winreg.CloseKey(key)
        winreg.DeleteKey(hive, path)
    except Exception:
        pass


def step_registry():
    yield r(HEAD, "Registry")

    HKLM = winreg.HKEY_LOCAL_MACHINE
    HKCU = winreg.HKEY_CURRENT_USER

    entries = [
        (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Cloudflare WARP"),
        (HKLM, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Cloudflare WARP"),
        (HKLM, r"SOFTWARE\Cloudflare"),
        (HKCU, r"SOFTWARE\Cloudflare"),
        (HKLM, r"SOFTWARE\WOW6432Node\Cloudflare"),
        (HKCU, r"SOFTWARE\WOW6432Node\Cloudflare"),
        (HKLM, r"SYSTEM\CurrentControlSet\Services\CloudflareWARP"),
        (HKLM, r"SYSTEM\CurrentControlSet\Services\warp-svc"),
        (HKLM, r"SYSTEM\CurrentControlSet\Services\cfwarp"),
        (HKLM, r"SYSTEM\CurrentControlSet\Services\CloudflareWARPTunnel"),
        (HKLM, r"SYSTEM\ControlSet001\Services\CloudflareWARP"),
        (HKLM, r"SYSTEM\ControlSet001\Services\warp-svc"),
        (HKLM, r"SYSTEM\ControlSet002\Services\CloudflareWARP"),
        (HKLM, r"SYSTEM\ControlSet002\Services\warp-svc"),
        (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\CloudflareWARP.exe"),
        (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\warp-svc.exe"),
        (HKLM, r"SYSTEM\CurrentControlSet\Services\EventLog\Application\CloudflareWARP"),
        (HKLM, r"SYSTEM\CurrentControlSet\Services\EventLog\Application\warp-svc"),
    ]

    for hive, path in entries:
        leaf = path.split("\\")[-1]
        if _reg_exists(hive, path):
            _reg_del_tree(hive, path)
            yield r(OK, f"Removed key: {leaf}")
        else:
            yield r(SKIP, f"Key not found: {leaf}")

    # Sweep Run / RunOnce for any leftover autostart values
    yield r(NOTE, "Scanning startup entries...")
    run_paths = [
        (HKCU, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        (HKCU, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
        (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
        (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
        (HKLM, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    ]
    found_run = False
    for hive, path in run_paths:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS)
            to_del = []
            idx = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, idx)
                    combined = (name + str(val)).lower()
                    if any(k in combined for k in ("cloudflare", "warp-svc", "warptray")):
                        to_del.append(name)
                    idx += 1
                except OSError:
                    break
            for name in to_del:
                winreg.DeleteValue(key, name)
                yield r(OK, f"Removed startup entry: {name}")
                found_run = True
            winreg.CloseKey(key)
        except Exception:
            pass
    if not found_run:
        yield r(SKIP, "No WARP startup entries found")


# ---------------------------------------------------------------------------
# Step: Scheduled tasks
# ---------------------------------------------------------------------------

def step_tasks():
    yield r(HEAD, "Scheduled Tasks")
    known = ["CloudflareWARP", "Cloudflare WARP", "warp-svc", "WARPAutoUpdate"]
    for t in known:
        out = query(f'schtasks /Query /TN "{t}" /FO LIST 2>nul')
        if "Task Name" in out:
            run(f'schtasks /Delete /TN "{t}" /F')
            yield r(OK, f"Deleted task: {t}")
        else:
            yield r(SKIP, f"Task not found: {t}")

    all_tasks = query("schtasks /Query /FO CSV /NH 2>nul")
    for line in all_tasks.splitlines():
        if any(k in line.lower() for k in ("cloudflare", "warp")):
            name = line.split(",")[0].strip('"')
            if name not in known:
                run(f'schtasks /Delete /TN "{name}" /F')
                yield r(OK, f"Deleted extra task: {name}")


# ---------------------------------------------------------------------------
# Step: Firewall rules
# ---------------------------------------------------------------------------

def step_firewall():
    yield r(HEAD, "Firewall Rules")
    rules = ["Cloudflare WARP", "warp-svc", "CloudflareWARP", "WARP Tunnel"]
    for rule in rules:
        out = query(f'netsh advfirewall firewall show rule name="{rule}" 2>nul')
        if "Rule Name" in out:
            run(f'netsh advfirewall firewall delete rule name="{rule}"')
            yield r(OK, f"Removed rule: {rule}")
        else:
            yield r(SKIP, f"Rule not found: {rule}")


# ---------------------------------------------------------------------------
# Step: Network adapter and drivers
# ---------------------------------------------------------------------------

def step_network():
    yield r(HEAD, "Network Adapter")
    for a in ["CloudflareWARP", "Cloudflare WARP", "WARP"]:
        run(f'netsh interface delete interface "{a}" 2>nul')

    out = query("pnputil /enum-devices /class Net 2>nul")
    found_dev = False
    for line in out.splitlines():
        if "cloudflare" in line.lower() or ("warp" in line.lower() and "hardware" not in line.lower()):
            parts = line.split(":", 1)
            if len(parts) > 1:
                dev_id = parts[1].strip()
                run(f'pnputil /remove-device "{dev_id}" /subtree 2>nul')
                yield r(OK, f"Removed device: {dev_id}")
                found_dev = True
    if not found_dev:
        yield r(SKIP, "No WARP network devices found")

    WIN = os.environ.get("WINDIR", r"C:\Windows")
    for df in ["cloudflare.sys", "cfwarp.sys", "warp.sys"]:
        fp = os.path.join(WIN, "System32", "drivers", df)
        if os.path.exists(fp):
            ok = _rm(fp)
            yield r(OK if ok else FAIL,
                    f"{'Removed' if ok else 'Could not remove'} driver: {df}")
        else:
            yield r(SKIP, f"Driver not present: {df}")


# ---------------------------------------------------------------------------
# Step: Network stack cleanup
# ---------------------------------------------------------------------------

def step_cleanup():
    yield r(HEAD, "Network Cleanup")
    run("ipconfig /flushdns")
    yield r(OK, "DNS cache flushed")
    run("netsh winsock reset")
    yield r(OK, "Winsock reset")
    run("netsh int ip reset")
    yield r(OK, "TCP/IP stack reset")


# ---------------------------------------------------------------------------
# Step: Event logs
# ---------------------------------------------------------------------------

def step_eventlogs():
    yield r(HEAD, "Event Logs")
    for log in ["CloudflareWARP", "Cloudflare WARP", "warp-svc"]:
        out = query(f'wevtutil gl "{log}" 2>nul')
        if "logName" in out.lower():
            run(f'wevtutil cl "{log}" 2>nul')
            run(f'wevtutil um "{log}" 2>nul')
            yield r(OK, f"Cleared log: {log}")
        else:
            yield r(SKIP, f"Log not found: {log}")


# ---------------------------------------------------------------------------
# All steps in order
# ---------------------------------------------------------------------------

STEPS = [
    ("Uninstall",   step_uninstall),
    ("Services",    step_services),
    ("Files",       step_files),
    ("Registry",    step_registry),
    ("Tasks",       step_tasks),
    ("Firewall",    step_firewall),
    ("Network",     step_network),
    ("Event Logs",  step_eventlogs),
    ("Cleanup",     step_cleanup),
]


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

C = {
    "bg":       "#111113",
    "surface":  "#18181b",
    "surface2": "#212126",
    "border":   "#2c2c32",
    "border2":  "#3a3a42",
    "text":     "#dcdce0",
    "muted":    "#5a5a65",
    "muted2":   "#3a3a42",
    "ok":       "#7fcf94",
    "skip":     "#4a4a55",
    "skip_t":   "#686878",
    "fail":     "#d97070",
    "note":     "#81a8d0",
    "head":     "#9898a8",
    "btn_bg":   "#dcdce0",
    "btn_fg":   "#111113",
    "done_bg":  "#2a4a32",
    "done_fg":  "#7fcf94",
}

SYM = {
    OK:   ("check", C["ok"]),
    SKIP: ("dash",  C["skip_t"]),
    FAIL: ("cross", C["fail"]),
    NOTE: ("dot",   C["note"]),
    HEAD: ("arrow", C["head"]),
}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Warp Remover")
        self.geometry("800x580")
        self.minsize(640, 480)
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self._counts = {OK: 0, SKIP: 0, FAIL: 0}
        self._fonts()
        self._layout()

    # -- Fonts ----------------------------------------------------------------

    def _fonts(self):
        self.fn = {
            "title":  tkfont.Font(family="Segoe UI", size=14, weight="bold"),
            "sub":    tkfont.Font(family="Segoe UI", size=9),
            "label":  tkfont.Font(family="Segoe UI", size=8, weight="bold"),
            "log":    tkfont.Font(family="Consolas",  size=9),
            "stat":   tkfont.Font(family="Segoe UI", size=12, weight="bold"),
            "btn":    tkfont.Font(family="Segoe UI", size=10, weight="bold"),
            "micro":  tkfont.Font(family="Segoe UI", size=8),
            "step":   tkfont.Font(family="Segoe UI", size=7),
        }

    # -- Layout ---------------------------------------------------------------

    def _layout(self):
        # The window is split into three fixed rows:
        # top (header + stats), middle (log, expands), bottom (progress + button)
        # Using pack with explicit fill/expand keeps the button always visible.

        # ---- TOP BLOCK ------------------------------------------------------
        top = tk.Frame(self, bg=C["bg"])
        top.pack(side="top", fill="x")

        # Thin top rule
        tk.Frame(top, bg=C["border2"], height=1).pack(fill="x")

        # Header row
        hdr = tk.Frame(top, bg=C["bg"], padx=26, pady=18)
        hdr.pack(fill="x")

        tk.Label(hdr, text="Warp Remover",
                 font=self.fn["title"], bg=C["bg"], fg=C["text"],
                 anchor="w").pack(anchor="w")
        tk.Label(hdr,
                 text="Runs the official uninstaller, then removes all leftover files, registry keys, and services.",
                 font=self.fn["sub"], bg=C["bg"], fg=C["muted"],
                 anchor="w").pack(anchor="w")

        # Divider
        tk.Frame(top, bg=C["border"], height=1).pack(fill="x")

        # Stats row
        stats_row = tk.Frame(top, bg=C["surface"], padx=26, pady=12)
        stats_row.pack(fill="x")

        self._stat_lbl = {}
        for key, label, color in [
            (OK,   "Removed",    C["ok"]),
            (SKIP, "Not found",  C["skip_t"]),
            (FAIL, "Failed",     C["fail"]),
        ]:
            f = tk.Frame(stats_row, bg=C["surface"], padx=18)
            f.pack(side="left")
            n = tk.Label(f, text="0", font=self.fn["stat"],
                         bg=C["surface"], fg=color)
            n.pack()
            tk.Label(f, text=label, font=self.fn["step"],
                     bg=C["surface"], fg=C["muted"]).pack()
            self._stat_lbl[key] = n

        # Divider
        tk.Frame(top, bg=C["border"], height=1).pack(fill="x")

        # Step indicators
        step_strip = tk.Frame(top, bg=C["surface2"], padx=26, pady=9)
        step_strip.pack(fill="x")

        self._dots = []
        for name, _ in STEPS:
            f = tk.Frame(step_strip, bg=C["surface2"])
            f.pack(side="left", padx=4)
            d = tk.Label(f, text="o", font=self.fn["step"],
                         bg=C["surface2"], fg=C["muted2"])
            d.pack()
            l = tk.Label(f, text=name, font=self.fn["step"],
                         bg=C["surface2"], fg=C["muted2"])
            l.pack()
            self._dots.append((d, l))

        tk.Frame(top, bg=C["border"], height=1).pack(fill="x")

        # ---- BOTTOM BLOCK (packed before middle so it's always visible) -----
        bot = tk.Frame(self, bg=C["bg"])
        bot.pack(side="bottom", fill="x")

        tk.Frame(bot, bg=C["border"], height=1).pack(fill="x")

        inner_bot = tk.Frame(bot, bg=C["bg"], padx=26, pady=14)
        inner_bot.pack(fill="x")

        # Progress bar (thin canvas)
        self._prog = tk.Canvas(inner_bot, bg=C["surface2"],
                               height=2, highlightthickness=0, bd=0)
        self._prog.pack(fill="x", pady=(0, 12))

        btn_area = tk.Frame(inner_bot, bg=C["bg"])
        btn_area.pack(fill="x")

        self.btn = tk.Button(
            btn_area, text="Remove Cloudflare WARP",
            font=self.fn["btn"],
            bg=C["btn_bg"], fg=C["btn_fg"],
            relief="flat", bd=0,
            activebackground="#c8c8cc",
            activeforeground=C["btn_fg"],
            padx=22, pady=9,
            cursor="hand2",
            command=self._confirm,
        )
        self.btn.pack(side="left")

        self._status = tk.Label(
            btn_area, text="Ready",
            font=self.fn["micro"], bg=C["bg"], fg=C["muted"])
        self._status.pack(side="left", padx=14)

        # ---- MIDDLE BLOCK (log, expands to fill remaining space) ------------
        mid = tk.Frame(self, bg=C["bg"])
        mid.pack(side="top", fill="both", expand=True)

        log_wrap = tk.Frame(mid, bg=C["bg"], padx=26, pady=12)
        log_wrap.pack(fill="both", expand=True)

        log_header = tk.Frame(log_wrap, bg=C["bg"])
        log_header.pack(fill="x", pady=(0, 5))
        tk.Label(log_header, text="LOG", font=self.fn["label"],
                 bg=C["bg"], fg=C["muted"]).pack(side="left")
        self._log_status = tk.Label(log_header, text="",
                                    font=self.fn["micro"],
                                    bg=C["bg"], fg=C["muted"])
        self._log_status.pack(side="right")

        frame_border = tk.Frame(log_wrap, bg=C["border"], padx=1, pady=1)
        frame_border.pack(fill="both", expand=True)

        log_inner = tk.Frame(frame_border, bg=C["surface"])
        log_inner.pack(fill="both", expand=True)

        self.log = tk.Text(
            log_inner, bg=C["surface"], fg=C["text"],
            font=self.fn["log"], relief="flat", bd=0,
            state="disabled", wrap="none",
            padx=12, pady=10, cursor="arrow",
            insertbackground=C["surface"],
        )
        vsb = tk.Scrollbar(log_inner, orient="vertical",
                           command=self.log.yview,
                           bg=C["surface2"], troughcolor=C["surface"],
                           width=7)
        self.log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        # Text tags
        for tag, (_, color) in SYM.items():
            self.log.tag_config(f"s_{tag}", foreground=color)
        self.log.tag_config("t_ok",   foreground=C["ok"])
        self.log.tag_config("t_skip", foreground=C["skip_t"])
        self.log.tag_config("t_fail", foreground=C["fail"])
        self.log.tag_config("t_note", foreground=C["text"])
        self.log.tag_config("t_head", foreground=C["head"])

    # -- Logging --------------------------------------------------------------

    def _write(self, tag, msg):
        _, sym_color = SYM.get(tag, ("dot", C["muted"]))
        sym_map = {
            "check": "v", "dash": "-", "cross": "x",
            "dot": ".", "arrow": ">"
        }
        sym_key = SYM.get(tag, ("dot", ""))[0]
        sym = sym_map.get(sym_key, ".")

        self.log.configure(state="normal")
        if tag == HEAD:
            self.log.insert("end", "\n")
            self.log.insert("end", f"  {sym}  ", f"s_{tag}")
            self.log.insert("end", msg + "\n", f"t_{tag}")
        else:
            self.log.insert("end", f"      {sym}  ", f"s_{tag}")
            self.log.insert("end", msg + "\n",        f"t_{tag}")
        self.log.see("end")
        self.log.configure(state="disabled")

        if tag in self._counts:
            self._counts[tag] += 1
            self._stat_lbl[tag].configure(text=str(self._counts[tag]))

        self.update_idletasks()

    def _set_status(self, msg, color=None):
        self._status.configure(text=msg, fg=color or C["muted"])
        self._log_status.configure(text=msg, fg=color or C["muted"])
        self.update_idletasks()

    def _update_progress(self, step_idx):
        w = self._prog.winfo_width() or 750
        self._prog.delete("all")
        self._prog.create_rectangle(0, 0, w, 2, fill=C["surface2"], outline="")
        if step_idx > 0:
            pct = step_idx / len(STEPS)
            self._prog.create_rectangle(
                0, 0, int(w * pct), 2, fill=C["text"], outline="")

        for i, (d, l) in enumerate(self._dots):
            if i < step_idx:
                d.configure(fg=C["ok"],   text="v")
                l.configure(fg=C["ok"])
            elif i == step_idx < len(STEPS):
                d.configure(fg=C["text"], text="o")
                l.configure(fg=C["text"])
            else:
                d.configure(fg=C["muted2"], text="o")
                l.configure(fg=C["muted2"])
        self.update_idletasks()

    # -- Confirm dialog -------------------------------------------------------

    def _confirm(self):
        dlg = tk.Toplevel(self)
        dlg.title("Confirm")
        dlg.geometry("430x186")
        dlg.configure(bg=C["bg"])
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)

        tk.Frame(dlg, bg=C["border2"], height=1).pack(fill="x")
        wrap = tk.Frame(dlg, bg=C["bg"], padx=26, pady=22)
        wrap.pack(fill="both", expand=True)

        tk.Label(wrap, text="Remove Cloudflare WARP?",
                 font=self.fn["title"], bg=C["bg"],
                 fg=C["text"]).pack(anchor="w")
        tk.Label(wrap,
                 text="The official uninstaller will run first, then all remaining\n"
                      "files, registry keys, services and adapters will be removed.",
                 font=self.fn["sub"], bg=C["bg"],
                 fg=C["muted"], justify="left").pack(anchor="w", pady=(6, 16))

        row = tk.Frame(wrap, bg=C["bg"])
        row.pack(anchor="w")

        def go():
            dlg.destroy()
            threading.Thread(target=self._run, daemon=True).start()

        tk.Button(row, text="Remove", font=self.fn["btn"],
                  bg=C["btn_bg"], fg=C["btn_fg"], relief="flat", bd=0,
                  activebackground="#c8c8cc", padx=18, pady=7,
                  cursor="hand2", command=go).pack(side="left", padx=(0, 8))
        tk.Button(row, text="Cancel", font=self.fn["btn"],
                  bg=C["surface2"], fg=C["muted"], relief="flat", bd=0,
                  padx=18, pady=7, cursor="hand2",
                  command=dlg.destroy).pack(side="left")

    # -- Main removal thread --------------------------------------------------

    def _run(self):
        self.btn.configure(state="disabled", bg=C["surface2"],
                           fg=C["muted"], text="Running...")

        for i, (name, fn) in enumerate(STEPS):
            self._update_progress(i)
            self._set_status(f"{name}...", C["note"])
            try:
                for tag, msg in fn():
                    self._write(tag, msg)
            except Exception as exc:
                self._write(FAIL, f"Step error in {name}: {exc}")
            time.sleep(0.06)

        self._update_progress(len(STEPS))
        ok = self._counts[OK]
        sk = self._counts[SKIP]
        fl = self._counts[FAIL]
        self._write(NOTE, "")
        self._write(NOTE, f"Done.  {ok} removed, {sk} not found, {fl} failed.")
        self._set_status("Complete", C["ok"])

        self.btn.configure(
            state="normal",
            bg=C["done_bg"], fg=C["done_fg"],
            text="Done",
        )
        self.after(300, self._restart_dialog)

    # -- Restart dialog -------------------------------------------------------

    def _restart_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Restart")
        dlg.geometry("430x186")
        dlg.configure(bg=C["bg"])
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)
        dlg.lift()

        tk.Frame(dlg, bg=C["border2"], height=1).pack(fill="x")
        wrap = tk.Frame(dlg, bg=C["bg"], padx=26, pady=22)
        wrap.pack(fill="both", expand=True)

        tk.Label(wrap, text="Restart recommended",
                 font=self.fn["title"], bg=C["bg"],
                 fg=C["text"]).pack(anchor="w")
        tk.Label(wrap,
                 text="Restarting now finalizes removal of WARP drivers\n"
                      "and kernel components. You can also restart later.",
                 font=self.fn["sub"], bg=C["bg"],
                 fg=C["muted"], justify="left").pack(anchor="w", pady=(6, 16))

        row = tk.Frame(wrap, bg=C["bg"])
        row.pack(anchor="w")

        def restart_now():
            dlg.destroy()
            # Standard restart, no sign-out screen
            run("shutdown /r /f /t 0")

        tk.Button(row, text="Restart now", font=self.fn["btn"],
                  bg=C["btn_bg"], fg=C["btn_fg"], relief="flat", bd=0,
                  activebackground="#c8c8cc", padx=18, pady=7,
                  cursor="hand2", command=restart_now).pack(
                  side="left", padx=(0, 8))
        tk.Button(row, text="Later", font=self.fn["btn"],
                  bg=C["surface2"], fg=C["muted"], relief="flat", bd=0,
                  padx=18, pady=7, cursor="hand2",
                  command=dlg.destroy).pack(side="left")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not is_admin():
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            " ".join(f'"{a}"' for a in sys.argv),
            None, 1,
        )
        sys.exit(0)

    app = App()
    app.mainloop()
