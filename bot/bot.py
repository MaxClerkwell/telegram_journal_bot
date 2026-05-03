"""
Telegram journal bot — main entry point.

Conversation flow:
  IDLE → receives a thought → QUESTIONING (asks 1-3 follow-up questions) → WRITING → IDLE

Scheduled tasks:
  - Daily 23:30: compile today's journal into a memoir LaTeX chapter
  - Saturday 18:00: send weekly summary via Telegram
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import opencode_client as llm
import repo_writer as repo
import todo_writer as todos

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])
REPO_PATH = Path(os.environ.get("REPO_PATH", "/repo"))

# ---------------------------------------------------------------------------
# Conversation state (in-memory, single user)
# ---------------------------------------------------------------------------

# States
IDLE = "IDLE"
QUESTIONING = "QUESTIONING"
WRITING = "WRITING"
TODO_COMPLETE_FOLLOWUP = "TODO_COMPLETE_FOLLOWUP"
TODO_PICK = "TODO_PICK"
CHATTING = "CHATTING"

state = {
    "status": IDLE,
    "thought": "",
    "questions": [],
    "answers": [],
    "current_q": 0,
    "attachment": None,
    "todo_complete_text": "",
    "todo_complete_date": "",
    "chat_history": [],  # list of {"role": "user"|"assistant", "text": str}
    "todo_pick_list": [],  # open todos shown to user for selection
}


def reset_state():
    state.update({
        "status": IDLE,
        "thought": "",
        "questions": [],
        "answers": [],
        "current_q": 0,
        "attachment": None,
        "todo_complete_text": "",
        "todo_complete_date": "",
        "chat_history": [],
        "todo_pick_list": [],
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


async def reply(update: Update, text: str):
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return  # Ignore silently

    text = update.message.text.strip()

    if state["status"] == TODO_COMPLETE_FOLLOWUP:
        await finish_todo_complete(update, context, notes=text)
        return

    if state["status"] == TODO_PICK:
        await handle_todo_pick(update, context, text)
        return

    if state["status"] == CHATTING:
        await handle_chat(update, context, text)
        return

    if state["status"] == IDLE:
        # Classify message before starting journal flow
        date = datetime.now().strftime("%Y-%m-%d")
        open_todo_texts = [t["text"] for t in todos.get_todos(date) if not t["done"]]
        try:
            classification = llm.classify_message(text, open_todo_texts, today=date)
        except Exception as e:
            logger.error(f"classify_message error: {e}")
            classification = {"type": "journal", "data": {}}

        msg_type = classification["type"]
        data = classification.get("data", {})

        if msg_type == "todo_add":
            todo_text = data.get("todo", text)
            target_date = data.get("date", date)
            todos.add_todo(target_date, todo_text)
            try:
                repo.commit_and_push(f"todo: add '{todo_text}' for {target_date}")
            except Exception as e:
                logger.error(f"todo push failed: {e}")
            label = "today" if target_date == date else target_date
            await reply(update, f"Added for {label}: {todo_text}")
            return

        elif msg_type == "todo_complete":
            matched = data.get("todo", "")
            notes = data.get("notes", "")
            if matched:
                todos.complete_todo(date, matched, notes)
                # Ask for follow-up note
                state["status"] = TODO_COMPLETE_FOLLOWUP
                state["todo_complete_text"] = matched
                state["todo_complete_date"] = date
                await reply(update, "Would you like to add a note? (or 'no' to skip)")
            else:
                await reply(update, "I could not find a matching todo.")
            return

        elif msg_type == "todo_postpone":
            matched = data.get("todo", "")
            if matched:
                from datetime import timedelta
                today_dt = datetime.now().date()
                target_date = data.get("date") or (today_dt + timedelta(days=1)).isoformat()
                todos.postpone_todo(date, target_date, matched)
                await reply(update, f"Postponed to {target_date}: {matched}")
            else:
                await reply(update, "I could not find a matching todo.")
            return

        # Default: journal flow
        await start_questioning(update, context, text)

    elif state["status"] == QUESTIONING:
        await collect_answer(update, context, text)

    elif state["status"] == WRITING:
        await reply(update, "Still writing your previous entry, please wait a moment...")


async def finish_todo_complete(update: Update, context: ContextTypes.DEFAULT_TYPE, notes: str):
    """Handle the follow-up note after completing a todo."""
    todo_text = state["todo_complete_text"]
    date = state["todo_complete_date"]
    reset_state()

    skip_words = {"nein", "no", "skip", "nope", "-"}
    if notes.lower().strip() in skip_words or not notes.strip():
        await reply(update, f"Done: {todo_text}")
        return

    # Update the completed todo with the note (already marked done, just update notes)
    todos.complete_todo(date, todo_text, notes)

    # Append a short line to the journal
    journal_note = f"Done [{todo_text}]: {notes}"
    try:
        repo.append_journal(date, journal_note)
        repo.commit_and_push(f"todo: completed '{todo_text}'")
    except Exception as e:
        logger.error(f"finish_todo_complete journal append failed: {e}")

    await reply(update, f"Done: {todo_text}\nNote saved.")


async def start_questioning(update: Update, context: ContextTypes.DEFAULT_TYPE, thought: str):
    state["thought"] = thought
    state["status"] = QUESTIONING
    state["questions"] = []
    state["answers"] = []

    await reply(update, "...")

    recent_context = repo.load_recent_context(n_days=7)
    question = llm.next_question(thought, [], context=recent_context)
    if not question:
        await write_entry(update, context)
        return

    state["questions"].append(question)
    await reply(update, question)


async def collect_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, answer: str):
    state["answers"].append(answer)

    qa_so_far = [
        {"question": q, "answer": a}
        for q, a in zip(state["questions"], state["answers"])
    ]

    await reply(update, "...")

    recent_context = repo.load_recent_context(n_days=7)
    question = llm.next_question(state["thought"], qa_so_far, context=recent_context)
    if question:
        state["questions"].append(question)
        await reply(update, question)
    else:
        await write_entry(update, context)


async def write_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state["status"] = WRITING
    await reply(update, "Writing your journal entry...")

    thought = state["thought"]
    attachment = state.get("attachment")
    qa_pairs = [
        {"question": q, "answer": a}
        for q, a in zip(state["questions"], state["answers"])
    ]
    date = datetime.now().strftime("%Y-%m-%d")

    # Enrich thought with attachment reference for LLM context
    thought_with_context = thought
    if attachment:
        thought_with_context = f"{thought}\n\n[Attached image: {attachment}]"

    try:
        # Generate journal content
        journal_content = llm.write_journal_entry(date, thought_with_context, qa_pairs)
        repo.append_journal(date, journal_content)

        # Generate topic entry
        topic_data = llm.write_topic_entry(thought_with_context, qa_pairs)
        repo.upsert_topic(topic_data["slug"], topic_data["title"], topic_data["content"])

        # Commit and push (includes the already-saved attachment)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        repo.commit_and_push(f"journal: {timestamp}")

        await reply(
            update,
            f"Entry saved and pushed!\n\nTopic: *{topic_data['title']}*",
        )

    except Exception as e:
        logger.error(f"Failed to write entry: {e}")
        await reply(update, f"Something went wrong writing the entry: {e}")

    finally:
        reset_state()


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await reply(update, f"Bot is running. State: {state['status']}")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    reset_state()
    await reply(update, "Cancelled. Send me a new thought whenever you're ready.")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all open todos across the next 14 days, numbered for selection."""
    if not is_allowed(update):
        return
    from datetime import timedelta
    today = datetime.now().date()
    all_open = []  # list of (date_str, todo_dict)

    for i in range(14):
        d = (today + timedelta(days=i)).isoformat()
        for t in todos.get_todos(d):
            if not t["done"]:
                all_open.append((d, t))

    if not all_open:
        await reply(update, "No open todos in the next 14 days.")
        return

    # Group by date for display
    lines = []
    current_date = None
    for i, (d, t) in enumerate(all_open):
        if d != current_date:
            label = "Today" if d == today.isoformat() else d
            lines.append(f"\n{label}:")
            current_date = d
        lines.append(f"  {i+1}. {t['text']}")

    state["status"] = TODO_PICK
    state["todo_pick_list"] = all_open
    state["todo_complete_date"] = today.isoformat()
    await reply(update, f"Which todo is done?\n{''.join(lines)}\n\nEnter a number (0 = cancel):")


