"""
memory.py – Gesprächsgedächtnis durch hierarchische Zusammenfassungen

Struktur:
  data/memory/daily/YYYY-MM-DD.md     – tägliche Zusammenfassung
  data/memory/weekly/YYYY-WXX.md      – wöchentliche Zusammenfassung (aus 7 Tages-Summaries)
  data/memory/monthly/YYYY-MM.md      – monatliche Zusammenfassung (aus 4 Wochen-Summaries)

Trigger (alle via analyzer.py um 23:00):
  - Täglich:    immer
  - Wöchentlich: sonntags
  - Monatlich:  am letzten Tag des Monats
"""

from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

import anthropic

DATA_DIR   = Path(__file__).parent.parent / "data"
MEMORY_DIR = DATA_DIR / "memory"
DAILY_DIR  = MEMORY_DIR / "daily"
WEEKLY_DIR = MEMORY_DIR / "weekly"
MONTHLY_DIR = MEMORY_DIR / "monthly"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pfad-Hilfsfunktionen
# ---------------------------------------------------------------------------

def _daily_path(d: date) -> Path:
    return DAILY_DIR / f"{d.isoformat()}.md"


def _weekly_path(d: date) -> Path:
    year, week, _ = d.isocalendar()
    return WEEKLY_DIR / f"{year}-W{week:02d}.md"


def _monthly_path(d: date) -> Path:
    return MONTHLY_DIR / f"{d.strftime('%Y-%m')}.md"


def _ensure_dirs() -> None:
    for d in (DAILY_DIR, WEEKLY_DIR, MONTHLY_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Lesen: Gedächtnis für Claude-Kontext
# ---------------------------------------------------------------------------

def get_memory_context() -> str:
    """
    Lädt die relevanten Zusammenfassungen für den Claude-Kontext:
    - Letzte 3 Tages-Zusammenfassungen
    - Aktuelle Wochen-Zusammenfassung (falls vorhanden)
    - Letzte Monats-Zusammenfassung (falls vorhanden)
    """
    today = date.today()
    sections: list[str] = []

    # Letzte 3 Tages-Zusammenfassungen (exkl. heute, der läuft noch)
    daily_summaries = []
    for i in range(1, 8):  # bis zu 7 Tage zurückgehen, max. 3 finden
        d = today - timedelta(days=i)
        p = _daily_path(d)
        if p.exists():
            daily_summaries.append((d, p.read_text(encoding="utf-8")))
        if len(daily_summaries) == 3:
            break

    if daily_summaries:
        sections.append("### Letzte Tage (Zusammenfassungen)")
        for d, summary in daily_summaries:
            sections.append(f"**{d.strftime('%A, %d.%m.%Y')}**\n{summary.strip()}")

    # Aktuelle Wochen-Zusammenfassung
    weekly_path = _weekly_path(today)
    if weekly_path.exists():
        year, week, _ = today.isocalendar()
        sections.append(f"### Diese Woche (KW {week})\n{weekly_path.read_text(encoding='utf-8').strip()}")

    # Letzte Monats-Zusammenfassung (Vormonat)
    first_of_month = today.replace(day=1)
    last_month = first_of_month - timedelta(days=1)
    monthly_path = _monthly_path(last_month)
    if monthly_path.exists():
        sections.append(
            f"### Letzter Monat ({last_month.strftime('%B %Y')})\n"
            f"{monthly_path.read_text(encoding='utf-8').strip()}"
        )

    if not sections:
        return "Noch keine Zusammenfassungen vorhanden (beginnt nach dem ersten Tag)."

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Claude-Client (identisch zu analyzer.py / coach.py)
# ---------------------------------------------------------------------------

def _build_client() -> tuple[anthropic.AsyncAnthropic, str]:
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


async def _call_claude(prompt: str, max_tokens: int = 1024) -> str:
    client, model = _build_client()
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Zusammenfassungen erstellen
# ---------------------------------------------------------------------------

async def summarize_day(
    conversation_history: list[dict],
    ref_date: date | None = None,
) -> str:
    """
    Erstellt eine Tages-Zusammenfassung aus dem Gesprächsverlauf
    und speichert sie in data/memory/daily/YYYY-MM-DD.md.
    Gibt die Zusammenfassung zurück.
    """
    _ensure_dirs()
    ref_date = ref_date or date.today()
    path = _daily_path(ref_date)

    if not conversation_history:
        logger.info("Kein Gesprächsverlauf für %s – keine Tages-Zusammenfassung.", ref_date)
        return ""

    # Gesprächsverlauf als lesbaren Text formatieren
    convo_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Coach'}: {_extract_text(m['content'])}"
        for m in conversation_history
        if not str(_extract_text(m['content'])).startswith("[Proaktiver")
    )

    if not convo_text.strip():
        return ""

    # Bestehende Zusammenfassung laden falls schon teilweise vorhanden
    existing = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    existing_block = f"\n\nBisherige Zusammenfassung des heutigen Tages (aktualisieren):\n{existing}" if existing else ""

    prompt = f"""Du bist ein Gedächtnis-Assistent für einen persönlichen Gesundheitscoach-Bot.

Erstelle eine kompakte Zusammenfassung des heutigen Gesprächs ({ref_date.strftime('%d.%m.%Y')}).
Die Zusammenfassung dient als Gedächtnis für zukünftige Gespräche.{existing_block}

## Heutiges Gespräch
{convo_text}

## Anweisungen
Schreibe eine strukturierte Zusammenfassung auf Deutsch mit diesen Abschnitten (nur ausfüllen was relevant ist):
- **Stimmung & Energie:** Wie wirkte der User heute?
- **Fortschritte:** Was hat er positives berichtet oder erreicht?
- **Schwierigkeiten:** Womit hat er gekämpft oder was lief nicht so gut?
- **Ernährung:** Was wurde gegessen / geplant?
- **Aktivität:** Sport, Bewegung, Schlaf?
- **Wichtige Erwähnungen:** Besondere Themen, Entscheidungen oder Ereignisse?
- **Für den Coach:** Was sollte der Coach beim nächsten Gespräch aufgreifen?

Schreibe knapp und präzise. Keine Floskeln."""

    summary = await _call_claude(prompt, max_tokens=800)
    path.write_text(summary, encoding="utf-8")
    logger.info("Tages-Zusammenfassung gespeichert: %s (%d Zeichen)", path.name, len(summary))
    return summary


