"""
bot.py - Main File: Telegram-Bot + APScheduler-Integration
"""

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import re

from .init_data import ensure_data_dir
from .coach import CheckType, ask_claude
from .analyzer import (
    analyze_and_plan_tomorrow,
    format_schedule_for_display,
    get_pending_messages,
    mark_message_sent,
    run_memory_summaries,
)
from .memory import get_memory_context
from .diet import format_cheat_status_summary, get_recent_diary
from .habits import (
    add_habit,
    delete_habit,
    delete_habit_confirmed,
    done_habit,
    edit_habit,
    list_fields,
    list_habits,
)
from .scheduler import CHECK_LABELS, create_scheduler, load_schedule, reschedule_job

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID: int = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])

DATA_DIR = Path(__file__).parent.parent / "data"
CHAT_LOG_FILE = DATA_DIR / "chat_log.json"

# Short-History for LLM-Context (limited, saves tokens)
MAX_HISTORY = 6
_conversation_history: list[dict] = []

# Full daily log for nightly summary (unlimited)
_daily_log: list[dict] = []


def _load_chat_log() -> None:
    """Loads conversation history and daily log from disk (crash persistence)."""
    global _conversation_history, _daily_log
    if not CHAT_LOG_FILE.exists():
        return
    try:
        data = json.loads(CHAT_LOG_FILE.read_text(encoding="utf-8"))
        if data.get("date") != date.today().isoformat():
            logger.info("Chat-Log is from a different day – starting fresh.")
            return
        _conversation_history = data.get("conversation_history", [])
        _daily_log = data.get("daily_log", [])
        logger.info(
            "Chat-Log loaded: %d history entries, %d daily log entries",
            len(_conversation_history), len(_daily_log),
        )
    except Exception:
        logger.warning("Chat-Log could not be loaded, starting fresh.")


