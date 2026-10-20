"""Guess the project a meeting belongs to; fall back to the 'other' project when unsure.

Uses the local `claude` CLI (your existing Claude Code login, no API key). If that's
unavailable, a keyword/attendee heuristic is used instead.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import HOME

SCHEMA = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["project", "confidence", "reason"],
}

INSTRUCTIONS = """You file Microsoft Teams meeting transcripts into project folders.
Using the meeting title, attendees, AI notes and transcript, pick the ONE project folder from the
catalog this meeting is about. Weigh the evidence: project/customer/product names mentioned,
past meetings and attendees already filed to each project, and each project's description.
If no project clearly fits, or it's split across several, answer "other".
confidence = probability you're right (0-1); be honest — below the threshold it goes to "other".
reason = one short sentence citing the deciding evidence."""


def _describe(project_dir: Path):
    for name in ("README.md", "CLAUDE.md", "readme.md", "README"):
        f = project_dir / name
        if f.is_file():
            return re.sub(r"\s+", " ", f.read_text(errors="ignore"))[:600]
    pkg = project_dir / "package.json"
    if pkg.is_file():
        try:
            return json.loads(pkg.read_text()).get("description", "")
        except json.JSONDecodeError:
            pass
    return ""


def catalog(projects_root: Path, history, exclude):
    out = []
    for d in sorted(p for p in projects_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if d.name == exclude:
            continue
        learned = history.get("projects", {}).get(d.name, {})
        out.append({
            "project": d.name,
            "description": _describe(d),
            "past_meeting_titles": learned.get("titles", [])[-15:],
            "frequent_attendees": sorted(learned.get("attendees", {}),
                                         key=lambda n: -learned["attendees"][n])[:15],
        })
    return out


def _claude_path(cfg):
    return cfg.get("claude_path") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def _ask_claude(cfg, payload):
    r = subprocess.run(
        [_claude_path(cfg), "-p", INSTRUCTIONS, "--output-format", "json",
         "--json-schema", json.dumps(SCHEMA), "--model", cfg["classifier_model"],
         "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
        input=json.dumps(payload, indent=1), capture_output=True, text=True, timeout=300, cwd=HOME,
    )
    out = json.loads(r.stdout)
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(out.get("result") or r.stderr)
    return out["structured_output"]


def _heuristic(projects, meeting):
    text = " ".join([meeting["title"], meeting.get("notes") or "", meeting.get("transcript", "")]).lower()
    attendees = set(meeting.get("attendees", []))
    best, best_score = None, 0.0
    for p in projects:
        score = 0.0
        if meeting["title"] in p["past_meeting_titles"]:
            score += 5
        for tok in (t for t in re.split(r"[-_. ]+", p["project"].lower()) if len(t) > 2):
            score += 2 * meeting["title"].lower().count(tok) + min(text.count(tok), 5) * 0.4
        score += len(attendees & set(p["frequent_attendees"]))
        if score > best_score:
            best, best_score = p["project"], score
    return {"project": best or "other", "confidence": min(best_score / 8, 0.9),
            "reason": f"heuristic score {best_score:.1f} (claude CLI unavailable)"}


def classify(cfg, history, meeting):
    """meeting: {title, attendees, notes, transcript}. Returns {project, confidence, reason}."""
    fallback = cfg["fallback_project"]
    projects = catalog(cfg["projects_root"], history, exclude=fallback)
    payload = {
        "confidence_threshold": cfg["min_confidence"],
        "projects": projects,
        "meeting": {
            "title": meeting["title"],
            "attendees": meeting.get("attendees", []),
            "ai_notes": (meeting.get("notes") or "")[:10000],
            "transcript": meeting.get("transcript", "")[:cfg["transcript_chars_for_classifier"]],
        },
    }
    try:
        guess = _ask_claude(cfg, payload)
    except Exception as e:
        guess = _heuristic(projects, meeting)
        guess["reason"] += f"; claude error: {str(e)[:120]}"
    names = {p["project"] for p in projects}
    if guess["project"] not in names or guess["confidence"] < cfg["min_confidence"]:
        guess = {**guess, "guessed": guess["project"], "project": fallback}
    return guess


def learn(history, project, title, attendees):
    """Remember what got filed where so future guesses improve."""
    p = history.setdefault("projects", {}).setdefault(project, {"titles": [], "attendees": {}})
    if title not in p["titles"]:
        p["titles"].append(title)
    for a in attendees:
        p["attendees"][a] = p["attendees"].get(a, 0) + 1
