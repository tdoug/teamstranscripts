"""Read a meeting's AI summary, transcript and attendees from the Teams desktop app (new Teams).

Drives the app through macOS Accessibility: presses tabs/rows with AXPress and reads text from
the accessibility tree. Teams doesn't need to be in front, and nothing is downloaded, so it also
works when the organizer hasn't allowed transcript downloads. Afterwards it puts Teams back on
the view you had open.
"""
import re
import threading
import time

import AppKit
import ApplicationServices as AX
import Quartz

from .detect import _APP_VIEWS

BUNDLE_IDS = ("com.microsoft.teams2", "com.microsoft.teams")
TRANSCRIPT_HEADER = re.compile(r"^(.+?) ((?:\d+ hours? )?\d+ minutes? \d+ seconds?)$")
BOILERPLATE = re.compile(r"^(AI-generated content may be incorrect|Are these notes useful\?.*|Is this .* useful\?.*|"
                         r"Meeting notes|Follow-up tasks|\.)$")

# Driving the UI is one-at-a-time.
_ui_lock = threading.Lock()


class NotReady(Exception):
    """The meeting chat or its Recap isn't there yet; retry later."""


# --- Accessibility helpers -------------------------------------------------------------------

def _get(el, attr):
    err, v = AX.AXUIElementCopyAttributeValue(el, attr, None)
    return v if err == 0 else None


def _label(el):
    """Visible label: title/description/value, minus Teams' ' / <level>' suffixes."""
    parts = [str(x) for x in (_get(el, "AXTitle"), _get(el, "AXDescription"), _get(el, "AXValue")) if x]
    return parts[0].strip() if parts else ""


def _walk(el):
    yield el
    for child in _get(el, "AXChildren") or []:
        yield from _walk(child)


def _find(root, role, pattern, timeout=8.0):
    rx = re.compile(pattern, re.I)
    deadline = time.time() + timeout
    while True:
        for el in _walk(root()):
            if _get(el, "AXRole") == role and rx.search(_label(el)):
                return el
        if time.time() > deadline:
            return None
        time.sleep(0.5)


def _press(el, settle=1.5):
    AX.AXUIElementPerformAction(el, "AXPress")
    time.sleep(settle)


def _teams_pid():
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() in BUNDLE_IDS:
            return app.processIdentifier()
    return None


def _escape(pid):
    for down in (True, False):
        Quartz.CGEventPostToPid(pid, Quartz.CGEventCreateKeyboardEvent(None, 53, down))
    time.sleep(1)


def permission_ok():
    return AX.AXIsProcessTrusted()


def _idle_seconds():
    return Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGAnyInputEventType)


def away_reason(idle_minutes):
    """Why it's OK to drive Teams unseen right now (None = you might be watching)."""
    session = Quartz.CGSessionCopyCurrentDictionary() or {}
    if session.get("CGSSessionScreenIsLocked"):
        return "screen locked"
    if Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID()):
        return "display asleep"
    pid = _teams_pid()
    if pid:
        if AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid).isHidden():
            return "Teams hidden"
        win = _main_window(AX.AXUIElementCreateApplication(pid))
        if win is not None and _get(win, "AXMinimized"):
            return "Teams minimized"
    if _idle_seconds() >= idle_minutes * 60:
        return f"idle {idle_minutes}+ min"
    return None


def user_busy(idle_seconds=30):
    """True if you're actively using Teams; fetching then waits so it doesn't switch views under you."""
    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not front or front.bundleIdentifier() not in BUNDLE_IDS:
        return False
    return _idle_seconds() < idle_seconds


# --- Reading the Recap ---------------------------------------------------------------------

def _bullet(text, indent=""):
    m = re.match(r"^([^:]{1,80}):\s*(.*)$", text, re.S)
    return f"{indent}- **{m.group(1)}:** {m.group(2)}" if m else f"{indent}- {text}"


def _read_summary(window):
    """Markdown from Recap → AI summary (Meeting notes + Follow-up tasks), or None if not there."""
    tab = _find(window, "AXRadioButton", r"^AI summary")
    if not tab:
        return None
    _press(tab)
    if not _find(window, "AXButton", r"^Copy all", timeout=10):
        return None  # still generating, or no AI summary for this meeting
    expand = _find(window, "AXButton", r"^Expand all", timeout=2)
    if expand:
        _press(expand, settle=1)

    els = list(_walk(window()))
    start = next(i for i, e in enumerate(els) if _get(e, "AXRole") == "AXButton" and _label(e) == "Copy all")
    lines, rows = [], []
    for el in els[start + 1:]:
        role, text = _get(el, "AXRole"), _label(el)
        if not text:
            continue
        if role == "AXHeading":
            lines.append(f"\n### {text}\n")
        elif role == "AXRow":
            rows.append(text)
            lines.append(_bullet(text[2:], "  ") if text.startswith(". ") else _bullet(text))
        elif role == "AXStaticText" and not BOILERPLATE.match(text.strip()):
            # Row text is repeated in child (and hidden duplicate) static texts; keep only loose text.
            if not any(text.strip() in r for r in rows):
                lines.append(text.strip())
    md = "\n".join(lines).strip()
    return md or None


