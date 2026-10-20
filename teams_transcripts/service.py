"""The watcher: logs meeting start/end from Teams windows, then fetches and files each meeting."""
import fcntl
import json
import logging
import subprocess
import threading
import time
from datetime import datetime, timedelta

from . import classify, detect, store, teams_app
from .config import HISTORY_FILE, HOME, MEETINGS_LOG, PENDING_FILE, RECENT_FILE, read_json, save_config, write_json

log = logging.getLogger("teams-transcripts")
PERMISSION_STATUS = "⚠ Allow Accessibility in System Settings"


def record(event, **fields):
    """Append a meeting start/end event to ~/.teams-transcripts/meetings.jsonl."""
    entry = {"event": event, "time": datetime.now().isoformat(timespec="seconds"), **fields}
    with MEETINGS_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("%s %s", event, fields)


def enqueue(title, started, ended):
    jobs = read_json(PENDING_FILE, [])
    jobs.append({"title": title, "started": started.isoformat(), "ended": ended.isoformat(),
                 "next_try": ended.isoformat()})
    write_json(PENDING_FILE, jobs)


def notify(title, message):
    script = 'on run argv\ndisplay notification (item 2 of argv) with title "Teams transcript" subtitle (item 1 of argv)\nend run'
    subprocess.run(["osascript", "-e", script, title, message])


def file_meeting(cfg, job, got):
    """Classify the downloaded meeting and save it into <project>/transcripts/."""
    turns = got["transcript"] or []
    transcript_md = store.transcript_markdown(turns)
    attendees = list(dict.fromkeys(got.get("attendees", []) + store.speakers(turns)))

    history = read_json(HISTORY_FILE, {})
    guess = classify.classify(cfg, history, {"title": job["title"], "attendees": attendees,
                                             "notes": got.get("notes"), "transcript": transcript_md})
    started = datetime.fromisoformat(job["started"])
    meta = {"title": job["title"], "date": started.isoformat(timespec="minutes"),
            "ended": job["ended"][:16], "attendees": attendees, "project": guess["project"],
            "confidence": round(guess["confidence"], 2), "reason": guess["reason"],
            "has_summary": bool(got.get("notes")), "has_transcript": bool(turns)}
    if guess.get("guessed", cfg["fallback_project"]) != cfg["fallback_project"]:
        meta["best_guess"] = guess["guessed"]
    saved = store.save(cfg["projects_root"] / guess["project"], meta, started, transcript_md, got.get("notes"))

    if guess["project"] != cfg["fallback_project"]:
        classify.learn(history, guess["project"], job["title"], attendees)
        write_json(HISTORY_FILE, history)
    log.info("filed '%s' -> %s (%.2f: %s): %s", job["title"], guess["project"], guess["confidence"],
             guess["reason"], ", ".join(map(str, saved)))
    what = " + ".join(k for k, v in (("summary", got.get("notes")), ("transcript", turns)) if v)
    notify(job["title"], f"Filed {what} to {guess['project']}/transcripts — {guess['reason']}")
    return {"title": job["title"], "project": guess["project"], "what": what,
            "folder": str(cfg["projects_root"] / guess["project"] / "transcripts"),
            "time": datetime.now().isoformat(timespec="minutes")}


def _may_fetch(cfg, job, now, force):
    """Fetching switches Teams views, so by default only do it when you won't see it."""
    if force or not cfg["fetch_only_when_away"]:
        return not teams_app.user_busy()
    if teams_app.away_reason(cfg["away_idle_minutes"]):
        return True
    # Fallback so nothing waits forever: once overdue, fetch whenever Teams isn't in use.
    overdue = now - datetime.fromisoformat(job["ended"]) > timedelta(hours=cfg["fallback_after_hours"])
    return overdue and not teams_app.user_busy()


