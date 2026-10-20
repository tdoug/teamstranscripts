"""Detect Teams meetings from Teams' own windows (no API, no mic heuristics).

A meeting is in progress while Teams has a window that isn't one of its main app views
(Chat, Calendar, …). It is over once every such window is gone. A minimized meeting still
has its window.
"""
import subprocess

_WINDOWS_SCRIPT = """
set out to ""
tell application "System Events"
  repeat with pname in {"MSTeams", "Microsoft Teams", "Microsoft Teams (work or school)"}
    if exists process (pname as text) then
      repeat with w in windows of process (pname as text)
        set out to out & (name of w) & linefeed
      end repeat
    end if
  end repeat
end tell
return out
"""

# First title segment of Teams' main-window views; anything else is treated as a meeting window.
_APP_VIEWS = {"chat", "activity", "calendar", "teams", "calls", "onedrive", "apps", "files",
              "settings", "copilot", "people", "communities", "microsoft teams", "notifications",
              "search", "planner", "approvals", "viva", "help", ""}
# Generic title segments, e.g. the pre-join screen "Meeting join | <subject> | Microsoft Teams".
_NOISE = {"microsoft teams", "meeting", "meeting join", "compact meeting view", "meeting compact view"}


def teams_windows():
    """Window titles of the Teams app. Requires Accessibility permission for this process."""
    r = subprocess.run(["osascript", "-e", _WINDOWS_SCRIPT], capture_output=True, text=True)
    if r.returncode:
        raise PermissionError(r.stderr.strip())  # usually missing Accessibility/Automation permission
    # The same process can match two of the names above, so de-duplicate.
    return list(dict.fromkeys(t.strip() for t in r.stdout.splitlines() if t.strip()))


def meeting_title(window_title):
    """'Weekly Sync | Microsoft Teams' -> 'Weekly Sync'; main-window views -> None."""
    parts = [p.strip() for p in window_title.split("|")]
    if parts and parts[0].lower() in _APP_VIEWS:
        return None
    parts = [p for p in parts if p.lower() not in _NOISE]
    return parts[0] if parts else ""  # "" = meeting window with no usable title


def current_meetings():
    """Titles of open meeting windows (empty list = not in a meeting)."""
    titles = (meeting_title(w) for w in teams_windows())
    return [t for t in titles if t is not None]
