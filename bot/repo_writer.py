"""
Filesystem and git operations for /repo.
All journal, topic, and memoir files live under /repo (mounted volume).
"""

import os
import subprocess
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_PATH = Path(os.environ.get("REPO_PATH", "/repo"))


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def append_journal(date: str, content: str):
    """
    Append a journal entry to journal/YYYY-MM-DD.md.
    Creates the file with YAML frontmatter if it doesn't exist yet.
    """
    journal_dir = REPO_PATH / "journal"
    _ensure_dir(journal_dir)
    filepath = journal_dir / f"{date}.md"

    if not filepath.exists():
        frontmatter = f"---\ndate: {date}\ntags: [journal]\n---\n\n"
        filepath.write_text(frontmatter, encoding="utf-8")

    timestamp = datetime.now().strftime("%H:%M")
    entry_block = f"\n## {timestamp}\n\n{content}\n"

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(entry_block)

    logger.info(f"Appended to {filepath}")


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

def upsert_topic(slug: str, title: str, content: str):
    """
    Create or update topics/[slug].md with Obsidian-compatible YAML frontmatter.
    If the file exists, appends a new dated section instead of overwriting.
    """
    topics_dir = REPO_PATH / "topics"
    _ensure_dir(topics_dir)
    filepath = topics_dir / f"{slug}.md"

    today = datetime.now().strftime("%Y-%m-%d")

    if not filepath.exists():
        frontmatter = (
            f"---\n"
            f"title: {title}\n"
            f"tags: [topic]\n"
            f"created: {today}\n"
            f"updated: {today}\n"
            f"---\n\n"
            f"# {title}\n\n"
        )
        filepath.write_text(frontmatter + content + "\n", encoding="utf-8")
    else:
        # Update the 'updated' date in frontmatter
        text = filepath.read_text(encoding="utf-8")
        text = _update_frontmatter_field(text, "updated", today)
        # Append a new dated section
        text += f"\n\n## {today}\n\n{content}\n"
        filepath.write_text(text, encoding="utf-8")

    logger.info(f"Upserted topic {filepath}")


