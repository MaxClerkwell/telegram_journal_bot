"""
LLM client for the journal bot.
Calls provider APIs directly (Google Gemini, Anthropic Claude, or OpenAI).
OpenCode TUI is available in the container for manual use but not used here.
"""

import os
import json
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROVIDER = os.environ.get("LLM_PROVIDER", "google").lower()
MODEL = os.environ.get("LLM_MODEL", "gemini-2.5-flash")


class LLMClient:
    """Unified LLM client supporting Google, Anthropic, and OpenAI."""

    def __init__(self):
        self.provider = PROVIDER
        self.model = MODEL
        self._client = None
        self._setup()

    def _setup(self):
        if self.provider == "google":
            from google import genai
            self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        elif self.provider == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        elif self.provider == "openai":
            from openai import OpenAI
            self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        else:
            raise ValueError(f"Unsupported LLM_PROVIDER: {self.provider}")

    def ask_questions_about_image(self, image_path: str) -> list[str]:
        """Look at an image and return 1-3 follow-up questions about it."""
        if self.provider != "google":
            raise ValueError("Image analysis is only supported with the Google provider.")
        from google import genai
        from google.genai import types as genai_types

        suffix = Path(image_path).suffix.lower()
        mime = {"jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(suffix, "image/jpeg")

        with open(image_path, "rb") as f:
            image_data = f.read()

        response = self._client.models.generate_content(
            model=self.model,
            contents=[
                genai_types.Part.from_bytes(data=image_data, mime_type=mime),
                (
                    "You are a thoughtful journaling assistant. "
                    "Look at this image and generate 1 to 3 short, open-ended questions "
                    "that help the user reflect on what they captured and why. "
                    "Return ONLY a JSON array of question strings, e.g.: [\"What drew your attention here?\"]"
                ),
            ],
        )
        return _extract_json_array(response.text.strip())

    def transcribe_audio(self, audio_path: str) -> str:
        """Transcribe and interpret an audio file using Gemini's multimodal API.
        Returns the interpreted text content of the voice message.
        Only supported for Google provider.
        """
        if self.provider != "google":
            raise ValueError("Audio transcription is only supported with the Google provider.")
        from google import genai
        from google.genai import types as genai_types

        with open(audio_path, "rb") as f:
            audio_data = f.read()

        response = self._client.models.generate_content(
            model=self.model,
            contents=[
                genai_types.Part.from_bytes(data=audio_data, mime_type="audio/ogg"),
                "Transcribe this voice message accurately. Return only the spoken text, nothing else.",
            ],
        )
        return response.text.strip()

    def complete(self, prompt: str, system: str = "") -> str:
        """Send a prompt and return the text response."""
        try:
            if self.provider == "google":
                full_prompt = f"{system}\n\n{prompt}" if system else prompt
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=full_prompt,
                )
                return response.text.strip()

            elif self.provider == "anthropic":
                messages = [{"role": "user", "content": prompt}]
                kwargs = {"model": self.model, "max_tokens": 2048, "messages": messages}
                if system:
                    kwargs["system"] = system
                response = self._client.messages.create(**kwargs)
                return response.content[0].text.strip()

            elif self.provider == "openai":
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=2048,
                )
                return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"LLM completion error ({self.provider}): {e}")
            raise


# Module-level singleton
_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def _extract_json_array(text: str) -> list:
    """Extract a JSON array from LLM output, even if wrapped in markdown."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find a JSON array in the text
    match = re.search(r'\[.*?\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # Fallback: split numbered lines
    lines = [l.strip().lstrip('0123456789.-) ').strip('"\'') for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if l and not l.startswith('[') and not l.startswith('{')]
    return lines[:3] if lines else ["Can you tell me more about this?"]


def chat_reply(history: list[dict]) -> str:
    """Continue a freeform chat. history is list of {"role": "user"|"assistant", "text": str}."""
    system = "You are a helpful, direct assistant in a personal chat. Be concise and conversational."
    formatted = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}"
        for m in history
    )
    prompt = f"{formatted}\nAssistant:"
    return get_client().complete(prompt, system=system)


def summarize_chat_for_journal(history: list[dict]) -> str:
    """Summarize a chat conversation into a concise journal entry."""
    system = "You are a journaling assistant. Summarize a conversation into a factual, first-person journal note."
    formatted = "\n".join(
        f"{'Ich' if m['role'] == 'user' else 'Bot'}: {m['text']}"
        for m in history
    )
    prompt = (
        f"Conversation:\n{formatted}\n\n"
        "Write a concise journal note (3-8 sentences) summarizing what was discussed, "
        "decided, or reflected upon. First-person, factual, no filler."
    )
    return get_client().complete(prompt, system=system)


def build_context_summary(date: str, journal_content: str) -> str:
    """Build a compact context summary of a day's journal for future use."""
    system = "You are a journaling assistant building a compact memory of the day."
    prompt = (
        f"Date: {date}\n\nJournal entries:\n{journal_content}\n\n"
        "Write a compact summary (5-10 bullet points) of the key thoughts, decisions, themes, "
        "and open questions from this day. Be factual and dense — this will be used as background "
        "context for future conversations. No prose, bullets only."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"build_context_summary failed: {e}")
        return journal_content[:500]


