# Agent Configuration

This document describes which AI model handles each task in the journal bot, and how to configure or change them.

## Architecture

The bot calls LLM provider APIs **directly** from Python. OpenCode is installed in the container for manual interactive sessions (`docker exec -it journal-bot opencode`), but all automated logic uses the provider SDK directly.

## Tasks and Agents

| Task | Function | Description |
|------|----------|-------------|
| Message classification | `classify_message()` | Decides whether an incoming message is a journal entry, a new todo, a todo completion, or a postponement — and extracts dates from natural language ("morgen", "Freitag", etc.) |
| Follow-up questions (text) | `ask_questions()` | Given the user's initial thought, generates 1–3 clarifying questions |
| Follow-up questions (image) | `ask_questions_about_image()` | Analyses an image visually and generates 1–3 reflective questions about it |
| Voice transcription | `transcribe_audio()` | Transcribes OGG voice messages directly via Gemini's multimodal API (Google only) |
| Journal entry writing | `write_journal_entry()` | Formats thought + Q&A into a structured markdown entry |
| Topic extraction | `write_topic_entry()` | Identifies a topic slug, title, and Obsidian-compatible summary |
| Daily memoir compilation | `compile_memoir_chapter()` | Converts today's journal entries + images into a LaTeX chapter (`memoir/chapters/YYYY-MM-DD.tex`) |
| Weekly summary | `summarize_week()` | Reads the last 7 days of journal entries and writes a Telegram-friendly summary |

## Repo Structure (mounted at `/repo`)

```
journal/          YYYY-MM-DD.md     — daily log entries
topics/           [slug].md         — semantic summaries (Obsidian-compatible)
todos/            YYYY-MM-DD.md     — daily todo lists
attachments/      YYYY-MM-DD/       — photos and files sent to the bot
memoir/
  main.tex                          — LaTeX document root (\input per chapter)
  chapters/       YYYY-MM-DD.tex   — one chapter per day
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/todo <text>` | Add a todo (date extracted from text, defaults to today) |
| `/todos` | Show today's open todos |
| `/todos tomorrow` | Show tomorrow's todos |
| `/memoir [date]` | Compile journal → LaTeX chapter (defaults to today) |
| `/pdf` | Compile `memoir/main.tex` → PDF and send via Telegram |
| `/clean` | Remove all tracked files from the repo, commit, and push |
| `/cancel` | Cancel the current journal or todo flow |
| `/status` | Show current bot state |

## Natural Language Todo Detection

The bot uses `classify_message()` to detect intent before starting a journal flow:

- `todo: Steuerberater morgen` → adds to tomorrow's todo file
- `todo: 2026-05-10 Zahnarzt` → adds to that exact date
- `Zahnarzt erledigt` → marks matching todo as done, asks for a note
- `Steuerberater auf Freitag verschieben` → moves to next Friday

When no date is mentioned, todos default to **today**.

## Scheduled Jobs

| Time | Job |
|------|-----|
| Daily 23:30 | Compile today's journal into a LaTeX memoir chapter |
| Daily 22:00 | Roll over uncompleted todos to tomorrow |
| Saturday 18:15 | Send weekly summary text + compiled PDF via Telegram |

## Default Configuration (Free Tier)

```env
LLM_PROVIDER=google
LLM_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=<your key from https://aistudio.google.com/app/apikey>
```

`gemini-2.5-flash` is the current default. Check available models in the startup log:
`Available Gemini models: [...]`

## Switching Providers

### Anthropic Claude

```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-haiku-4-5
ANTHROPIC_API_KEY=<your key>
```

Note: voice transcription and image analysis are **not supported** with Anthropic (no multimodal audio API).

### OpenAI

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=<your key>
```

## OpenCode (Manual Use)

OpenCode is installed at `/usr/local/bin/opencode`. Its config is written at startup by `entrypoint.sh`.

```bash
docker exec -it journal-bot opencode
```
