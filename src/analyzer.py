"""
analyzer.py - Night analysis and dynamic scheduling for the health coach bot.

Flow:
  23:00 → analyze_and_plan_tomorrow() → data/dynamic_schedule.json
  Every 5 minutes (08-22) → get_pending_messages() → Bot sends due messages
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).parent.parent / "data"
DYNAMIC_SCHEDULE_FILE = DATA_DIR / "dynamic_schedule.json"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schedule-Datei lesen / schreiben
# ---------------------------------------------------------------------------

def load_dynamic_schedule() -> dict:
    if not DYNAMIC_SCHEDULE_FILE.exists():
        return {"date": "", "messages": []}
    try:
        return json.loads(DYNAMIC_SCHEDULE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"date": "", "messages": []}


def save_dynamic_schedule(schedule: dict) -> None:
    DYNAMIC_SCHEDULE_FILE.write_text(
        json.dumps(schedule, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_pending_messages(now: datetime | None = None) -> list[dict]:
    """
    Returns the messages that should be sent now (±3 minutes)
    and are not yet marked as sent.
    """
    now = now or datetime.now()
    schedule = load_dynamic_schedule()
    today = date.today().isoformat()

    if schedule.get("date") != today:
        return []

    pending = []
    for msg in schedule.get("messages", []):
        if msg.get("sent"):
            continue
        try:
            msg_dt = datetime.strptime(f"{today} {msg['time']}", "%Y-%m-%d %H:%M")
            if abs((now - msg_dt).total_seconds()) <= 180:  # ±3 minutes
                pending.append(msg)
        except ValueError:
            logger.warning("Invalid time format in dynamic_schedule: %s", msg.get("time"))
    return pending


def mark_message_sent(msg_id: str) -> None:
    schedule = load_dynamic_schedule()
    for msg in schedule.get("messages", []):
        if msg.get("id") == msg_id:
            msg["sent"] = True
    save_dynamic_schedule(schedule)


def format_schedule_for_display() -> str:
    """Formats the dynamic schedule for the /plan command."""
    schedule = load_dynamic_schedule()
    target_date = schedule.get("date", "")
    messages = schedule.get("messages", [])

    if not target_date:
        return "No dynamic schedule generated yet. It will be created at 23:00."

    lines = [f"*Dynamic Schedule for {target_date}*\n"]

    analysis = schedule.get("analysis", "")
    if analysis:
        lines.append(f"_Analysis:_ {analysis}\n")

    if not messages:
        lines.append("No additional messages planned.")
    else:
        for msg in sorted(messages, key=lambda x: x.get("time", "")):
            status = "✅" if msg.get("sent") else "⏳"
            lines.append(f"{status} *{msg['time']}* [{msg.get('topic', '')}]\n   {msg['message']}")

    generated_at = schedule.get("generated_at", "")
    if generated_at:
        try:
            dt = datetime.fromisoformat(generated_at)
            lines.append(f"\n_Generated at: {dt.strftime('%d.%m. %H:%M')} Uhr_")
        except ValueError:
            pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Night analysis: LLM plans the next day
# ---------------------------------------------------------------------------

def _build_client() -> tuple[anthropic.AsyncAnthropic, str]:
    """Returns (client, model) – identical to coach.py."""
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    use_openrouter = bool(openrouter_api_key)
    if use_openrouter:
        client = anthropic.AsyncAnthropic(
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api",
            default_headers={"HTTP-Referer": "health-coach-bot"},
        )
    else:
        client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
    if use_openrouter and "/" not in model:
        model = f"anthropic/{model}"

    return client, model


def _build_analysis_context() -> str:
    """Reads all relevant data files for analysis."""
    from .coach import build_context
    return build_context()


async def run_memory_summaries(conversation_history: list[dict]) -> None:
    """
    Creates daily, weekly, and monthly summaries.
    Called before the night analysis.
    """
    from .memory import (
        should_summarize_month,
        should_summarize_week,
        summarize_day,
        summarize_month,
        summarize_week,
    )

    today = date.today()

    # Always: Daily summary
    logger.info("Creating daily summary for %s...", today)
    await summarize_day(conversation_history, today)

    # Sundays: Weekly summary
    if should_summarize_week(today):
        logger.info("Creating weekly summary...")
        await summarize_week(today)

    # Last day of the month: Monthly summary
    if should_summarize_month(today):
        logger.info("Creating monthly summary...")
        await summarize_month(today)


async def analyze_and_plan_tomorrow() -> dict:
    """
    Main function: Reads all data, lets Claude analyze,
    and saves the dynamic schedule for tomorrow.
    """
    from .scheduler import load_schedule

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    fixed_times = set(load_schedule().values())
    context = _build_analysis_context()

    prompt = f"""Du bist ein intelligenter Analyse-Assistent für einen persönlichen Gesundheitscoach-Bot.