def next_question(thought: str, qa_so_far: list[dict], context: str = "") -> str | None:
    """Generate the next follow-up question given the thought and answers so far.
    Returns a question string, or None if enough has been covered (max 3 rounds).
    qa_so_far: list of {"question": str, "answer": str}
    """
    if len(qa_so_far) >= 3:
        return None

    history = "\n".join(
        f"Q: {p['question']}\nA: {p['answer']}" for p in qa_so_far
    )
    system = (
        "You are a precise journaling assistant conducting a short back-and-forth reflection. "
        "Read the original thought and all previous answers carefully before deciding what to ask next. "
        "If the thought is technical or project-related, ask concrete questions about details, decisions, or next steps. "
        "If it is emotional or personal, ask questions that deepen self-understanding. "
        "Only ask what is still genuinely unclear or worth exploring — do not repeat covered ground."
    )
    context_block = f"Background context from recent days:\n{context}\n\n" if context else ""
    prompt = (
        f"{context_block}"
        f"Original thought:\n\"{thought}\"\n\n"
        + (f"Conversation so far:\n{history}\n\n" if qa_so_far else "")
        + "Decide: is there one more specific, worthwhile question to ask? "
        "If yes, return it as a plain string. "
        "If the thought is already sufficiently explored, return exactly: DONE"
    )
    try:
        response = get_client().complete(prompt, system=system).strip()
        if response.upper() == "DONE" or not response:
            return None
        # Strip quotes if model wrapped the question
        return response.strip('"\'')
    except Exception as e:
        logger.error(f"next_question failed: {e}")
        return None


def write_journal_entry(date: str, thought: str, qa_pairs: list[dict]) -> str:
    """
    Generate markdown content for a journal entry.
    qa_pairs: list of {"question": str, "answer": str}
    Returns markdown string (without YAML frontmatter — that's added by repo_writer).
    """
    qa_text = "\n".join(
        f"**Q: {p['question']}**\nA: {p['answer']}" for p in qa_pairs
    )
    system = "You are a skilled journaling assistant who writes thoughtful, well-structured journal entries."
    prompt = (
        f"Date: {date}\n\n"
        f"Initial thought:\n{thought}\n\n"
        f"Follow-up Q&A:\n{qa_text}\n\n"
        "Write a single, cohesive journal entry that integrates the initial thought and the Q&A. "
        "Use markdown. Be reflective, first-person, and concise (150-300 words). "
        "Do NOT include a YAML frontmatter block. Start directly with the content."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"write_journal_entry failed: {e}")
        # Fallback: simple concatenation
        lines = [f"## Thought\n\n{thought}"]
        for p in qa_pairs:
            lines.append(f"\n**{p['question']}**\n\n{p['answer']}")
        return "\n\n".join(lines)