def _timestamp(when):
    nums = dict((unit, int(n)) for n, unit in re.findall(r"(\d+) (hour|minute|second)", when))
    return f"{nums.get('hour', 0):02}:{nums.get('minute', 0):02}:{nums.get('second', 0):02}"


def _transcript_list(window):
    for el in _walk(window()):
        if _get(el, "AXSubrole") == "AXLandmarkComplementary" and _label(el) == "Transcript":
            for e in _walk(el):
                if _get(e, "AXSubrole") == "AXContentList":
                    return e
    return None


def _read_transcript(window):
    """[(hh:mm:ss, speaker, text)] from Recap → Transcript, scrolling through the whole list."""
    tab = _find(window, "AXRadioButton", r"^Transcript$")
    if not tab:
        return None
    _press(tab, settle=3)
    lst = _transcript_list(window)
    if not lst:
        return None

    seen, order, idle = {}, [], 0
    while idle < 3:  # stop after a few scrolls that load nothing new
        items = []
        for item in _get(lst, "AXChildren") or []:
            header = next((TRANSCRIPT_HEADER.match(_label(e)) for e in _walk(item)
                           if _get(e, "AXRole") == "AXGroup" and TRANSCRIPT_HEADER.match(_label(e))), None)
            if not header:
                continue  # "X started/stopped transcription" and similar events
            who, when = header.groups()
            texts = [_label(e) for e in _walk(item) if _get(e, "AXRole") == "AXStaticText"]
            body = " ".join(t.strip() for t in texts if t.strip() not in (who, when, f"{who} {when}")).strip()
            items.append(((who, when), body, item))
        new = [i for i in items if i[0] not in seen]
        for key, body, _ in new:
            seen[key] = body
            order.append(key)
        idle = 0 if new else idle + 1
        if not items:
            break
        AX.AXUIElementPerformAction(items[-1][2], "AXScrollToVisible")
        time.sleep(0.7)
    return [(_timestamp(when), who, seen[(who, when)]) for who, when in order] or None


def _read_attendees(window, pid):
    btn = _find(window, "AXPopUpButton", r"^View and add participants", timeout=3)
    if not btn:
        return []
    _press(btn)
    names = []
    for w in _get(AX.AXUIElementCreateApplication(pid), "AXWindows") or []:
        for el in _walk(w):
            if _get(el, "AXRole") == "AXMenuItem":
                first = next((_label(s) for s in _walk(el) if _get(s, "AXRole") == "AXStaticText"), "")
                if first and first not in names:
                    names.append(first)
    _escape(pid)
    return names


# --- Navigation ----------------------------------------------------------------------------

def _main_window(app):
    windows = _get(app, "AXWindows") or []
    for w in windows:
        if (_get(w, "AXTitle") or "").split("|")[0].strip().lower() in _APP_VIEWS:
            return w
    return windows[0] if windows else None


def _selected_chat(window):
    for el in _walk(window()):
        if _get(el, "AXRole") == "AXRow" and _get(el, "AXSelected"):
            return _label(el)
    return None


def _open_meeting_chat(window, title):
    chat = _find(window, "AXCheckBox", r"^Chat \(", timeout=3)
    if chat and not _get(chat, "AXValue"):
        _press(chat)
    row_rx = rf"^Meeting chat {re.escape(title)}( |$)"
    row = _find(window, "AXRow", row_rx, timeout=4)
    if not row:
        more = _find(window, "AXRow", r"^See more$", timeout=1)
        if more:
            _press(more)
            row = _find(window, "AXRow", row_rx, timeout=4)
    if not row:
        raise NotReady(f"no 'Meeting chat {title}' in the Teams chat list yet")
    _press(row, settle=2.5)


def _restore(window, view, chat_row):
    """Put Teams back on the view/chat you had open."""
    tab = _find(window, "AXRadioButton", r"^Chat$", timeout=1)
    if tab:
        _press(tab, settle=0.5)
    if view and view != "chat":
        btn = _find(window, "AXCheckBox", rf"^{re.escape(view)} \(", timeout=2)
        if btn:
            _press(btn, settle=0.5)
    elif chat_row:
        row = _find(window, "AXRow", rf"^{re.escape(chat_row)}$", timeout=2)
        if row:
            _press(row, settle=0.5)


def fetch(title):
    """{"notes": markdown|None, "transcript": [(ts, who, text)]|None, "attendees": [names]}.

    Raises NotReady if Teams isn't running or the meeting chat / Recap isn't available yet.
    """
    with _ui_lock:
        pid = _teams_pid()
        if not pid:
            raise NotReady("Teams isn't running")
        app = AX.AXUIElementCreateApplication(pid)
        win = _main_window(app)
        if win is None:
            raise NotReady("no Teams window")

        def window():
            return _main_window(app) or win

        view = (_get(win, "AXTitle") or "").split("|")[0].strip().lower()
        previous_chat = _selected_chat(window) if view == "chat" else None
        try:
            _open_meeting_chat(window, title)
            attendees = _read_attendees(window, pid)
            recap = _find(window, "AXRadioButton", r"^Recap", timeout=5)
            if not recap:
                raise NotReady("meeting chat has no Recap tab yet")
            _press(recap, settle=3)
            notes = _read_summary(window)
            transcript = _read_transcript(window)
            return {"notes": notes, "transcript": transcript, "attendees": attendees}
        finally:
            _restore(window, view, previous_chat)