def _update_frontmatter_field(text: str, field: str, value: str) -> str:
    """Update a single field in YAML frontmatter."""
    import re
    pattern = rf'^({field}:\s*)(.+)$'
    replacement = rf'\g<1>{value}'
    return re.sub(pattern, replacement, text, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Memoir
# ---------------------------------------------------------------------------

_MAIN_TEMPLATE = r"""\documentclass[12pt,a4paper]{memoir}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[protrusion=true,expansion=false]{microtype}
\usepackage{hyperref}
\usepackage{graphicx}

\title{My Journal Memoir}
\author{}
\date{}

\begin{document}
\maketitle
\tableofcontents

% chapters — do not edit this line
\end{document}
"""


def append_memoir_chapter(date: str, latex_chapter: str):
    """
    Write memoir/chapters/YYYY-MM-DD.tex and register it in memoir/main.tex.
    Creates main.tex if it doesn't exist yet.
    """
    memoir_dir = REPO_PATH / "memoir"
    chapters_dir = memoir_dir / "chapters"
    _ensure_dir(chapters_dir)

    # Write the chapter file
    chapter_file = chapters_dir / f"{date}.tex"
    chapter_file.write_text(latex_chapter, encoding="utf-8")
    logger.info(f"Written chapter file {chapter_file}")

    # Create or update main.tex
    main_file = memoir_dir / "main.tex"
    if not main_file.exists():
        main_file.write_text(_MAIN_TEMPLATE, encoding="utf-8")

    text = main_file.read_text(encoding="utf-8")
    input_line = f"\\input{{chapters/{date}}}"

    if input_line not in text:
        text = text.replace(
            "% chapters — do not edit this line",
            f"{input_line}\n% chapters — do not edit this line",
        )
        main_file.write_text(text, encoding="utf-8")
        logger.info(f"Registered chapter {date} in main.tex")
    else:
        logger.info(f"Chapter {date} already registered in main.tex")


# ---------------------------------------------------------------------------
# Context summaries
# ---------------------------------------------------------------------------

def _read_context_file(filepath: Path) -> str:
    """Read a context file, stripping YAML frontmatter."""
    text = filepath.read_text(encoding="utf-8")
    lines = text.splitlines()
    body = "\n".join(l for l in lines if not l.startswith("---") and not l.startswith("date:")).strip()
    return body


def save_context_summary(date: str, content: str):
    """Save a compact context summary to context/YYYY-MM-DD.md."""
    context_dir = REPO_PATH / "context"
    _ensure_dir(context_dir)
    filepath = context_dir / f"{date}.md"
    filepath.write_text(f"---\ndate: {date}\n---\n\n{content}\n", encoding="utf-8")
    logger.info(f"Saved context summary for {date}")


def save_weekly_context(year: int, week: int, content: str):
    d = REPO_PATH / "context" / "weeks"
    _ensure_dir(d)
    filepath = d / f"{year}-W{week:02d}.md"
    filepath.write_text(f"---\nweek: {year}-W{week:02d}\n---\n\n{content}\n", encoding="utf-8")
    logger.info(f"Saved weekly context {year}-W{week:02d}")


def save_monthly_context(year: int, month: int, content: str):
    d = REPO_PATH / "context" / "months"
    _ensure_dir(d)
    filepath = d / f"{year}-{month:02d}.md"
    filepath.write_text(f"---\nmonth: {year}-{month:02d}\n---\n\n{content}\n", encoding="utf-8")
    logger.info(f"Saved monthly context {year}-{month:02d}")


def save_yearly_context(year: int, content: str):
    d = REPO_PATH / "context" / "years"
    _ensure_dir(d)
    filepath = d / f"{year}.md"
    filepath.write_text(f"---\nyear: {year}\n---\n\n{content}\n", encoding="utf-8")
    logger.info(f"Saved yearly context {year}")


def load_daily_context_for_week(year: int, week: int) -> str:
    """Load all daily context summaries for a given ISO week."""
    from datetime import date as date_cls, timedelta
    context_dir = REPO_PATH / "context"
    chunks = []
    # ISO week: Monday=day 1
    monday = date_cls.fromisocalendar(year, week, 1)
    for i in range(7):
        d = (monday + timedelta(days=i)).isoformat()
        filepath = context_dir / f"{d}.md"
        if filepath.exists():
            body = _read_context_file(filepath)
            if body:
                chunks.append(f"[{d}]\n{body}")
    return "\n\n".join(chunks)


def load_weekly_context_for_month(year: int, month: int) -> str:
    """Load all weekly context summaries that fall within a given month."""
    import calendar
    weeks_dir = REPO_PATH / "context" / "weeks"
    if not weeks_dir.exists():
        return ""
    chunks = []
    for filepath in sorted(weeks_dir.glob(f"{year}-W*.md")):
        # Check if the week overlaps with the month
        stem = filepath.stem  # e.g. 2026-W18
        try:
            w = int(stem.split("W")[1])
            from datetime import date as date_cls
            monday = date_cls.fromisocalendar(year, w, 1)
            if monday.month == month or (monday + __import__('datetime').timedelta(days=6)).month == month:
                body = _read_context_file(filepath)
                if body:
                    chunks.append(f"[{stem}]\n{body}")
        except Exception:
            continue
    return "\n\n".join(chunks)


def load_monthly_context_for_year(year: int) -> str:
    """Load all monthly context summaries for a given year."""
    months_dir = REPO_PATH / "context" / "months"
    if not months_dir.exists():
        return ""
    chunks = []
    for filepath in sorted(months_dir.glob(f"{year}-*.md")):
        body = _read_context_file(filepath)
        if body:
            chunks.append(f"[{filepath.stem}]\n{body}")
    return "\n\n".join(chunks)


def load_recent_context(n_days: int = 7) -> str:
    """Load recent context: daily summaries + latest weekly/monthly if available."""
    from datetime import date as date_cls, timedelta
    today = date_cls.today()
    chunks = []

    # Daily context from last n_days
    for i in range(1, n_days + 1):
        d = (today - timedelta(days=i)).isoformat()
        filepath = REPO_PATH / "context" / f"{d}.md"
        if filepath.exists():
            body = _read_context_file(filepath)
            if body:
                chunks.append(f"[{d}] {body}")

    # Add latest weekly summary if older than n_days
    weeks_dir = REPO_PATH / "context" / "weeks"
    if weeks_dir.exists():
        weekly_files = sorted(weeks_dir.glob("*.md"), reverse=True)
        if weekly_files:
            body = _read_context_file(weekly_files[0])
            if body:
                chunks.append(f"[{weekly_files[0].stem} — weekly]\n{body}")

    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def commit_and_push(message: str):
    """Stage all changes in /repo, commit, and push to origin main."""
    try:
        _git("add", "-A")
        _git("commit", "-m", message)
        _git("push", "origin", "main")
        logger.info(f"Committed and pushed: {message}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e.stderr}")
        raise


def _git(*args):
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO_PATH),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Empty working tree is not an error for commit
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            logger.info("Nothing to commit.")
            return result
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    return result
