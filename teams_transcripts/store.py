import json
import re
from datetime import datetime
from pathlib import Path


def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:60] or "meeting"


def parse_vtt(vtt):
    """Teams VTT -> [(timestamp, speaker, text)], merging consecutive lines by the same speaker."""
    turns = []
    for block in vtt.split("\n\n"):
        lines = [l for l in block.splitlines() if l.strip()]
        ts = next((l.split(".")[0] for l in lines if "-->" in l), "")
        for line in lines:
            m = re.match(r"<v ([^>]+)>(.*?)(?:</v>)?$", line)
            if not m:
                continue
            who, text = m.group(1), m.group(2).strip()
            if turns and turns[-1][1] == who:
                turns[-1] = (turns[-1][0], who, turns[-1][2] + " " + text)
            else:
                turns.append((ts, who, text))
    return turns


def transcript_markdown(turns):
    return "\n".join(f"**{who}** `{ts}`  \n{text}\n" for ts, who, text in turns)


def speakers(turns):
    return list(dict.fromkeys(who for _, who, _ in turns))


def _front_matter(meta):
    return "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in meta.items()) + "---\n\n"


def save(project_dir: Path, meta, started: datetime, transcript_md, notes):
    """Write the AI summary and transcript (whichever exist) into <project>/transcripts/."""
    tdir = project_dir / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    base = f"{started:%Y-%m-%d_%H%M}_{slug(meta['title'])}"
    header = _front_matter(meta) + f"# {meta['title']}\n\n"

    saved = []
    if transcript_md:
        md = tdir / f"{base}.transcript.md"
        md.write_text(header + transcript_md)
        saved.append(md)
    if notes:
        s = tdir / f"{base}.summary.md"
        s.write_text(header + "## AI summary (from Teams Recap)\n\n" + notes + "\n")
        saved.append(s)
    return saved