def process_queue(cfg, on_status=lambda s: None, on_filed=lambda f: None, force=False):
    """Try each due job once; reschedule NotReady, drop after too many attempts."""
    now = datetime.now()
    jobs = read_json(PENDING_FILE, [])
    remaining = []
    for job in jobs:
        if datetime.fromisoformat(job["next_try"]) > now or not _may_fetch(cfg, job, now, force):
            remaining.append(job)
            continue
        on_status(f"Fetching: {job['title']}")
        waited = now - datetime.fromisoformat(job["ended"])
        # Count attempts rather than elapsed time: when away-only, attempts can be hours apart.
        job["attempts"] = job.get("attempts", 0) + 1
        expired = job["attempts"] >= cfg["give_up_after_attempts"]
        try:
            got = teams_app.fetch(job["title"])
            grace_over = waited > timedelta(minutes=cfg["transcript_grace_minutes"])
            # The AI summary matters most: file once it's here (waiting briefly for a transcript too).
            # A transcript alone is only filed once we've given up waiting for the summary.
            if got["notes"] and (got["transcript"] or grace_over) or got["transcript"] and expired:
                on_filed(file_meeting(cfg, job, got))
                continue
            missing = [k for k, v in (("AI summary", got["notes"]), ("transcript", got["transcript"])) if not v]
            raise teams_app.NotReady(f"{' and '.join(missing)} not available yet")
        except teams_app.NotReady as e:
            log.info("'%s' not ready: %s", job["title"], e)
        except Exception:
            log.exception("fetch failed for '%s'", job["title"])
        if expired:
            log.warning("giving up on '%s'", job["title"])
            notify(job["title"], "No AI summary or transcript appeared in the meeting's Recap")
            continue
        job["next_try"] = (now + timedelta(seconds=cfg["retry_every_seconds"])).isoformat()
        remaining.append(job)
    write_json(PENDING_FILE, remaining)


def single_instance():
    """Hold a lock for the life of the process; False if another instance already runs."""
    global _lock_file
    _lock_file = open(HOME / "instance.lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


class Service:
    """Runs the watch loop on a background thread; the menu bar app reads its state."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.status = "Starting…"
        self.meeting = None  # title of the meeting in progress
        self.recent = read_json(RECENT_FILE, [])
        self.pending = 0
        self._force = False

    def file_now(self):
        """Fetch waiting meetings on the next pass even if you're not away."""
        self._force = True

    @property
    def enabled(self):
        return self.cfg["enabled"]

    def set_enabled(self, on):
        self.cfg["enabled"] = on
        save_config(self.cfg)
        log.info("%s", "resumed" if on else "paused")

    def _filed(self, entry):
        self.recent = ([entry] + self.recent)[:10]
        write_json(RECENT_FILE, self.recent)

    def start(self):
        threading.Thread(target=self.run_forever, daemon=True, name="watcher").start()

    def run_forever(self):
        log.info("watching Teams meeting windows; projects in %s", self.cfg["projects_root"])
        while True:
            try:
                self._loop()
            except Exception:
                log.exception("watcher crashed; restarting in 10s")
                time.sleep(10)

    def _loop(self):
        cfg = self.cfg
        started, title, last_seen, last_windows = None, None, None, None
        last_queue = 0
        while True:
            if not cfg["enabled"]:
                started, title, self.meeting, self.status = None, None, None, "Paused"
                time.sleep(3)
                continue

            try:
                if not teams_app.permission_ok():
                    raise PermissionError("Accessibility permission not granted")
                windows = detect.teams_windows()
            except PermissionError as e:
                if self.status != PERMISSION_STATUS:
                    log.error("can't read Teams windows: %s", e)
                self.status = PERMISSION_STATUS
                time.sleep(10)
                continue
            if windows != last_windows:
                log.debug("teams windows: %s", windows)  # handy for calibrating window-title rules
                last_windows = windows
            meetings = [t for t in (detect.meeting_title(w) for w in windows) if t is not None]
            now = datetime.now()

            if meetings:
                if not started:
                    started = now
                    record("start", title=meetings[0], windows=windows)
                title = next((t for t in meetings if t), title)  # latest real title wins
                last_seen = now
                self.meeting, self.status = title, f"In meeting: {title or 'untitled'}"
            elif started and (now - last_seen).total_seconds() > cfg["meeting_end_debounce_seconds"]:
                minutes = (last_seen - started).total_seconds() / 60
                record("end", title=title, started=started.isoformat(timespec="seconds"),
                       duration_min=round(minutes, 1))
                if not title:
                    log.warning("meeting window had no title; can't locate its chat")
                elif minutes < cfg["min_meeting_minutes"]:
                    log.info("ignoring %.1f-minute meeting '%s'", minutes, title)
                else:
                    enqueue(title, started, last_seen)
                started, title, self.meeting = None, None, None

            # Fetching blocks the loop, so only between meetings (and never mid-call in Teams).
            if not started and (self._force or time.time() - last_queue > 30):
                last_queue = time.time()
                force, self._force = self._force, False
                process_queue(cfg, on_status=self._set_status, on_filed=self._filed, force=force)
            self.pending = len(read_json(PENDING_FILE, []))
            if not started:
                self.status = "Watching for Teams meetings"
            time.sleep(3)

    def _set_status(self, s):
        self.status = s