def write_topic_entry(thought: str, qa_pairs: list[dict]) -> dict:
    """
    Extract a topic from the thought and generate an Obsidian-compatible topic file.
    Returns {"slug": str, "title": str, "content": str}
    """
    qa_text = "\n".join(
        f"Q: {p['question']}\nA: {p['answer']}" for p in qa_pairs
    )
    system = "You are a knowledge management assistant helping organize personal notes into an Obsidian vault."
    prompt = (
        f"Journal entry:\n{thought}\n\nFollow-up Q&A:\n{qa_text}\n\n"
        "Identify the main topic of this entry. Then return a JSON object with:\n"
        "- \"slug\": a lowercase kebab-case identifier (e.g. \"deep-work\", \"anxiety-management\")\n"
        "- \"title\": a human-readable title\n"
        "- \"content\": a concise markdown summary (2-4 sentences) suitable for an Obsidian note. "
        "Include [[wiki-links]] to related concepts where natural.\n\n"
        "Return ONLY the JSON object."
    )
    try:
        response = get_client().complete(prompt, system=system)
        # Try to parse JSON
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON object found in response")
        return {
            "slug": str(data.get("slug", "untitled")).lower().replace(" ", "-"),
            "title": str(data.get("title", "Untitled")),
            "content": str(data.get("content", thought[:200])),
        }
    except Exception as e:
        logger.error(f"write_topic_entry failed: {e}")
        slug = re.sub(r'[^a-z0-9]+', '-', thought[:40].lower()).strip('-')
        return {"slug": slug or "untitled", "title": thought[:60], "content": thought}


def compile_memoir_chapter(date: str, journal_content: str, image_filenames: list[str] = None) -> str:
    """Generate a LaTeX chapter for the given date's journal content.
    image_filenames: list of relative paths from the memoir/ dir, e.g. ['../attachments/2026-05-03/photo.jpg']
    """
    image_filenames = image_filenames or []

    image_instructions = ""
    if image_filenames:
        image_list = "\n".join(f"  - {f}" for f in image_filenames)
        image_instructions = (
            f"\n\nThe following image files are available for this day:\n{image_list}\n"
            "Embed them naturally in the chapter using \\includegraphics[width=0.8\\textwidth]{path} "
            "inside a figure environment with a short \\caption describing where it fits in the narrative. "
            "Place each image at the most fitting point in the text."
        )

    system = (
        "You are an assistant that structures personal journal entries into clear, factual memoir chapters in LaTeX. "
        "Write in a sober, direct style — no flowery language, no dramatic phrasing, no metaphors. "
        "Report what happened, what was thought, what was decided. Clarity over style."
    )
    prompt = (
        f"Date: {date}\n\n"
        f"Journal entries for this day:\n\n{journal_content}\n"
        f"{image_instructions}\n"
        f"Write a memoir chapter in LaTeX. Use \\chapter{{{date}}} as the chapter heading. "
        "Structure the content clearly — use short paragraphs, one idea per paragraph. "
        "First-person. Factual and concise. No embellishment. 150-300 words. "
        "Return ONLY valid LaTeX (no \\documentclass or preamble — just \\chapter{{...}} and the body)."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"compile_memoir_chapter failed: {e}")
        fallback = f"\\chapter{{{date}}}\n\n{journal_content}\n"
        for img in image_filenames:
            fallback += f"\n\\begin{{figure}}[h]\n\\centering\n\\includegraphics[width=0.8\\textwidth]{{{img}}}\n\\end{{figure}}\n"
        return fallback