def _save_chat_log() -> None:
    """Writes current state to disk."""
    try:
        CHAT_LOG_FILE.write_text(
            json.dumps(
                {
                    "date": date.today().isoformat(),
                    "conversation_history": _conversation_history,
                    "daily_log": _daily_log,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.warning("Chat-Log could not be saved.")


def _fmt(text: str) -> str:
    """Converts Claude output to Telegram Markdown (parse_mode='Markdown')."""
    # **bold** → *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text, flags=re.DOTALL)
    # ### Heading → *Heading*
    text = re.sub(r'#{1,3} (.+)', r'*\1*', text)
    return text


def _is_allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == ALLOWED_USER_ID


def _append_history(role: str, content: str) -> None:
    entry = {"role": role, "content": content}
    # Short-History: limited to MAX_HISTORY pairs for LLM-Context
    _conversation_history.append(entry)
    while len(_conversation_history) > MAX_HISTORY * 2:
        _conversation_history.pop(0)
    # Daily log: grows unlimited throughout the day for summary
    _daily_log.append(entry)
    _save_chat_log()


# ---------------------------------------------------------------------------
# Proactive messages (called by the scheduler)
# ---------------------------------------------------------------------------

async def send_proactive_message(
    check_type: str,
    application: "Application | None" = None,
) -> None:
    app = application or _app
    check = cast(CheckType, check_type)
    logger.info("Proactive check-in: %s", check_type)

    try:
        await app.bot.send_chat_action(chat_id=ALLOWED_USER_ID, action=ChatAction.TYPING)
        response = await ask_claude(check_type=check, conversation_history=list(_conversation_history))
        await app.bot.send_message(chat_id=ALLOWED_USER_ID, text=_fmt(response), parse_mode="Markdown")

        _append_history("user", f"[Proactive {check_type}-Check-in]")
        _append_history("assistant", response)


        # Morgens: zweite separate Nachricht mit der Ernährungsfrage
        if check_type == "morning":
            food_question = (
                "Weißt du schon, was du heute essen wirst? 🍽️\n\n"
                "Erzähl mir einfach von deinen geplanten Mahlzeiten  "
                "ich schaue dann, ob das gut zu deinen Ernährungszielen passt, "
                "und gebe dir Vorschläge falls du noch keine Idee hast."
            )
            await app.bot.send_message(chat_id=ALLOWED_USER_ID, text=food_question)
            _append_history("assistant", food_question)

    except Exception:
        logger.exception("Error while proactive check-in '%s'", check_type)


# ---------------------------------------------------------------------------
# Handler for user messages
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        logger.warning("Unauthorized access from User-ID %s", update.effective_user and update.effective_user.id)
        return

    user_text = update.message.text or ""
    logger.info("User message received: %s", user_text[:80])

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        response = await ask_claude(
            check_type="user_reply",
            user_text=user_text,
            conversation_history=list(_conversation_history),
        )
        await update.message.reply_text(_fmt(response), parse_mode="Markdown")

        _append_history("user", user_text)
        _append_history("assistant", response)

    except Exception:
        logger.exception("Error while processing user message")
        await update.message.reply_text(
            "Entschuldigung, da ist etwas schiefgelaufen. Bitte versuche es gleich nochmal."
        )


HELP_TEXT = r"""*Dein persönlicher Gesundheitscoach*

*Allgemein*
/start - Begrüßung
/help - Diese Übersicht
/status - Nächste Check\-ins & Cheat\-Status
/clear - Gesprächsverlauf zurücksetzen

*Check\-in Zeiten*
/schedule show - Aktuelle Zeiten anzeigen
/schedule morning 08:00 - Zeit ändern
/schedule noon 12:30
/schedule evening 21:00

*Gewohnheiten & Ziele*
/habits - Alle anzeigen
/habit add Titel - Neu hinzufügen
/habit edit 1 title Neuer Titel - Feld bearbeiten
/habit edit 1 priority hoch - Priorität setzen
/habit edit 1 target\_date 2026\-12\-31 - Zieldatum
/habit done 1 - Streak erhöhen ✅
/habit delete 1 - Löschen \(mit Bestätigung\)
/habit fields - Alle editierbaren Felder

*Ernährung*
/diet - Tagebuch \(letzte 7 Tage\) \+ Cheat\-Status
/diet log dinner Spaghetti Bolognese - Mahlzeit eintragen
/diet log lunch Döner \-\- fastfood,döner - mit Tags

*Manueller Test*
/checkin morning\|noon\|evening - Check\-in jetzt auslösen

*Natürliche Sprache funktioniert überall\!*
_„Heute Abend esse ich Döner"_ → Eintrag \+ Cheat\-Check
_„Was soll ich heute zu Mittag essen?"_ → Vorschläge nach Zielen
_„Ich möchte lieber 6000 Schritte gehen"_ → Habit wird angepasst
"""


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="MarkdownV2")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "Hallo! Ich bin dein persönlicher Gesundheitscoach.\n\n"
        "Ich melde mich täglich zu den konfigurierten Check-in Zeiten bei dir.\n"
        "Du kannst mir jederzeit schreiben - ich bin für dich da!\n\n"
        "/help - alle Befehle anzeigen"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return

    scheduler_jobs = _scheduler.get_jobs()
    lines = ["*Geplante Check-ins:*"]
    for job in scheduler_jobs:
        next_run = job.next_run_time
        lines.append(f"• {job.name}: nächster Run {next_run.strftime('%d.%m. %H:%M') if next_run else 'unbekannt'}")

    lines.append("\n_Zeiten ändern: /schedule morning 08:00_")
    lines.append(f"\n*Cheat-Limits diese Woche:*\n{format_cheat_status_summary()}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alters the time of a check-in. Syntax: /schedule <morning|noon|evening> <HH:MM>"""
    if not _is_allowed(update):
        return

    args = context.args or []

    # /schedule show – aktuelle Zeiten anzeigen
    if not args or args[0] == "show":
        schedule = load_schedule()
        lines = ["*Aktuelle Check-in Zeiten:*"]
        for check_type, time_str in schedule.items():
            lines.append(f"• {CHECK_LABELS[check_type]}: {time_str} Uhr")
        lines.append("\n_Ändern: /schedule morning 08:00_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if len(args) < 2:
        await update.message.reply_text(
            "Verwendung: `/schedule <morning|noon|evening> <HH:MM>`\n"
            "Beispiel: `/schedule morning 08:00`\n"
            "Aktuelle Zeiten: `/schedule show`",
            parse_mode="Markdown",
        )
        return

    check_type, time_str = args[0].lower(), args[1]

    if check_type not in CHECK_LABELS:
        await update.message.reply_text(
            f"Unbekannter Check-in Typ: `{check_type}`\nErlaubt: morning, noon, evening",
            parse_mode="Markdown",
        )
        return

    try:
        reschedule_job(_scheduler, check_type, time_str, _send_wrapper_fn)
    except ValueError:
        await update.message.reply_text(
            f"Ungültige Uhrzeit: `{time_str}`\nBitte im Format HH:MM angeben, z.B. `08:30`",
            parse_mode="Markdown",
        )
        return

    label = CHECK_LABELS[check_type]
    await update.message.reply_text(
        f"*{label}* wurde auf *{time_str} Uhr* umgestellt und gespeichert.",
        parse_mode="Markdown",
    )


async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manueller Check-in – nützlich zum Testen."""
    if not _is_allowed(update):
        return

    args = context.args or []
    check_type = args[0] if args else "morning"
    valid = {"morning", "noon", "evening"}
    if check_type not in valid:
        await update.message.reply_text(f"Ungültiger Typ. Erlaubt: {', '.join(valid)}")
        return

    await send_proactive_message(check_type)


async def run_night_analysis(application: "Application | None" = None) -> None:
    """23:00 Job: Tages-Zusammenfassung → Wochen/Monats-Summary (falls fällig) → Nacht-Analyse."""
    logger.info("Starting nightly Job...")
    try:
        # 1. Gesprächsgedächtnis: Zusammenfassung des heutigen Tages erstellen
        await run_memory_summaries(list(_daily_log))
        logger.info("Memory summaries completed.")
        # Reset daily log – content is now saved in data/memory/
        _daily_log.clear()
        _save_chat_log()
    except Exception:
        logger.exception("Error during memory summaries")

    try:
        # 2. Create dynamic plan for tomorrow
        schedule = await analyze_and_plan_tomorrow()
        n = len(schedule.get("messages", []))
        logger.info("Night analysis completed: %d messages planned.", n)
    except Exception:
        logger.exception("Error during night analysis")


async def poll_dynamic_messages(application: "Application | None" = None) -> None:
    """Every 5 minutes: Checks if dynamic messages should be sent."""
    app = application or _app
    from datetime import datetime
    pending = get_pending_messages(datetime.now())
    for msg in pending:
        try:
            await app.bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=_fmt(msg["message"]),
                parse_mode="Markdown",
            )
            mark_message_sent(msg["id"])
            logger.info("Dynamic message sent: [%s] %s", msg.get("topic"), msg["message"][:60])
        except Exception:
            logger.exception("Error sending dynamic message %s", msg.get("id"))


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the stored memory (latest summaries)."""
    if not _is_allowed(update):
        return
    text = get_memory_context()
    # Split into blocks if too long for a Telegram message
    if len(text) > 3800:
        chunks = [text[i:i+3800] for i in range(0, len(text), 3800)]
        for chunk in chunks:
            await update.message.reply_text(_fmt(chunk), parse_mode="Markdown")
    else:
        await update.message.reply_text(_fmt(text), parse_mode="Markdown")


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the dynamic plan for tomorrow."""
    if not _is_allowed(update):
        return
    await update.message.reply_text(format_schedule_for_display(), parse_mode="Markdown")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggers the night analysis manually (for testing)."""
    if not _is_allowed(update):
        return
    await update.message.reply_text("Analyse läuft... ⏳")
    try:
        schedule = await analyze_and_plan_tomorrow()
        n = len(schedule.get("messages", []))
        await update.message.reply_text(
            f"✅ Analyse abgeschlossen: *{n} Nachrichten* für morgen geplant.\n\n"
            f"_{schedule.get('analysis', '')}_\n\n"
            f"/plan für Details",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Error during manual analysis")
        await update.message.reply_text("Error during analysis. Please check logs.")


async def cmd_diet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the diet diary or logs a meal directly."""
    if not _is_allowed(update):
        return

    args = context.args or []

    # /diet log <meal_type> <beschreibung> [-- tags]
    if args and args[0].lower() == "log":
        if len(args) < 3:
            await update.message.reply_text(
                "Verwendung: `/diet log <breakfast|lunch|dinner|snack> <Beschreibung>`\n"
                "Optional mit Tags: `/diet log dinner Döner -- fastfood,döner`",
                parse_mode="Markdown",
            )
            return

        meal_type = args[1].lower()
        rest = " ".join(args[2:])
        tags = ""
        if " -- " in rest:
            description, tags = rest.split(" -- ", 1)
        else:
            description = rest

        from .diet import log_meal as _log_meal
        result = _log_meal(meal_type=meal_type, description=description.strip(), tags=tags.strip())
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    # /diet – Tagebuch + Cheat-Status anzeigen
    diary = get_recent_diary(7)
    cheat = format_cheat_status_summary()
    text = f"*Ernährungstagebuch (letzte 7 Tage)*\n\n{diary}\n\n*Cheat-Limits diese Woche*\n{cheat}"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_habits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows all habits."""
    if not _is_allowed(update):
        return
    await update.message.reply_text(list_habits(), parse_mode="Markdown")


async def cmd_habit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Habit-Verwaltung: add / edit / done / delete / fields."""
    if not _is_allowed(update):
        return

    args = context.args or []

    if not args:
        await update.message.reply_text(
            "*Habit-Befehle:*\n"
            "`/habits` – alle anzeigen\n"
            "`/habit add Titel` – neu hinzufügen\n"
            "`/habit edit 1 title Neuer Titel` – Feld bearbeiten\n"
            "`/habit done 1` – Streak erhöhen\n"
            "`/habit delete 1` – löschen (mit Bestätigung)\n"
            "`/habit fields` – editierbare Felder anzeigen",
            parse_mode="Markdown",
        )
        return

    subcommand = args[0].lower()

    # /habit fields
    if subcommand == "fields":
        await update.message.reply_text(list_fields(), parse_mode="Markdown")
        return

    # /habit add <titel...>
    if subcommand == "add":
        if len(args) < 2:
            await update.message.reply_text("Bitte einen Titel angeben: `/habit add Mein Ziel`", parse_mode="Markdown")
            return
        title = " ".join(args[1:])
        await update.message.reply_text(add_habit(title), parse_mode="Markdown")
        return

    # /habit edit <nr> <feld> <wert...>
    if subcommand == "edit":
        if len(args) < 4:
            await update.message.reply_text(
                "Verwendung: `/habit edit <Nr> <Feld> <Wert>`\n"
                "Beispiel: `/habit edit 1 priority hoch`\n"
                "Felder: `/habit fields`",
                parse_mode="Markdown",
            )
            return
        try:
            index = int(args[1])
        except ValueError:
            await update.message.reply_text("Die Nummer muss eine Zahl sein, z.B. `1`.", parse_mode="Markdown")
            return
        field = args[2].lower()
        value = " ".join(args[3:])
        await update.message.reply_text(edit_habit(index, field, value), parse_mode="Markdown")
        return

    # /habit done <nr>
    if subcommand == "done":
        if len(args) < 2:
            await update.message.reply_text("Verwendung: `/habit done <Nr>`", parse_mode="Markdown")
            return
        try:
            index = int(args[1])
        except ValueError:
            await update.message.reply_text("Die Nummer muss eine Zahl sein, z.B. `1`.", parse_mode="Markdown")
            return
        await update.message.reply_text(done_habit(index), parse_mode="Markdown")
        return

    # /habit delete <nr> [confirm]
    if subcommand == "delete":
        if len(args) < 2:
            await update.message.reply_text("Verwendung: `/habit delete <Nr>`", parse_mode="Markdown")
            return
        try:
            index = int(args[1])
        except ValueError:
            await update.message.reply_text("Die Nummer muss eine Zahl sein, z.B. `1`.", parse_mode="Markdown")
            return
        if len(args) >= 3 and args[2].lower() == "confirm":
            await update.message.reply_text(delete_habit_confirmed(index), parse_mode="Markdown")
        else:
            _, msg = delete_habit(index)
            await update.message.reply_text(msg, parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"Unbekannter Befehl `{subcommand}`. Verfügbar: add, edit, done, delete, fields",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gesprächsverlauf zurücksetzen."""
    if not _is_allowed(update):
        return
    _conversation_history.clear()
    _save_chat_log()
    await update.message.reply_text("Gesprächsverlauf wurde zurückgesetzt.")


# ---------------------------------------------------------------------------
# App-Setup
# ---------------------------------------------------------------------------

_app: "Application"
_scheduler = None
_send_wrapper_fn = None


def main() -> None:
    global _app, _scheduler

    ensure_data_dir()
    _load_chat_log()

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )
    _app = application

    # Kommandos
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("schedule", cmd_schedule))
    application.add_handler(CommandHandler("memory", cmd_memory))
    application.add_handler(CommandHandler("plan", cmd_plan))
    application.add_handler(CommandHandler("analyze", cmd_analyze))
    application.add_handler(CommandHandler("diet", cmd_diet))
    application.add_handler(CommandHandler("habits", cmd_habits))
    application.add_handler(CommandHandler("habit", cmd_habit))
    application.add_handler(CommandHandler("checkin", cmd_checkin))
    application.add_handler(CommandHandler("clear", cmd_clear))

    # Textnachrichten
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Scheduler einrichten
    async def _send_wrapper(check_type: str) -> None:
        await send_proactive_message(check_type, application=application)

    async def _analysis_wrapper() -> None:
        await run_night_analysis(application=application)

    async def _poll_wrapper() -> None:
        await poll_dynamic_messages(application=application)

    global _send_wrapper_fn
    _send_wrapper_fn = _send_wrapper
    _scheduler = create_scheduler(_send_wrapper, _analysis_wrapper, _poll_wrapper)

    async def on_startup(app: Application) -> None:
        _scheduler.start()
        logger.info("Scheduler gestartet. Bot läuft.")

    async def on_shutdown(app: Application) -> None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler gestoppt.")

    application.post_init = on_startup
    application.post_shutdown = on_shutdown

    logger.info("Starte Telegram Health Coach Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
