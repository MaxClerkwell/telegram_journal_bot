# Journal Bot

A Dockerized Telegram bot that turns your thoughts, voice messages, and photos into a structured personal journal — with follow-up questions, topic extraction, todo management, and a compiled LaTeX memoir.

---

## What it does

You send a message (text, voice, or photo) to your personal Telegram bot. The bot asks up to three context-aware follow-up questions, one at a time. When the conversation feels complete, it:

- Writes a structured entry to `journal/YYYY-MM-DD.md`
- Extracts a topic and updates or creates the corresponding `topics/[slug].md` (Obsidian-compatible)
- Commits and pushes everything to your private git repository

At 23:30 each night it compiles the day's journal into a LaTeX chapter. Every Saturday at 18:15 it sends you a written weekly summary and a compiled PDF of your memoir. Context summaries are built automatically (daily → weekly → monthly → yearly) so the bot's follow-up questions improve over time.

---

## Prerequisites

- A **Debian-based Linux host** with Docker and Docker Compose installed
- A **Telegram account** and a bot created via BotFather
- A **Google account** and an API key from Google AI Studio (free tier works)
- A **private git repository** accessible via SSH (e.g. on GitHub or Gitea) that will store your journal files
- Your SSH key pair for that repository (`~/.ssh/id_rsa` or `~/.ssh/id_ed25519`)

---

## Installation

### 1. Clone this repository

```bash
git clone <this-repo-url>
cd journal-bot
```

### 2. Run the setup script

```bash
bash setup.sh
```

This copies your SSH keys from `~/.ssh` into `./ssh/` with the correct permissions, and creates a `.env` file from the template.

### 3. Fill in the `.env` file

```bash
vim .env
```

You will fill this in step by step below. Leave it open or come back to it.

---

## Step-by-step configuration

### Create a Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a name and a username ending in `bot`).
3. BotFather will show you a **token** that looks like `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`. Copy it.
4. Paste it into `.env`:
   ```
   TELEGRAM_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
   ```

### Find your Telegram chat ID

1. Open your browser and navigate to:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Replace `<YOUR_TOKEN>` with the token you just copied.

2. The page will show `{"ok":true,"result":[]}` — no messages yet.

3. Now open Telegram, find your new bot, and **send it any message** (e.g. `hello`).

4. **Reload** the browser page. You will see a JSON response. Find the `"chat"` object and copy the `"id"` value:
   ```json
   "chat": { "id": 987654321, ... }
   ```

5. Paste it into `.env`:
   ```
   TELEGRAM_CHAT_ID=987654321
   ```

### Get a Google AI API key

