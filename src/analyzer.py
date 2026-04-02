"""
analyzer.py – Nacht-Analyse und dynamische Tagesplanung

Flow:
  23:00 → analyze_and_plan_tomorrow() → data/dynamic_schedule.json
  Alle 5 Min (08-22 Uhr) → get_pending_messages() → Bot sendet fällige Nachrichten
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
    Gibt Nachrichten zurück die jetzt (±3 Minuten) gesendet werden sollen
    und noch nicht als sent markiert sind.
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
            if abs((now - msg_dt).total_seconds()) <= 180:  # ±3 Minuten
                pending.append(msg)
        except ValueError:
            logger.warning("Ungültiges Zeitformat in dynamic_schedule: %s", msg.get("time"))
    return pending


def mark_message_sent(msg_id: str) -> None:
    schedule = load_dynamic_schedule()
    for msg in schedule.get("messages", []):
        if msg.get("id") == msg_id:
            msg["sent"] = True
    save_dynamic_schedule(schedule)


def format_schedule_for_display() -> str:
    """Formatiert den dynamischen Plan für /plan Command."""
    schedule = load_dynamic_schedule()
    target_date = schedule.get("date", "")
    messages = schedule.get("messages", [])

    if not target_date:
        return "Noch kein dynamischer Plan generiert. Wird um 23:00 Uhr erstellt."

    lines = [f"*Dynamischer Plan für {target_date}*\n"]

    analysis = schedule.get("analysis", "")
    if analysis:
        lines.append(f"_Analyse:_ {analysis}\n")

    if not messages:
        lines.append("Keine zusätzlichen Nachrichten geplant.")
    else:
        for msg in sorted(messages, key=lambda x: x.get("time", "")):
            status = "✅" if msg.get("sent") else "⏳"
            lines.append(f"{status} *{msg['time']}* [{msg.get('topic', '')}]\n   {msg['message']}")

    generated_at = schedule.get("generated_at", "")
    if generated_at:
        try:
            dt = datetime.fromisoformat(generated_at)
            lines.append(f"\n_Generiert: {dt.strftime('%d.%m. %H:%M')} Uhr_")
        except ValueError:
            pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Nacht-Analyse: Claude plant den nächsten Tag
# ---------------------------------------------------------------------------

def _build_client() -> tuple[anthropic.AsyncAnthropic, str]:
    """Gibt (client, model) zurück – identisch zu coach.py."""
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
    """Liest alle relevanten Datendateien für die Analyse ein."""
    from .coach import build_context
    return build_context()


async def run_memory_summaries(conversation_history: list[dict]) -> None:
    """
    Erstellt tägliche, ggf. wöchentliche und monatliche Zusammenfassungen.
    Wird vor der Nacht-Analyse aufgerufen.
    """
    from .memory import (
        should_summarize_month,
        should_summarize_week,
        summarize_day,
        summarize_month,
        summarize_week,
    )

    today = date.today()

    # Immer: Tages-Zusammenfassung
    logger.info("Erstelle Tages-Zusammenfassung für %s...", today)
    await summarize_day(conversation_history, today)

    # Sonntags: Wochen-Zusammenfassung
    if should_summarize_week(today):
        logger.info("Erstelle Wochen-Zusammenfassung...")
        await summarize_week(today)

    # Letzter Tag des Monats: Monats-Zusammenfassung
    if should_summarize_month(today):
        logger.info("Erstelle Monats-Zusammenfassung...")
        await summarize_month(today)


async def analyze_and_plan_tomorrow() -> dict:
    """
    Hauptfunktion: Liest alle Daten, lässt Claude analysieren,
    speichert den dynamischen Zeitplan für morgen.
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
- Maximal 5 Nachrichten – lieber weniger, aber treffsichere
- Nur bei echten Schwächen oder konkreten Plänen eingreifen
- Positives Feedback wenn Ziele gut laufen (nicht nur kritisieren)
- Direkt auf morgen bezogene Pläne (planned_meals) einbeziehen
- Kurz, persönlich, motivierend – kein Coaching-Blabla
- Auf Deutsch schreiben
- Kein **fett** – nutze *fett* (einfache Sternchen) für Telegram

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
    logger.info("Starte Nacht-Analyse für %s mit Modell %s", tomorrow, model)

    response = await client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    logger.debug("Rohantwort der Analyse: %s", raw[:300])

    # JSON robust extrahieren (falls Claude trotzdem Text drumherum schreibt)
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        raise ValueError(f"Kein JSON in der Claude-Antwort: {raw[:300]}")

    result = json.loads(json_match.group())

    # Validierung und sent-Flag
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
        "Dynamischer Plan für %s gespeichert: %d Nachrichten – %s",
        tomorrow,
        len(messages),
        result.get("analysis", "")[:80],
    )
    return schedule