async def summarize_week(ref_date: date | None = None) -> str:
    """
    Erstellt eine Wochen-Zusammenfassung aus den 7 Tages-Summaries
    und speichert sie in data/memory/weekly/YYYY-WXX.md.
    """
    _ensure_dirs()
    ref_date = ref_date or date.today()
    path = _weekly_path(ref_date)

    # Montag der aktuellen Woche
    monday = ref_date - timedelta(days=ref_date.weekday())
    daily_texts: list[str] = []

    for i in range(7):
        d = monday + timedelta(days=i)
        p = _daily_path(d)
        if p.exists():
            daily_texts.append(f"**{d.strftime('%A, %d.%m.')}**\n{p.read_text(encoding='utf-8').strip()}")

    if not daily_texts:
        logger.info("Keine Tages-Summaries für KW %d – keine Wochen-Zusammenfassung.", ref_date.isocalendar()[1])
        return ""

    year, week, _ = ref_date.isocalendar()
    prompt = f"""Du bist ein Gedächtnis-Assistent für einen persönlichen Gesundheitscoach-Bot.

Erstelle eine Wochen-Zusammenfassung für KW {week} ({year}) auf Basis der Tages-Zusammenfassungen.

## Tages-Zusammenfassungen
{chr(10).join(daily_texts)}

## Anweisungen
Schreibe eine kompakte Wochen-Zusammenfassung auf Deutsch:
- **Wochentrend:** Wie war die Woche insgesamt?
- **Größte Erfolge:** Was lief besonders gut?
- **Wiederkehrende Schwierigkeiten:** Welche Muster zeigen sich?
- **Ziele & Gewohnheiten:** Was wurde konsequent umgesetzt, was nicht?
- **Für die nächste Woche:** Worauf sollte der Coach besonders achten?

Prägnant, keine Wiederholungen der Einzeltage."""

    summary = await _call_claude(prompt, max_tokens=600)
    path.write_text(summary, encoding="utf-8")
    logger.info("Wochen-Zusammenfassung gespeichert: %s", path.name)
    return summary


async def summarize_month(ref_date: date | None = None) -> str:
    """
    Erstellt eine Monats-Zusammenfassung aus den Wochen-Summaries
    und speichert sie in data/memory/monthly/YYYY-MM.md.
    """
    _ensure_dirs()
    ref_date = ref_date or date.today()
    path = _monthly_path(ref_date)

    # Alle Wochen-Summaries des Monats sammeln
    weekly_texts: list[str] = []
    first_day = ref_date.replace(day=1)
    last_day  = ref_date.replace(day=calendar.monthrange(ref_date.year, ref_date.month)[1])
    d = first_day
    seen_weeks: set[str] = set()

    while d <= last_day:
        wp = _weekly_path(d)
        if wp.name not in seen_weeks and wp.exists():
            year, week, _ = d.isocalendar()
            weekly_texts.append(f"**KW {week}**\n{wp.read_text(encoding='utf-8').strip()}")
            seen_weeks.add(wp.name)
        d += timedelta(days=7)

    if not weekly_texts:
        logger.info("Keine Wochen-Summaries für %s – keine Monats-Zusammenfassung.", ref_date.strftime("%Y-%m"))
        return ""

    prompt = f"""Du bist ein Gedächtnis-Assistent für einen persönlichen Gesundheitscoach-Bot.

Erstelle eine Monats-Zusammenfassung für {ref_date.strftime('%B %Y')} auf Basis der Wochen-Zusammenfassungen.

## Wochen-Zusammenfassungen
{chr(10).join(weekly_texts)}

## Anweisungen
Schreibe eine kompakte Monats-Zusammenfassung auf Deutsch:
- **Monatsrückblick:** Wie war der Monat insgesamt?
- **Wichtigste Fortschritte:** Was hat sich langfristig verbessert?
- **Hartnäckige Herausforderungen:** Was bleibt schwierig?
- **Entwicklung der Gewohnheiten:** Welche Habits haben sich gefestigt oder sind gescheitert?
- **Für den nächsten Monat:** Welche Schwerpunkte sollte der Coach setzen?

Prägnant und auf das Wesentliche reduziert."""

    summary = await _call_claude(prompt, max_tokens=500)
    path.write_text(summary, encoding="utf-8")
    logger.info("Monats-Zusammenfassung gespeichert: %s", path.name)
    return summary


# ---------------------------------------------------------------------------
# Hilfsfunktion
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    """Extrahiert Text aus verschiedenen Message-Content-Formaten."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return str(content)


def should_summarize_week(ref_date: date | None = None) -> bool:
    return (ref_date or date.today()).weekday() == 6  # Sonntag


def should_summarize_month(ref_date: date | None = None) -> bool:
    d = ref_date or date.today()
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.day == last_day
