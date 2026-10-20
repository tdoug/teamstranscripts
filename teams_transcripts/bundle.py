"""Build ~/Applications/Teams Transcripts.app and manage its Start-at-Login launch agent."""
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .config import LOG_FILE

NAME = "Teams Transcripts"
BUNDLE_ID = "com.user.teams-transcripts"
APP = Path.home() / "Applications" / f"{NAME}.app"
EXECUTABLE = APP / "Contents/MacOS/teams-transcripts"
AGENT = Path.home() / "Library/LaunchAgents" / f"{BUNDLE_ID}.plist"
REPO = Path(__file__).resolve().parent.parent


def build_app():
    """A thin bundle whose launcher runs this checkout's venv, so code edits take effect on relaunch."""
    if APP.exists():
        shutil.rmtree(APP)
    EXECUTABLE.parent.mkdir(parents=True)
    EXECUTABLE.write_text(f'#!/bin/sh\ncd "{REPO}"\nexec "{sys.executable}" -m teams_transcripts app\n')
    EXECUTABLE.chmod(EXECUTABLE.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (APP / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": EXECUTABLE.name, "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1",
        "LSUIElement": True,  # menu bar only, no Dock icon
        "NSAppleEventsUsageDescription": "Reads Teams window titles to detect when meetings start and end.",
    }))
    subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(APP)], capture_output=True)
    return APP


def login_item_enabled():
    return AGENT.exists()


def set_login_item(on):
    """launchd starts the app at login and restarts it if it crashes, but not after you Quit it."""
    domain = f"gui/{os.getuid()}"
    if not on:
        AGENT.unlink(missing_ok=True)  # don't bootout: that would kill this running app
        return
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    AGENT.write_bytes(plistlib.dumps({
        "Label": BUNDLE_ID,
        "ProgramArguments": [str(EXECUTABLE)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(LOG_FILE), "StandardErrorPath": str(LOG_FILE),
    }))
    subprocess.run(["launchctl", "bootout", f"{domain}/{BUNDLE_ID}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(AGENT)], capture_output=True)
