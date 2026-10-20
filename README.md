# teams-transcripts

When a Teams meeting ends, downloads its transcript (and Teams/Copilot AI notes, if present),
works out which `~/repos` project it belongs to, and files it in `<project>/transcripts/`.
If it isn't confident, it files it in `~/repos/other/transcripts/`.
No Graph API, no admin consent, no prompts, no clicking.

## How it works
1. **Detect**: polls Teams' window list every 3s. A Teams window that isn't a main-app view
   (Chat, Calendar, …) is a meeting window, and its title is the meeting name. When one appears,
   a `start` event goes to `~/.teams-transcripts/meetings.jsonl`. When all meeting windows are
   gone (you hung up), an `end` event is written with the duration. Meetings under 2 minutes are ignored.
2. **Fetch**, from the **Teams desktop app** via macOS Accessibility. No browser, API or downloads.
   It opens the meeting chat, reads the participant list, then Recap → **AI summary** (after
   "Expand all") and Recap → **Transcript**, scrolling through the whole transcript. This works
   even when the organizer blocks transcript downloads, and with Teams hidden.
   **It only fetches when you're away**, so you never see Teams switch views: when the screen is
   locked, the display is asleep, Teams is hidden (⌘H) or minimized, or after 5 min with no input.
   Meetings still unfiled after 2 hours are fetched whenever Teams isn't in active use.
   **File Waiting Meetings Now** in the menu skips the wait. Once the AI summary is there, it files
   right away (a transcript, if any, is included). A transcript without a summary is filed only
   after 45 attempts. Turn off **Only Fetch When I'm Away** to fetch a few minutes after each meeting.
3. **Classify**: sends the title, attendees (participants plus transcript speakers), AI notes and
   transcript to your local `claude` CLI, using your Claude Code login with no API key. It also
   sends a catalog of `~/repos` projects: each one's README/CLAUDE.md, plus the meeting titles and
   attendees previously filed there. The answer is a project, a confidence and a reason. Below
   `min_confidence` (0.6), or with no fit, it goes to `other`. A keyword/attendee heuristic is the
   fallback if the CLI fails. Every confident filing is remembered, so recurring meetings get easier.
4. **Save**, in `<project>/transcripts/`, each file with YAML front matter (attendees, project,
   confidence, reason):
   - `2026-10-20_1100_Weekly-Sync.transcript.md` transcript grouped by speaker, when one exists
   - `2026-10-20_1100_Weekly-Sync.summary.md` AI summary (the usual case; may be the only file)
   A macOS notification says where it went and why.

## The app
`~/Applications/Teams Transcripts.app` is a menu bar app with no Dock icon. Its icon changes to show
whether it's watching, in a meeting, fetching or paused. The menu has:
- **Pause / Resume**: stops watching and fetching (remembered across restarts)
- **Only Fetch When I'm Away** (on by default) and **File Waiting Meetings Now**
- **File a Past Meeting…**: type a meeting name to fetch and file it now
- **Recently Filed**: click one to open its `transcripts` folder
- **Start at Login**: launchd starts it at login and restarts it if it crashes, but not after Quit
- **Open Log / Open Meeting Log / Open Settings**, **Quit**

The app runs this checkout's code, so after editing the code, choose Quit and reopen the app.

## Setup
```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m teams_transcripts build-app   # builds and opens the app
```
Then turn on **Start at Login** in its menu. Grant Accessibility if macOS asks.

Command line, for debugging: `detect` (meeting windows), `fetch "Meeting title" [--dry-run]`,
`classify "Title" file.vtt` (dry-run filing decision), `run` (watcher without UI).
Logs: `~/Library/Logs/teams-transcripts.log`. Settings: `~/.teams-transcripts/config.json`.

## Limits
- Needs the new Teams desktop app running and signed in.
- A transcript exists only if someone turned on transcription. The AI summary needs Copilot or
  Teams Premium, which this account has.
- Meetings are matched by the "Meeting chat <title>" row in the chat list. Ad-hoc calls with no
  meeting chat aren't covered.
- UI automation breaks if Microsoft renames tabs or buttons ("Recap", "AI summary", "Transcript",
  "Copy all", "Expand all"). They're all regexes in `teams_transcripts/teams_app.py`.
