"""Menu bar app: status, pause/resume, recent filings, sign-in, start at login, quit."""
import logging
import subprocess
from datetime import datetime, timedelta

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

from . import bundle, service
from .config import CONFIG_FILE, LOG_FILE, MEETINGS_LOG, save_config

log = logging.getLogger("teams-transcripts")

ICONS = {"Paused": "⏸", "In meeting": "🎙", "Fetching": "⏳"}


class TranscriptsApp(rumps.App):
    def __init__(self, svc):
        super().__init__(bundle.NAME, title="📝", quit_button=None)
        self.svc = svc
        self.status_item = rumps.MenuItem("Starting…")
        self.queue_item = rumps.MenuItem("")
        self.toggle_item = rumps.MenuItem("Pause", callback=self.on_toggle)
        self.recent_menu = rumps.MenuItem("Recently Filed")
        self.login_item = rumps.MenuItem("Start at Login", callback=self.on_login_item)
        self.away_item = rumps.MenuItem("Only Fetch When I'm Away", callback=self.on_away)
        self.file_now_item = rumps.MenuItem("File Waiting Meetings Now", callback=self.on_file_now)
        self.menu = [
            self.status_item, self.queue_item, self.file_now_item, None,
            self.toggle_item, self.away_item,
            rumps.MenuItem("File a Past Meeting…", callback=self.on_fetch),
            self.recent_menu, None,
            self.login_item,
            rumps.MenuItem("Open Log", callback=lambda _: subprocess.run(["open", "-a", "Console", str(LOG_FILE)])),
            rumps.MenuItem("Open Meeting Log", callback=lambda _: subprocess.run(["open", "-t", str(MEETINGS_LOG)])),
            rumps.MenuItem("Open Settings", callback=lambda _: subprocess.run(["open", "-t", str(CONFIG_FILE)])),
            None,
            rumps.MenuItem("Quit Teams Transcripts", callback=lambda _: rumps.quit_application()),
        ]
        self._recent_shown = None
        self.refresh(None)
        rumps.Timer(self.refresh, 2).start()

    # Runs on the main thread; the watcher thread only updates plain attributes.
    def refresh(self, _):
        s = self.svc
        self.status_item.title = s.status
        self.title = next((icon for prefix, icon in ICONS.items() if s.status.startswith(prefix)), "📝")
        if not s.pending:
            self.queue_item.title = "Nothing waiting"
        elif s.cfg["fetch_only_when_away"]:
            self.queue_item.title = f"{s.pending} meeting(s) to file when you step away"
        else:
            self.queue_item.title = f"{s.pending} meeting(s) waiting for Recap"
        self.file_now_item.set_callback(self.on_file_now if s.pending else None)
        self.away_item.state = s.cfg["fetch_only_when_away"]
        self.toggle_item.title = "Pause" if s.enabled else "Resume"
        self.login_item.state = bundle.login_item_enabled()
        if s.recent != self._recent_shown:
            self._recent_shown = list(s.recent)
            if len(self.recent_menu):
                self.recent_menu.clear()
            for r in s.recent or [{"title": "Nothing yet"}]:
                label = f"{r['title']} → {r['project']}" if "project" in r else r["title"]
                folder = r.get("folder")
                self.recent_menu.add(rumps.MenuItem(label, callback=(lambda _, f=folder: subprocess.run(["open", f])) if folder else None))

    def on_toggle(self, _):
        self.svc.set_enabled(not self.svc.enabled)
        self.refresh(None)

    def on_away(self, _):
        self.svc.cfg["fetch_only_when_away"] = not self.svc.cfg["fetch_only_when_away"]
        save_config(self.svc.cfg)
        self.refresh(None)

    def on_file_now(self, _):
        self.svc.file_now()

    def on_login_item(self, _):
        bundle.set_login_item(not bundle.login_item_enabled())
        self.refresh(None)

    def on_fetch(self, _):
        r = rumps.Window("Meeting name as it appears in your Teams chat list:", "File a past meeting",
                         ok="File", cancel="Cancel", dimensions=(320, 24)).run()
        if r.clicked and r.text.strip():
            now = datetime.now()
            grace = timedelta(minutes=self.svc.cfg["transcript_grace_minutes"] + 1)
            service.enqueue(r.text.strip(), now, now - grace)  # past meeting: no grace wait
            self.svc.file_now()  # you asked for it, so don't wait until you're away


def run_app(cfg):
    if not service.single_instance():
        service.notify("Already running", "Teams Transcripts is in the menu bar")
        return
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    svc = service.Service(cfg)
    svc.start()
    TranscriptsApp(svc).run()
