"""teams-transcripts: when a Teams meeting ends, fetch its AI summary/transcript and file it in the right project.

  python -m teams_transcripts build-app             build ~/Applications/Teams Transcripts.app and open it
  python -m teams_transcripts app                   run the menu bar app (what the .app runs)
  python -m teams_transcripts run                   run the watcher without UI
  python -m teams_transcripts fetch "Title"         file a past meeting now (--dry-run: just print it)
  python -m teams_transcripts classify "Title" F    dry-run: which project would transcript F go to?
  python -m teams_transcripts detect                show current meeting-window state
"""
import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import classify, detect, service, store, teams_app
from .config import HISTORY_FILE, LOG_FILE, load_config, read_json


def main():
    ap = argparse.ArgumentParser(prog="teams_transcripts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build-app", "app", "run", "detect"):
        sub.add_parser(name)
    f = sub.add_parser("fetch")
    f.add_argument("title")
    f.add_argument("--dry-run", action="store_true", help="print what Teams has; don't file anything")
    c = sub.add_parser("classify")
    c.add_argument("title")
    c.add_argument("transcript", type=Path)
    args = ap.parse_args()

    handlers = [logging.StreamHandler()]
    if args.cmd in ("app", "run") and not sys.stderr.isatty():
        handlers = [logging.FileHandler(LOG_FILE)]  # launched from Finder/launchd
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    cfg = load_config()

    if args.cmd == "build-app":
        from . import bundle
        app = bundle.build_app()
        print(f"Built {app}")
        subprocess.run(["open", str(app)])
    elif args.cmd == "app":
        from .app import run_app
        run_app(cfg)
    elif args.cmd == "run":
        if not service.single_instance():
            sys.exit("Already running (menu bar app or another watcher).")
        service.Service(cfg).run_forever()
    elif args.cmd == "detect":
        windows = detect.teams_windows()
        print("teams windows:", windows)
        print("meeting windows:", [t for t in map(detect.meeting_title, windows) if t is not None])
    elif args.cmd == "fetch" and args.dry_run:
        got = teams_app.fetch(args.title)
        print("ATTENDEES:", got["attendees"])
        print("\nAI SUMMARY:\n", got["notes"] or "<none>")
        turns = got["transcript"] or []
        print(f"\nTRANSCRIPT: {len(turns)} entries")
        print(store.transcript_markdown(turns[:5] + turns[-3:]) if turns else "<none>")
    elif args.cmd == "fetch":
        now = datetime.now()
        # A past meeting: don't wait out the transcript grace period.
        service.enqueue(args.title, now, now - timedelta(minutes=cfg["transcript_grace_minutes"] + 1))
        service.process_queue(cfg)
    elif args.cmd == "classify":
        text = args.transcript.read_text(encoding="utf-8-sig")
        turns = store.parse_vtt(text)
        md = store.transcript_markdown(turns) if turns else text
        print(json.dumps(classify.classify(cfg, read_json(HISTORY_FILE, {}), {
            "title": args.title, "attendees": store.speakers(turns), "transcript": md}), indent=2))


if __name__ == "__main__":
    main()
