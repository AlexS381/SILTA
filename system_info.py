"""
SILTA — Side Y: get_system_info()
Gathers distro, kernel, hardware, and installed packages.
No external dependencies: only stdlib + subprocess.
"""

import subprocess
import platform
import shutil
import json
import os
from pathlib import Path


def _run(cmd: list[str], timeout: int = 5) -> str:
    """Executes a command and returns stripped stdout, '' if error."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _distro() -> dict:
    info = {}
    # /etc/os-release is the modern standard
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip().strip('"')
    return {
        "id":          info.get("ID", platform.system()),
        "name":        info.get("PRETTY_NAME", info.get("NAME", platform.system())),
        "version":     info.get("VERSION_ID", ""),
        "codename":    info.get("VERSION_CODENAME", ""),
    }


def _kernel() -> dict:
    return {
        "release":  platform.release(),
        "version":  platform.version(),
        "machine":  platform.machine(),
    }


def _cpu() -> dict:
    # Try lscpu first (richer), fallback to /proc/cpuinfo
    lscpu = _run(["lscpu"])
    if lscpu:
        data = {}
        for line in lscpu.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip()
        return {
            "model":       data.get("Model name", data.get("CPU(s)", "unknown")),
            "cores":       data.get("CPU(s)", ""),
            "threads":     data.get("Thread(s) per core", ""),
            "arch":        data.get("Architecture", platform.machine()),
            "freq_mhz":    data.get("CPU max MHz", data.get("CPU MHz", "")),
        }
    # Fallback
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                return {"model": line.split(":", 1)[-1].strip(), "arch": platform.machine()}
    return {"model": "unknown", "arch": platform.machine()}


def _memory() -> dict:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return {}
    data = {}
    for line in meminfo.read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    def kb_to_mb(val: str) -> int:
        try:
            return int(val.split()[0]) // 1024
        except Exception:
            return 0
    total = kb_to_mb(data.get("MemTotal", "0"))
    avail = kb_to_mb(data.get("MemAvailable", "0"))
    return {
        "total_mb":     total,
        "available_mb": avail,
        "used_mb":      total - avail,
    }


def _disk() -> list[dict]:
    out = _run(["df", "-h", "--output=source,fstype,size,used,avail,pcent,target"])
    lines = out.splitlines()
    if len(lines) < 2:
        return []
    disks = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 7:
            continue
        # Only show real filesystems (exclude tmpfs, devtmpfs, etc.)
        if parts[1] in ("tmpfs", "devtmpfs", "overlay", "squashfs"):
            continue
        disks.append({
            "source":  parts[0],
            "fstype":  parts[1],
            "size":    parts[2],
            "used":    parts[3],
            "avail":   parts[4],
            "use_pct": parts[5],
            "mount":   parts[6],
        })
    return disks


def _packages() -> dict:
    """Detects package managers and counts/lists installed packages."""
    managers = {}

    # dpkg (Debian/Ubuntu)
    if shutil.which("dpkg"):
        out = _run(["dpkg", "-l"])
        count = len([l for l in out.splitlines() if l.startswith("ii")])
        if count:
            managers["dpkg"] = {"count": count}

    # rpm (RHEL/Fedora/openSUSE)
    if shutil.which("rpm"):
        out = _run(["rpm", "-qa"])
        count = len([l for l in out.splitlines() if l.strip()])
        if count:
            managers["rpm"] = {"count": count}

    # pacman (Arch)
    if shutil.which("pacman"):
        out = _run(["pacman", "-Q"])
        count = len([l for l in out.splitlines() if l.strip()])
        if count:
            managers["pacman"] = {"count": count}

    # apk (Alpine)
    if shutil.which("apk"):
        out = _run(["apk", "info"])
        count = len([l for l in out.splitlines() if l.strip()])
        if count:
            managers["apk"] = {"count": count}

    # pip (Python packages)
    if shutil.which("pip3") or shutil.which("pip"):
        pip = shutil.which("pip3") or "pip"
        out = _run([pip, "list", "--format=columns"])
        count = max(0, len(out.splitlines()) - 2)  # header + separator
        if count:
            managers["pip"] = {"count": count}

    return managers


def get_system_info() -> dict:
    """
    Entry point exposed by the bridge as a tool call to the AI.
    Returns a JSON-serializable dictionary with all system info.
    """
    return {
        "distro":   _distro(),
        "kernel":   _kernel(),
        "cpu":      _cpu(),
        "memory":   _memory(),
        "disk":     _disk(),
        "packages": _packages(),
        "hostname": _run(["hostname"]) or platform.node(),
        "uptime":   _run(["uptime", "-p"]),
        "shell":    os.environ.get("SHELL", ""),
        "user":     os.environ.get("USER", os.environ.get("LOGNAME", "")),
    }


if __name__ == "__main__":
    print(json.dumps(get_system_info(), indent=2))