async def handle_todo_pick(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle the number the user picked, then ask for a note."""
    pick_list = state["todo_pick_list"]
    try:
        idx = int(text.strip()) - 1
    except ValueError:
        await reply(update, f"Please enter a number between 0 and {len(pick_list)} (0 = cancel).")
        return
    if idx == -1:
        reset_state()
        await reply(update, "Cancelled.")
        return
    if not 0 <= idx < len(pick_list):
        await reply(update, f"Please enter a number between 0 and {len(pick_list)} (0 = cancel).")
        return

    date, todo = pick_list[idx]
    picked = todo["text"]
    todos.complete_todo(date, picked)
    state["status"] = TODO_COMPLETE_FOLLOWUP
    state["todo_complete_text"] = picked
    state["todo_complete_date"] = date
    state["todo_pick_list"] = []
    await reply(update, f"✓ {picked}\n\nWould you like to add a note? (or 'no')")


async def handle_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle messages while in CHATTING state."""
    if text == "/:q!":
        reset_state()
        await reply(update, "Chat ended.")
        return

    if text == "/:wq":
        history = state["chat_history"]
        reset_state()
        if not history:
            await reply(update, "Nothing to save.")
            return
        await reply(update, "Creating summary...")
        try:
            date = datetime.now().strftime("%Y-%m-%d")
            summary = llm.summarize_chat_for_journal(history)
            repo.append_journal(date, summary)
            repo.commit_and_push(f"journal: chat summary {date}")
            await reply(update, f"Saved:\n\n{summary}")
        except Exception as e:
            logger.error(f"handle_chat save failed: {e}")
            await reply(update, f"Error saving: {e}")
        return

    # Normal chat turn
    state["chat_history"].append({"role": "user", "text": text})
    try:
        response = llm.chat_reply(state["chat_history"])
        state["chat_history"].append({"role": "assistant", "text": response})
        await reply(update, response)
    except Exception as e:
        logger.error(f"chat_reply failed: {e}")
        await reply(update, f"Error: {e}")


async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enter freeform chat mode. Use /:wq to save to journal, /:q! to exit without saving."""
    if not is_allowed(update):
        return
    reset_state()
    state["status"] = CHATTING
    await reply(update, "Chat mode. /:wq to save + exit, /:q! to exit without saving.")


async def cmd_clean(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove all tracked files from the repo and commit a clean slate."""
    if not is_allowed(update):
        return
    await reply(update, "Cleaning repo...")
    try:
        result = subprocess.run(
            ["git", "rm", "-rf", "--ignore-unmatch", "."],
            cwd=str(REPO_PATH), capture_output=True, text=True,
        )
        removed = [l for l in result.stdout.splitlines() if l.startswith("rm ")]
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"clean: remove {len(removed)} files"],
            cwd=str(REPO_PATH), capture_output=True, text=True,
        )
        push = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(REPO_PATH), capture_output=True, text=True,
            env={**os.environ},
        )
        if push.returncode != 0:
            await reply(update, f"Committed locally but push failed:\n{push.stderr}")
        else:
            await reply(update, f"Done. {len(removed)} files removed and pushed.")
    except Exception as e:
        logger.error(f"cmd_clean failed: {e}")
        await reply(update, f"Error: {e}")