def classify_message(text: str, open_todos: list[str], today: str = "") -> dict:
    """Classify an incoming message.

    Returns one of:
      {"type": "journal", "data": {}}
      {"type": "todo_add", "data": {"todo": "clean todo text", "date": "YYYY-MM-DD"}}
      {"type": "todo_complete", "data": {"todo": "matched todo text", "notes": "..."}}
      {"type": "todo_postpone", "data": {"todo": "matched todo text", "date": "YYYY-MM-DD"}}

    date in todo_add/todo_postpone is the resolved target date (defaults to today).
    """
    from datetime import date as date_cls, timedelta
    if not today:
        today = date_cls.today().isoformat()

    todos_block = "\n".join(f"- {t}" for t in open_todos) if open_todos else "(none)"
    system = (
        "You are a message classifier for a personal journal/todo Telegram bot. "
        "Classify the user message and extract dates. "
        "Return ONLY a JSON object with keys 'type' and 'data'."
    )
    prompt = (
        f"Today's date: {today}\n"
        f"Current open todos:\n{todos_block}\n\n"
        f"User message: \"{text}\"\n\n"
        "Classification rules:\n"
        "- If the message starts with 'todo:', 'task:', or 'aufgabe:' → todo_add.\n"
        "- If it clearly describes a future action or task (action verb + time reference like 'morgen', 'heute noch', 'nächste Woche', a weekday, or a date) → todo_add.\n"
        "- If it contains 'erledigt', 'fertig', 'done', 'abgehakt', 'geschafft' and matches an open todo → todo_complete.\n"
        "- If it contains 'auf morgen', 'postpone', 'verschieben', 'verschieb' → todo_postpone.\n"
        "- Otherwise → journal.\n\n"
        "Date extraction for todo_add and todo_postpone:\n"
        "- Extract any date reference from the message and resolve it to ISO format YYYY-MM-DD.\n"
        "- 'morgen' → tomorrow, 'heute' → today, 'übermorgen' → day after tomorrow.\n"
        "- Weekday names → next occurrence of that weekday.\n"
        "- If no date is mentioned → use today's date.\n"
        "- For the 'todo' field: strip the date reference and prefix (todo:/task:) from the text, keep only the action.\n\n"
        "Return format:\n"
        "todo_add:     {\"type\": \"todo_add\",     \"data\": {\"todo\": \"clean action text\", \"date\": \"YYYY-MM-DD\"}}\n"
        "todo_complete:{\"type\": \"todo_complete\", \"data\": {\"todo\": \"matched open todo text\", \"notes\": \"any notes\"}}\n"
        "todo_postpone:{\"type\": \"todo_postpone\", \"data\": {\"todo\": \"matched open todo text\", \"date\": \"YYYY-MM-DD\"}}\n"
        "journal:      {\"type\": \"journal\",       \"data\": {}}\n\n"
        "Return ONLY the JSON object, no explanation."
    )
    try:
        response = get_client().complete(prompt, system=system)
        # Try to parse JSON
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found in classify_message response")
        msg_type = data.get("type", "journal")
        if msg_type not in ("journal", "todo_add", "todo_complete", "todo_postpone"):
            msg_type = "journal"
        return {"type": msg_type, "data": data.get("data", {})}
    except Exception as e:
        logger.error(f"classify_message failed: {e}")
        return {"type": "journal", "data": {}}


def build_weekly_summary(weekly_content: str) -> str:
    """Build a compact weekly context summary from daily context summaries."""
    system = "You are a journaling assistant building a compact weekly memory."
    prompt = (
        f"Daily summaries for this week:\n\n{weekly_content}\n\n"
        "Write a compact weekly summary (5-10 bullet points) covering the key themes, decisions, "
        "open questions, and recurring patterns across the week. "
        "Factual and dense — this will be used as background context. No prose, bullets only."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"build_weekly_summary failed: {e}")
        return weekly_content[:500]


def build_monthly_summary(monthly_content: str) -> str:
    """Build a compact monthly context summary from weekly context summaries."""
    system = "You are a journaling assistant building a compact monthly memory."
    prompt = (
        f"Weekly summaries for this month:\n\n{monthly_content}\n\n"
        "Write a compact monthly summary (5-10 bullet points) covering the major themes, "
        "decisions, projects, and shifts in focus across the month. "
        "Factual and dense — this will be used as background context. No prose, bullets only."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"build_monthly_summary failed: {e}")
        return monthly_content[:500]


def build_yearly_summary(yearly_content: str) -> str:
    """Build a compact yearly context summary from monthly context summaries."""
    system = "You are a journaling assistant building a compact yearly memory."
    prompt = (
        f"Monthly summaries for this year:\n\n{yearly_content}\n\n"
        "Write a compact yearly summary (5-10 bullet points) covering the major themes, "
        "milestones, decisions, and changes over the year. "
        "Factual and dense — this will be used as background context. No prose, bullets only."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"build_yearly_summary failed: {e}")
        return yearly_content[:500]


def summarize_week(journal_files_content: str) -> str:
    """Generate a Telegram-friendly weekly summary from journal content."""
    system = "You are a reflective journaling assistant helping someone review their week."
    prompt = (
        f"Here are this week's journal entries:\n\n{journal_files_content}\n\n"
        "Write a warm, insightful weekly summary in plain text (no markdown). "
        "Highlight key themes, moments of growth, challenges faced, and patterns noticed. "
        "Address the user directly (\"you\"). Keep it to 200-350 words. "
        "End with one gentle question for reflection going into next week."
    )
    try:
        return get_client().complete(prompt, system=system)
    except Exception as e:
        logger.error(f"summarize_week failed: {e}")
        return "Here is a summary of your week's journal entries:\n\n" + journal_files_content[:500]