1. Go to [aistudio.google.com](https://aistudio.google.com).
2. Sign in with your Google account. No payment method is required to use free-tier models.
3. Click **Get API key** → **Create API key in new project**.
4. Copy the generated key and paste it into `.env`:
   ```
   GOOGLE_API_KEY=AIzaSy...
   ```

### Set your journal repository

Add the SSH URL of your private journal repository:

```
JOURNAL_REPO=git@github.com:youruser/your-journal-repo.git
```

The bot will clone this repository automatically on first start. Make sure the SSH key in `./ssh/` has read/write access to this repository. On GitHub, go to the repository → Settings → Deploy keys → Add deploy key, paste in the **public key** (`~/.ssh/id_rsa.pub` or `id_ed25519.pub`), and enable **Allow write access**.

### Choosing a model

The default is `gemini-2.5-flash`. If you hit rate limits on the free tier, `gemma-3-4b-it` is a good alternative — it has a significantly higher quota and is available through the same API key:

```env
LLM_MODEL=gemma-3-4b-it
```

Note that `gemma-3-4b-it` does not support audio transcription or image analysis. Voice messages and photos will fall back to text-only handling when using this model.

### Completed `.env` example

```env
TELEGRAM_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=987654321
JOURNAL_REPO=git@github.com:youruser/your-journal-repo.git
LLM_PROVIDER=google
LLM_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=AIzaSy...
GIT_USER_NAME=Journal Bot
GIT_USER_EMAIL=journal@bot.local
```

---

## Start the bot

```bash
docker compose up -d
```

Check the logs:

```bash
docker compose logs -f
```

You should see the bot start, connect to Telegram, and confirm the scheduler is running.

---

## Commands

| Command | Description |
|---|---|
| _(any text)_ | Start a journal entry. The bot asks up to 3 follow-up questions, then writes the entry. |
| `/todo <text>` | Add a todo for today. |
| `/todos` | Show today's open todos. |
| `/todos tomorrow` | Show tomorrow's todos. |
| `/done` | Show all open todos across the next 14 days. Enter the number to mark one as done. Enter `0` to cancel. |
| `/memoir [YYYY-MM-DD]` | Manually compile a memoir chapter for today (or a specific date). |
| `/pdf` | Compile and send the full memoir PDF. Missing chapters are auto-generated first. |
| `/chat` | Enter freeform chat mode with the bot. |
| `/cancel` | Cancel the current conversation or command. |
| `/status` | Show the bot's current internal state. |
| `/clean` | Remove all tracked files from the journal repo and push a clean commit. |

### In `/chat` mode

| Input | Action |
|---|---|
| _(any text)_ | Continue the conversation |
| `/:wq` | Save the conversation as a journal entry and exit |
| `/:q!` | Exit without saving |

---

## How it works

### Journal flow

1. You send a message (text, voice, or photo).
2. The bot transcribes voice messages via Gemini's multimodal API.
3. For photos, Gemini looks at the image and generates initial questions.
4. For text, the bot classifies the message first: is it a todo, a todo completion, a postponement, or a journal entry?
5. If it's a journal entry, the bot asks one follow-up question at a time (up to 3 rounds). Each question is generated fresh based on everything said so far and recent context from past days.
6. Once done, the bot writes a cohesive journal entry, extracts a topic note, and commits both to your repository.

### Todo management

Todos can be added directly via `/todo`, or the bot recognizes natural-language todo phrasing in normal messages (e.g. "I need to call the doctor tomorrow" → classified as `todo_add` for tomorrow's date). Date references like "tomorrow", "Monday", or "next week" are resolved to ISO dates by Gemini.

At 22:00 each night, uncompleted todos from today are automatically rolled over to tomorrow.

### Memoir

At 23:30 each night, today's journal file is compiled into a LaTeX chapter. The chapter is added to `memoir/main.tex`. Any photos from the day are embedded in the chapter with captions.

Every Saturday at 18:15, the bot sends a written weekly summary and compiles the full `memoir/main.tex` to a PDF using `pdflatex`, which it sends to you via Telegram.

### Context system

After compiling the daily memoir, the bot saves a compact bullet-point summary of the day to `context/YYYY-MM-DD.md`. These summaries are loaded as background context when generating follow-up questions, so the bot remembers what you have been working on and thinking about.

The context rolls up automatically:

| Trigger | Action |
|---|---|
| Every night (23:30) | Daily context summary saved |
| Every Sunday (23:45) | Weekly context built from the week's daily summaries |
| 1st of every month (00:15) | Monthly context built from the month's weekly summaries |
| January 1st (00:30) | Yearly context built from the year's monthly summaries |

All context files are committed and pushed to your journal repository alongside journal and topic files.

### Repository structure

After using the bot for a while, your journal repository will look like this:

```
journal/
  2026-05-03.md       # daily journal entries
  2026-05-04.md
topics/
  deep-work.md        # Obsidian-compatible topic notes
  anxiety-management.md
attachments/
  2026-05-03/
    photo.jpg
memoir/
  main.tex            # LaTeX memoir (all chapters)
  chapters/
    2026-05-03.tex
    2026-05-04.tex
context/
  2026-05-03.md       # daily context summaries
  weeks/
    2026-W18.md
  months/
    2026-05.md
  years/
    2025.md
todos/
  2026-05-03.json
```

---

## Using a different LLM provider

The bot supports Google Gemini (default), Anthropic Claude, and OpenAI. To switch:

**Anthropic:**
```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-7
ANTHROPIC_API_KEY=sk-ant-...
```

**OpenAI:**
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...
```

Note: voice transcription and image analysis are only supported with the Google provider.

---

## Stopping and updating

```bash
# Stop
docker compose down

# Pull updates and rebuild
git pull
docker compose up -d --build
```

Logs:
```bash
docker compose logs -f
```
