"""Run `tracker refresh` weekly with launchd, macOS's built-in scheduler.

`tracker schedule install` writes a LaunchAgent to ~/Library/LaunchAgents and loads it.
It runs once a week (default Monday 06:00 local time); if the Mac is asleep then, launchd
runs it at the next wake. Output goes to data/refresh/launchd.log.

macOS privacy note: a launchd job can't read ~/Downloads, ~/Documents, or ~/Desktop
unless the program it runs has been granted access (System Settings > Privacy & Security
> Files and Folders or Full Disk Access). Keep the project elsewhere, or grant uv access.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

LABEL = "com.texas-dc-tracker.refresh"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PROJECT_DIR = Path(__file__).resolve().parents[2]
PROTECTED = ("Downloads", "Documents", "Desktop")
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def build_plist(
    uv_path: str,
    project_dir: Path = PROJECT_DIR,
    *,
    weekday: int = 1,
    hour: int = 6,
    minute: int = 0,
) -> bytes:
    log = str(project_dir / "data" / "refresh" / "launchd.log")
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [
                uv_path,
                "--directory",
                str(project_dir),
                "run",
                "tracker",
                "refresh",
            ],
            "WorkingDirectory": str(project_dir),
            "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            "ProcessType": "Background",
        }
    )


def protected_folder(project_dir: Path = PROJECT_DIR) -> str | None:
    home = Path.home()
    for name in PROTECTED:
        if project_dir.is_relative_to(home / name):
            return name
    return None


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(weekday: int = 1, hour: int = 6, minute: int = 0) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv not found on PATH")
    (PROJECT_DIR / "data" / "refresh").mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if is_loaded():
        subprocess.run(["launchctl", "bootout", _domain(), str(PLIST_PATH)], check=False)
    PLIST_PATH.write_bytes(build_plist(uv, weekday=weekday, hour=hour, minute=minute))
    subprocess.run(["launchctl", "bootstrap", _domain(), str(PLIST_PATH)], check=True)
    return PLIST_PATH


def uninstall() -> bool:
    if not PLIST_PATH.exists():
        return False
    subprocess.run(["launchctl", "bootout", _domain(), str(PLIST_PATH)], check=False)
    PLIST_PATH.unlink()
    return True


def is_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True, text=True
    )
    return result.returncode == 0


def run_now() -> None:
    """Start the scheduled job immediately (same environment as the weekly run)."""
    subprocess.run(["launchctl", "kickstart", f"{_domain()}/{LABEL}"], check=True)