async def cmd_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Compile memoir/main.tex to PDF. Generates missing chapters first."""
    if not is_allowed(update):
        return

    main_tex = REPO_PATH / "memoir" / "main.tex"

    # If no main.tex yet, compile all existing journal days first
    if not main_tex.exists():
        journal_dir = REPO_PATH / "journal"
        journal_files = sorted(journal_dir.glob("*.md")) if journal_dir.exists() else []
        if not journal_files:
            await reply(update, "No journal entries found yet. Write something first.")
            return
        await reply(update, f"No memoir yet — compiling {len(journal_files)} journal day(s) first...")
        for jf in journal_files:
            date = jf.stem
            images = _find_attachments_for_date(date)
            try:
                content = jf.read_text(encoding="utf-8")
                latex_chapter = llm.compile_memoir_chapter(date, content, images)
                repo.append_memoir_chapter(date, latex_chapter)
            except Exception as e:
                logger.error(f"Auto-compile chapter {date} failed: {e}")
        try:
            repo.commit_and_push("memoir: auto-compile all chapters for /pdf")
        except Exception:
            pass

    await reply(update, "Compiling PDF...")
    await compile_and_send_pdf(update.get_bot())


async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save photo/document, then ask Gemini follow-up questions about it (images only)."""
    if not is_allowed(update):
        return

    date = datetime.now().strftime("%Y-%m-%d")
    attachments_dir = REPO_PATH / "attachments" / date
    attachments_dir.mkdir(parents=True, exist_ok=True)

    file_obj = None
    filename = None
    is_image = False

    if update.message.photo:
        photo = update.message.photo[-1]
        file_obj = await context.bot.get_file(photo.file_id)
        filename = f"{photo.file_unique_id}.jpg"
        is_image = True
    elif update.message.document:
        doc = update.message.document
        file_obj = await context.bot.get_file(doc.file_id)
        filename = doc.file_name or f"{doc.file_unique_id}.bin"
        is_image = doc.mime_type and doc.mime_type.startswith("image/")

    if not file_obj or not filename:
        return

    dest = attachments_dir / filename
    await file_obj.download_to_drive(str(dest))

    caption = update.message.caption or ""

    # For images: ask Gemini follow-up questions, then run journal flow
    if is_image and llm.PROVIDER == "google":
        await reply(update, "Let me take a look at that...")
        try:
            questions = llm.get_client().ask_questions_about_image(str(dest))
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            questions = ["What does this image mean to you?"]

        # Use caption as the initial thought, or a placeholder
        thought = caption if caption else f"[Image: {filename}]"
        state["thought"] = thought
        state["status"] = QUESTIONING
        state["questions"] = questions
        state["answers"] = []
        state["current_q"] = 0
        # Store the image path so it's referenced in the journal entry
        state["attachment"] = f"attachments/{date}/{filename}"

        await reply(update, questions[0])
    else:
        # Non-image file: just save and commit
        try:
            repo.commit_and_push(f"attachment: {date}/{filename}")
            await reply(update, f"Saved to attachments/{date}/{filename}" + (f" — _{caption}_" if caption else ""))
        except Exception as e:
            logger.error(f"handle_attachment commit failed: {e}")
            await reply(update, f"Saved locally but push failed: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe a voice message via Gemini and feed it into the journal flow."""
    if not is_allowed(update):
        return
    if llm.PROVIDER != "google":
        await reply(update, "Voice messages are only supported with the Google provider.")
        return

    await reply(update, "Transcribing your voice message...")
    try:
        voice = update.message.voice
        file_obj = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await file_obj.download_to_drive(tmp_path)

        transcript = llm.get_client().transcribe_audio(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        await reply(update, f"Understood: _{transcript}_\n\nProcessing as a journal entry...")
        await start_questioning(update, context, transcript)

    except Exception as e:
        logger.error(f"handle_voice failed: {e}")
        await reply(update, f"Could not transcribe voice message: {e}")


async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/todo <text> — add a todo for today."""
    if not is_allowed(update):
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await reply(update, "Usage: /todo <text>")
        return
    date = datetime.now().strftime("%Y-%m-%d")
    todos.add_todo(date, text)
    await reply(update, f"Added: {text}")


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/todos [tomorrow] — show today's or tomorrow's todo list."""
    if not is_allowed(update):
        return
    from datetime import timedelta
    if context.args and context.args[0].lower() == "tomorrow":
        date = (datetime.now().date() + timedelta(days=1)).isoformat()
    else:
        date = datetime.now().strftime("%Y-%m-%d")
    await reply(update, todos.format_todo_list(date))


async def cmd_memoir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger memoir compilation for today (or a given date: /memoir 2026-05-03)."""
    if not is_allowed(update):
        return

    args = context.args
    if args:
        date = args[0]
    else:
        date = datetime.now().strftime("%Y-%m-%d")

    journal_file = REPO_PATH / "journal" / f"{date}.md"
    if not journal_file.exists():
        await reply(update, f"No journal entries found for {date}.")
        return

    images = _find_attachments_for_date(date)
    await reply(update, f"Compiling memoir chapter for {date} ({len(images)} images)...")
    try:
        journal_content = journal_file.read_text(encoding="utf-8")
        latex_chapter = llm.compile_memoir_chapter(date, journal_content, images)
        repo.append_memoir_chapter(date, latex_chapter)
        repo.commit_and_push(f"memoir: add chapter {date}")
        await reply(update, f"Done. Chapter for {date} added to memoir/memoir.tex and pushed.")
    except Exception as e:
        logger.error(f"cmd_memoir failed: {e}")
        await reply(update, f"Error: {e}")


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------

def _find_attachments_for_date(date: str) -> list[str]:
    """Return image paths relative to memoir/ for a given date."""
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".pdf"}
    attachments_dir = REPO_PATH / "attachments" / date
    if not attachments_dir.exists():
        return []
    return [
        f"../attachments/{date}/{f.name}"
        for f in sorted(attachments_dir.iterdir())
        if f.suffix.lower() in image_extensions
    ]


async def compile_memoir(app: Application = None):
    """Daily 23:30 — compile today's journal into a memoir chapter."""
    date = datetime.now().strftime("%Y-%m-%d")
    journal_file = REPO_PATH / "journal" / f"{date}.md"

    if not journal_file.exists():
        logger.info(f"No journal file for {date}, skipping memoir compilation.")
        return

    try:
        journal_content = journal_file.read_text(encoding="utf-8")
        images = _find_attachments_for_date(date)
        latex_chapter = llm.compile_memoir_chapter(date, journal_content, images)
        repo.append_memoir_chapter(date, latex_chapter)
        context_summary = llm.build_context_summary(date, journal_content)
        repo.save_context_summary(date, context_summary)
        repo.commit_and_push(f"memoir: add chapter {date} + context summary")
        logger.info(f"Memoir chapter compiled for {date} ({len(images)} images)")
    except Exception as e:
        logger.error(f"compile_memoir failed: {e}")


async def send_weekly_summary(app: Application):
    """Saturday 18:00 — send weekly summary text + compiled PDF via Telegram."""
    from datetime import timedelta

    today = datetime.now().date()
    contents = []

    for i in range(7):
        day = today - timedelta(days=i)
        path = REPO_PATH / "journal" / f"{day.isoformat()}.md"
        if path.exists():
            contents.append(f"=== {day.isoformat()} ===\n{path.read_text(encoding='utf-8')}")

    if not contents:
        logger.info("No journal entries found for weekly summary.")
        return

    combined = "\n\n".join(contents)

    try:
        summary = llm.summarize_week(combined)
        await app.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=summary)
        logger.info("Weekly summary sent.")
    except Exception as e:
        logger.error(f"send_weekly_summary text failed: {e}")

    # Also compile and send the PDF
    await compile_and_send_pdf(app.bot)


async def compile_and_send_pdf(bot):
    """Compile memoir/main.tex to PDF and send it via Telegram."""
    main_tex = REPO_PATH / "memoir" / "main.tex"
    if not main_tex.exists():
        logger.info("No main.tex found, skipping PDF compilation.")
        return

    memoir_dir = REPO_PATH / "memoir"
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            pdflatex_cmd = [
                "pdflatex", "-interaction=nonstopmode",
                "-output-directory", tmpdir,
                "main.tex",
            ]
            result = subprocess.run(
                pdflatex_cmd, cwd=str(memoir_dir),
                capture_output=True, text=True, timeout=120,
            )
            # Run twice for TOC
            subprocess.run(
                pdflatex_cmd, cwd=str(memoir_dir),
                capture_output=True, text=True, timeout=120,
            )
            pdf_path = Path(tmpdir) / "main.pdf"
            if pdf_path.exists():
                with open(pdf_path, "rb") as f:
                    await bot.send_document(
                        chat_id=ALLOWED_CHAT_ID,
                        document=f,
                        filename=f"memoir-{datetime.now().strftime('%Y-%m-%d')}.pdf",
                        caption="Your memoir — compiled from this week's journal entries.",
                    )
                logger.info("PDF sent via Telegram.")
            else:
                logger.error(f"pdflatex ran but no PDF found. Output:\n{result.stdout}\n{result.stderr}")
        except Exception as e:
            logger.error(f"PDF compilation failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def build_weekly_context(app: Application = None):
    """Every Sunday — build a weekly context summary from this week's daily summaries."""
    from datetime import date as date_cls
    today = date_cls.today()
    year, week, _ = today.isocalendar()
    try:
        daily_content = repo.load_daily_context_for_week(year, week)
        if not daily_content:
            logger.info(f"No daily context for week {year}-W{week:02d}, skipping weekly summary.")
            return
        summary = llm.build_weekly_summary(daily_content)
        repo.save_weekly_context(year, week, summary)
        logger.info(f"Weekly context summary saved for {year}-W{week:02d}")
    except Exception as e:
        logger.error(f"build_weekly_context failed: {e}")


async def build_monthly_context(app: Application = None):
    """1st of every month — build a monthly context summary from weekly summaries."""
    from datetime import date as date_cls
    today = date_cls.today()
    # Summarize the previous month
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1
    try:
        weekly_content = repo.load_weekly_context_for_month(year, month)
        if not weekly_content:
            logger.info(f"No weekly context for {year}-{month:02d}, skipping monthly summary.")
            return
        summary = llm.build_monthly_summary(weekly_content)
        repo.save_monthly_context(year, month, summary)
        logger.info(f"Monthly context summary saved for {year}-{month:02d}")
    except Exception as e:
        logger.error(f"build_monthly_context failed: {e}")


async def build_yearly_context(app: Application = None):
    """January 1st — build a yearly context summary from monthly summaries."""
    from datetime import date as date_cls
    last_year = date_cls.today().year - 1
    try:
        monthly_content = repo.load_monthly_context_for_year(last_year)
        if not monthly_content:
            logger.info(f"No monthly context for {last_year}, skipping yearly summary.")
            return
        summary = llm.build_yearly_summary(monthly_content)
        repo.save_yearly_context(last_year, summary)
        logger.info(f"Yearly context summary saved for {last_year}")
    except Exception as e:
        logger.error(f"build_yearly_context failed: {e}")


async def rollover_todos(app: Application = None):
    """Daily 22:00 — move unchecked todos from today to tomorrow, then commit+push."""
    from datetime import timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()
    try:
        rolled = todos.rollover_uncompleted(today, tomorrow)
        if rolled:
            logger.info(f"Rolled over {len(rolled)} todos from {today} to {tomorrow}: {rolled}")
            repo.commit_and_push(f"todos: rollover {today} -> {tomorrow} ({len(rolled)} items)")
        else:
            logger.info(f"No unchecked todos to roll over from {today}.")
    except Exception as e:
        logger.error(f"rollover_todos failed: {e}")


async def post_init(app):
    # Log available Gemini models so the user can pick a working one
    provider = os.environ.get("LLM_PROVIDER", "google").lower()
    if provider == "google":
        try:
            from google import genai
            client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
            names = [m.name for m in client.models.list()]
            logger.info(f"Available Gemini models: {names[:10]}")
        except Exception as e:
            logger.warning(f"Could not list models: {e}")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        compile_memoir,
        CronTrigger(hour=23, minute=30),
        id="memoir",
        kwargs={"app": app},
    )
    scheduler.add_job(
        send_weekly_summary,
        CronTrigger(day_of_week="sat", hour=18, minute=15),
        id="weekly_summary",
        kwargs={"app": app},
    )
    scheduler.add_job(
        rollover_todos,
        CronTrigger(hour=22, minute=0),
        id="rollover_todos",
        kwargs={"app": app},
    )
    scheduler.add_job(
        build_weekly_context,
        CronTrigger(day_of_week="sun", hour=23, minute=45),
        id="weekly_context",
        kwargs={"app": app},
    )
    scheduler.add_job(
        build_monthly_context,
        CronTrigger(day=1, hour=0, minute=15),
        id="monthly_context",
        kwargs={"app": app},
    )
    scheduler.add_job(
        build_yearly_context,
        CronTrigger(month=1, day=1, hour=0, minute=30),
        id="yearly_context",
        kwargs={"app": app},
    )
    scheduler.start()
    logger.info("Scheduler started (memoir: 23:30, summary+PDF: Sat 18:15, rollover: 22:00, weekly: Sun 23:45, monthly: 1st 00:15, yearly: Jan 1st 00:30)")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("clean", cmd_clean))
    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("memoir", cmd_memoir))
    app.add_handler(CommandHandler("pdf", cmd_pdf))
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("todos", cmd_todos))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_attachment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
