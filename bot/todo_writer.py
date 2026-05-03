"""
Todo filesystem operations for /repo/todos/.
Files are stored as todos/YYYY-MM-DD.md, Obsidian-compatible.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_PATH = Path(os.environ.get("REPO_PATH", "/repo"))


def _todos_dir() -> Path:
    d = REPO_PATH / "todos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _todo_file(date: str) -> Path:
    return _todos_dir() / f"{date}.md"


def _create_file_if_needed(date: str):
    filepath = _todo_file(date)
    if not filepath.exists():
        frontmatter = (
            f"---\n"
            f"date: {date}\n"
            f"tags: [todo]\n"
            f"---\n\n"
            f"# Todos {date}\n\n"
        )
        filepath.write_text(frontmatter, encoding="utf-8")
    return filepath


def _parse_todo_line(line: str) -> dict | None:
    """Parse a markdown todo line into a dict with text, done, notes."""
    line = line.strip()
    if line.startswith("- [ ] "):
        rest = line[6:]
        parts = rest.split(" — ", 1)
        return {"text": parts[0].strip(), "done": False, "notes": parts[1].strip() if len(parts) > 1 else ""}
    elif line.startswith("- [x] "):
        rest = line[6:]
        parts = rest.split(" — ", 1)
        return {"text": parts[0].strip(), "done": True, "notes": parts[1].strip() if len(parts) > 1 else ""}
    return None


def get_todos(date: str) -> list[dict]:
    """Return list of {text, done, notes} parsed from the day's todo file."""
    filepath = _todo_file(date)
    if not filepath.exists():
        return []
    todos = []
    for line in filepath.read_text(encoding="utf-8").splitlines():
        parsed = _parse_todo_line(line)
        if parsed:
            todos.append(parsed)
    return todos


def add_todo(date: str, text: str):
    """Append a new unchecked todo to the day's file (creates it if needed)."""
    filepath = _create_file_if_needed(date)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"- [ ] {text}\n")
    logger.info(f"Added todo to {filepath}: {text}")


def complete_todo(date: str, todo_text: str, notes: str = ""):
    """Mark the first matching unchecked todo as done, optionally appending notes."""
    filepath = _todo_file(date)
    if not filepath.exists():
        logger.warning(f"Todo file not found for {date}")
        return

    lines = filepath.read_text(encoding="utf-8").splitlines(keepends=True)
    updated = False
    new_lines = []
    for line in lines:
        if not updated and line.strip().startswith("- [ ] ") and todo_text.lower() in line.lower():
            rest = line.strip()[6:]
            # Strip existing notes if any
            base_text = rest.split(" — ", 1)[0].strip()
            new_line = f"- [x] {base_text}"
            if notes:
                new_line += f" — {notes}"
            new_lines.append(new_line + "\n")
            updated = True
        else:
            new_lines.append(line)

    if updated:
        filepath.write_text("".join(new_lines), encoding="utf-8")
        logger.info(f"Completed todo in {filepath}: {todo_text}")
    else:
        logger.warning(f"No matching unchecked todo found for '{todo_text}' on {date}")


def postpone_todo(from_date: str, to_date: str, todo_text: str):
    """Remove the matching todo from from_date and add it unchecked to to_date."""
    from_file = _todo_file(from_date)
    if not from_file.exists():
        logger.warning(f"Todo file not found for {from_date}")
        return

    lines = from_file.read_text(encoding="utf-8").splitlines(keepends=True)
    removed = False
    new_lines = []
    removed_text = None
    for line in lines:
        if not removed and line.strip().startswith("- [ ] ") and todo_text.lower() in line.lower():
            removed_text = line.strip()[6:].split(" — ", 1)[0].strip()
            removed = True
        else:
            new_lines.append(line)

    if removed and removed_text:
        from_file.write_text("".join(new_lines), encoding="utf-8")
        add_todo(to_date, removed_text)
        logger.info(f"Postponed todo '{removed_text}' from {from_date} to {to_date}")
    else:
        logger.warning(f"No matching unchecked todo found for '{todo_text}' on {from_date}")


def rollover_uncompleted(from_date: str, to_date: str) -> list[str]:
    """
    Move all unchecked todos from from_date to to_date.
    Returns list of rolled-over todo texts.
    """
    from_file = _todo_file(from_date)
    if not from_file.exists():
        return []

    lines = from_file.read_text(encoding="utf-8").splitlines(keepends=True)
    keep_lines = []
    rolled = []
    for line in lines:
        if line.strip().startswith("- [ ] "):
            text = line.strip()[6:].split(" — ", 1)[0].strip()
            rolled.append(text)
        else:
            keep_lines.append(line)

    if rolled:
        from_file.write_text("".join(keep_lines), encoding="utf-8")
        for text in rolled:
            add_todo(to_date, text)
        logger.info(f"Rolled over {len(rolled)} todos from {from_date} to {to_date}")

    return rolled


def format_todo_list(date: str) -> str:
    """Return a human-readable string of todos for Telegram."""
    todos = get_todos(date)
    if not todos:
        return f"No todos for {date}."

    open_todos = [t for t in todos if not t["done"]]
    done_todos = [t for t in todos if t["done"]]

    lines = [f"Todos for {date}:"]
    if open_todos:
        lines.append("\nOpen:")
        for t in open_todos:
            lines.append(f"  • {t['text']}")
    if done_todos:
        lines.append("\nDone:")
        for t in done_todos:
            entry = f"  ✓ {t['text']}"
            if t["notes"]:
                entry += f" — {t['notes']}"
            lines.append(entry)

    return "\n".join(lines)