## Aufgabe
Analysiere die Gesundheitsdaten des Nutzers und plane bis zu 5 proaktive Nachrichten für morgen ({tomorrow}).
Die Nachrichten werden automatisch zum geplanten Zeitpunkt über Telegram gesendet.

## Regeln für die Zeitplanung
- Uhrzeiten: zwischen 08:00 und 22:00 Uhr
- NICHT zu den fixen Check-in Zeiten: {', '.join(sorted(fixed_times))} Uhr (±30 Min Puffer)
- Mindestabstand zwischen Nachrichten: 90 Minuten
- Immer volle 5-Minuten-Schritte wählen (z.B. 10:30, 14:15, 18:00)
- Uhrzeiten sinnvoll wählen: Wassererinnerung → mittags/nachmittags, Sport → morgens/abends, Mahlzeitenhinweis → vor der Mahlzeit

## Regeln für den Inhalt
- Maximal 5 Nachrichten - lieber weniger, aber treffsichere
- Nur bei echten Schwächen oder konkreten Plänen eingreifen
- Positives Feedback wenn Ziele gut laufen (nicht nur kritisieren)
- Direkt auf morgen bezogene Pläne (planned_meals) einbeziehen
- Kurz, persönlich, motivierend - kein Coaching-Blabla
- Auf Deutsch schreiben
- Kein **fett** - nutze *fett* (einfache Sternchen) für Telegram

## Aktueller Kontext (alle relevanten Daten)
{context}

## Ausgabe
Antworte ausschließlich mit einem validen JSON-Objekt, ohne Markdown-Codeblock, ohne Erklärungstext:

{{
  "analysis": "2-3 Sätze: Was läuft gut? Wo gibt es Lücken? Was ist für morgen relevant?",
  "messages": [
    {{
      "id": "dyn_001",
      "time": "HH:MM",
      "topic": "wasser | fastfood | sport | ernaehrung | planung | lob | schlaf",
      "message": "Nachrichtentext direkt an den User"
    }}
  ]
}}"""

    client, model = _build_client()
    logger.info("Start night analysis for %s with model %s", tomorrow, model)

    response = await client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    logger.debug("Raw analysis response: %s", raw[:300])

    # Robustly extract JSON (in case Claude adds text around it)
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        raise ValueError(f"No JSON in Claude's response: {raw[:300]}")

    result = json.loads(json_match.group())

    # Validation and sent-flag
    messages = result.get("messages", [])
    for msg in messages:
        msg.setdefault("sent", False)
        if "id" not in msg:
            msg["id"] = f"dyn_{messages.index(msg) + 1:03d}"

    schedule = {
        "date":         tomorrow,
        "generated_at": datetime.now().isoformat(),
        "analysis":     result.get("analysis", ""),
        "messages":     messages[:5],  # Hard-Cap: max. 5
    }

    save_dynamic_schedule(schedule)
    logger.info(
        "Dynamic schedule for %s saved: %d messages – %s",
        tomorrow,
        len(messages),
        result.get("analysis", "")[:80],
    )
    return schedule
