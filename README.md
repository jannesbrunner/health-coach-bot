# Health Coach

A personal Telegram bot acting as your health coach, working with Anthropic's Claude API or Open Router API.

The bot accompanies you daily with three check-ins (morning, noon, evening), tracks habits and nutrition, and regularly summarizes your progress.

> **Language note:** The bot is currently German-only — all conversations, prompts, and data files are in German. Localization support is planned for a future release.

## Features

- **Daily check-ins** via Telegram at configurable times
- **Habit tracking** with weekly targets
- **Diet diary** with cheat limits and nutrition goals
- **Meal planning** for upcoming days
- **Memory** — daily, weekly, and monthly summaries of your progress
- **Dynamic day planning** — nightly analysis and scheduling

## Prerequisites

- Telegram Bot Token ([BotFather](https://t.me/BotFather))
- Anthropic API Key ([console.anthropic.com](https://console.anthropic.com))
- OR Open Router API Key ([openrouter.com](https://openrouter.com))
- Docker & Docker Compose 
- OR local Python environment with dependencies (see `pyproject.toml`)

## Setup

### 1. Configure environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

```env
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USER_ID=your-telegram-user-id
ANTHROPIC_API_KEY=your-anthropic-key
OPEN_ROUTER_API_KEY=your-open-router-key (optional, only if using Open Router)
TIMEZONE=Europe/Berlin
```

### 2. Data files

On first start, the bot automatically creates all required files in the `data/` directory:

| File | Contents |
|---|---|
| `health_profile.md` | Your health profile — fill this in |
| `system_prompt.md` | Coach behavior — customizable |
| `goals.yaml` | Personal habit goals |
| `diet_goals.yaml` | Nutrition goals and cheat limits |
| `schedule.json` | Check-in times |
| `*.csv` | Tracking data (empty, grows with usage) |

After the first start, edit `data/health_profile.md`, `data/goals.yaml`, and `data/diet_goals.yaml` with your personal data.

### 3. Start

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f
```

## Data & Privacy

All user data lives exclusively in the `data/` directory, which is mounted as a Docker volume. The image contains no personal data. The directory is listed in `.gitignore` and will never be committed to the repository.

## Local Development

```bash
uv sync
cp .env.example .env  # fill in values
uv run python main.py
```
