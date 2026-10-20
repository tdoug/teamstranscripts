import json
from pathlib import Path

HOME = Path.home() / ".teams-transcripts"
CONFIG_FILE = HOME / "config.json"
HISTORY_FILE = HOME / "history.json"
PENDING_FILE = HOME / "pending.json"

RECENT_FILE = HOME / "recent.json"
LOG_FILE = Path.home() / "Library/Logs/teams-transcripts.log"

DEFAULTS = {
    "enabled": True,
    "projects_root": "~/repos",
    # Teams needs a few minutes after a meeting to produce the Recap.
    "retry_every_seconds": 120,
    # Fetching switches Teams views, so by default it waits until you're away: screen locked,
    # display asleep, Teams hidden/minimized, or no input for away_idle_minutes. Meetings still
    # unfiled after fallback_after_hours are fetched whenever Teams isn't in active use.
    "fetch_only_when_away": True,
    "away_idle_minutes": 5,
    "fallback_after_hours": 2,
    "give_up_after_attempts": 45,
    # Once the AI summary is in, wait this long after the meeting for a transcript before filing.
    "transcript_grace_minutes": 20,
    # Seconds with no meeting window before the meeting counts as over; shorter meetings are ignored.
    "meeting_end_debounce_seconds": 15,
    "min_meeting_minutes": 2,
    # Auto-filing: below min_confidence the transcript goes to <projects_root>/<fallback_project>.
    "fallback_project": "other",
    "min_confidence": 0.6,
    "classifier_model": "sonnet",
    "claude_path": "",
    "transcript_chars_for_classifier": 60000,
}
MEETINGS_LOG = HOME / "meetings.jsonl"


def load_config():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    for gone in ("headless", "browser_channel", "call_end_debounce_seconds", "give_up_after_minutes"):
        cfg.pop(gone, None)
    save_config(cfg)  # adds any new keys
    cfg["projects_root"] = Path(cfg["projects_root"]).expanduser()
    return cfg


def save_config(cfg):
    out = {**cfg, "projects_root": str(cfg["projects_root"]).replace(str(Path.home()), "~", 1)}
    CONFIG_FILE.write_text(json.dumps(out, indent=2))


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)
