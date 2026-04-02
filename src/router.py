"""
router.py – Intelligentes Routing für Model, Kontext-Tiefe und Tool-Auswahl

Klassifiziert jede Nachricht/Check-in und bestimmt:
  - Welches Modell (Haiku vs. Sonnet)
  - Welcher Kontext-Tier (minimal / habits / diet / full)
  - Welche Tools benötigt werden

Stufen:
  1. Keyword-Matching (synchron, kostenlos, <1ms)
  2. LLM-Fallback via Haiku (~50 Tokens, <200ms) – nur wenn Keywords nichts finden
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

ModelChoice = Literal["haiku", "sonnet"]
ContextTier = Literal["minimal", "habits", "diet", "full"]
ToolSet = Literal["none", "habits", "diet", "all"]


@dataclass
class RouteDecision:
    model: ModelChoice
    context_tier: ContextTier
    tool_set: ToolSet
    reason: str  # für Logging/Debugging


# ---------------------------------------------------------------------------
# Keyword-basierte Klassifikation (schnell, kein LLM nötig)
# ---------------------------------------------------------------------------

_HABIT_KEYWORDS = [
    r"\bjogge", r"\blaufe", r"\bgelaufen", r"\bjoggen",
    r"\bkrafttraining", r"\bgym", r"\btraining", r"\bsport",
    r"\byoga", r"\bdehnen", r"\bmeditation",
    r"\bbüro", r"\boffice",
    r"\bmagnesium", r"\bvitamin",
    r"\bzigarette", r"\bgeraucht", r"\brauchen", r"\bgeschlafen",
    r"\bschlaf", r"\bmitternacht",
    r"\baufgeräumt", r"\breparatur", r"\bhausaufgabe",
    r"\bwasser.*getrunken", r"\bliter.*getrunken", r"\bgetrunken.*liter",
    r"\bhabit", r"\bgewohnheit", r"\bstreak",
]

_DIET_KEYWORDS = [
    r"\bgegessen", r"\besse\b", r"\bessen\b", r"\bmahlzeit",
    r"\bfrühstück", r"\bmittagessen", r"\babendessen", r"\bsnack",
    r"\bkochen", r"\bgekocht",
    r"\bdöner", r"\bpizza", r"\bburger", r"\bfastfood",
    r"\bkalorien", r"\bernährung", r"\bdiät",
    r"\brezept", r"\blebensmittel",
    r"\bcheat",
]

_STATUS_KEYWORDS = [
    r"\bwie läuft", r"\bwochenstand", r"\bfortschritt",
    r"\bstatus", r"\bübersicht", r"\bwie steh",
    r"\bwas fehlt", r"\bwas ist offen", r"\bwas muss ich noch",
    r"\bwie war", r"\brückblick", r"\bzusammenfassung",
]

_SIMPLE_PATTERNS = [
    r"^(ok|okay|danke|thx|thanks|ja|nein|ne|nö|klar|super|gut|cool|top|nice|alles klar|passt|mach ich|wird gemacht|gute nacht|guten morgen|bis später|👍|💪|🙏|❤️|😊)\s*[.!]?\s*$",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


# ---------------------------------------------------------------------------
# Routing-Logik
# ---------------------------------------------------------------------------

def route_checkin(check_type: str) -> RouteDecision:
    """Routing für proaktive Check-ins (morning/noon/evening)."""
    if check_type == "evening":
        decision = RouteDecision(
            model="sonnet",
            context_tier="full",
            tool_set="all",
            reason="Abend-Reflexion braucht vollen Kontext",
        )
    elif check_type == "morning":
        decision = RouteDecision(
            model="haiku",
            context_tier="full",
            tool_set="habits",
            reason="Morgen-Check-in: braucht Tagesplan + geplante Mahlzeiten",
        )
    else:
        decision = RouteDecision(
            model="haiku",
            context_tier="habits",
            tool_set="habits",
            reason="Mittags-Check-in: Habit-Fokus",
        )
    logger.info("Check-in-Routing [%s] → model=%s, tools=%s (%s)", check_type, decision.model, decision.tool_set, decision.reason)
    return decision


# ---------------------------------------------------------------------------
# LLM-Fallback-Klassifikation (Haiku, ~50 Input-Tokens)
# ---------------------------------------------------------------------------

_CLASSIFY_PROMPT = """Klassifiziere diese Nachricht eines Gesundheits-Coaching-Klienten in EINE Kategorie.
Antworte NUR mit einem einzelnen Wort.

Kategorien:
- simple (Bestätigung, Gruß, kurze Reaktion ohne Inhalt – NUR wenn kein Kontext auf eine Aktion hindeutet)
- habit (Sport, Bewegung, Schlaf, Gewohnheiten, Supplements, Rauchen)
- diet (Essen, Trinken, Mahlzeiten, Ernährung, Kochen)
- status (Frage nach Fortschritt, Wochenstand, Übersicht)
- mixed (mehrere Kategorien oder komplexe Frage)

{context_block}Nachricht: "{text}"
Kategorie:"""

_CLASSIFY_PROMPT_CONTEXT_BLOCK = """Vorherige Antwort des Coaches (Kontext):
"{last_assistant}"

"""

_CLASSIFY_TO_ROUTE: dict[str, RouteDecision] = {
    "simple": RouteDecision(
        model="haiku", context_tier="minimal", tool_set="none",
        reason="LLM-Klassifikation: simple",
    ),
    "habit": RouteDecision(
        model="haiku", context_tier="habits", tool_set="habits",
        reason="LLM-Klassifikation: habit",
    ),
    "diet": RouteDecision(
        model="haiku", context_tier="diet", tool_set="diet",
        reason="LLM-Klassifikation: diet",
    ),
    "status": RouteDecision(
        model="sonnet", context_tier="full", tool_set="all",
        reason="LLM-Klassifikation: status",
    ),
    "mixed": RouteDecision(
        model="sonnet", context_tier="full", tool_set="all",
        reason="LLM-Klassifikation: mixed",
    ),
}

_DEFAULT_ROUTE = RouteDecision(
    model="haiku", context_tier="habits", tool_set="habits",
    reason="Default-Routing (Fallback)",
)


async def _classify_with_llm(text: str, last_assistant: str | None = None) -> RouteDecision:
    """Fragt Haiku nach der Kategorie. Fallback auf Default bei Fehler."""
    try:
        from .coach import _build_client, _resolve_model

        client = _build_client()
        model = _resolve_model("haiku")

        context_block = (
            _CLASSIFY_PROMPT_CONTEXT_BLOCK.format(last_assistant=last_assistant[:300])
            if last_assistant
            else ""
        )
        prompt = _CLASSIFY_PROMPT.format(text=text, context_block=context_block)

        response = await client.messages.create(
            model=model,
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        category = response.content[0].text.strip().lower()
        route = _CLASSIFY_TO_ROUTE.get(category, _DEFAULT_ROUTE)
        logger.info("LLM-Klassifikation: '%s' → %s", text[:50], category)
        return route
    except Exception:
        logger.warning("LLM-Klassifikation fehlgeschlagen, nutze Default.")
        return _DEFAULT_ROUTE


# ---------------------------------------------------------------------------
# Routing-Logik (Haupt-Einstiegspunkt für User-Nachrichten)
# ---------------------------------------------------------------------------

def route_user_message(text: str) -> RouteDecision:
    """Synchrones Routing via Keywords. Gibt None-Marker zurück wenn LLM nötig."""
    return _route_by_keywords(text)


async def route_user_message_async(
    text: str, last_assistant: str | None = None
) -> RouteDecision:
    """Async Routing: Keywords zuerst, bei Unsicherheit LLM-Fallback."""
    result = _route_by_keywords(text)
    if result.reason != "_needs_llm":
        logger.info("User-Routing (keyword) '%s…' → model=%s, tools=%s (%s)", text[:40], result.model, result.tool_set, result.reason)
        return result
    return await _classify_with_llm(text, last_assistant=last_assistant)


def _route_by_keywords(text: str) -> RouteDecision:
    """Keyword-basiertes Routing. Gibt _needs_llm als Marker zurück wenn unklar."""
    text_stripped = text.strip()

    # 1. Einfache Antworten → Haiku, minimal, keine Tools
    if _matches_any(text_stripped, _SIMPLE_PATTERNS):
        return RouteDecision(
            model="haiku",
            context_tier="minimal",
            tool_set="none",
            reason="Einfache Antwort, kein Tool nötig",
        )

    # 2. Status-/Übersichtsfragen → Sonnet, full
    if _matches_any(text_stripped, _STATUS_KEYWORDS):
        return RouteDecision(
            model="sonnet",
            context_tier="full",
            tool_set="all",
            reason="Status-Frage, voller Kontext",
        )

    # 3. Habit-bezogen → Haiku, habits-fokus
    has_habit = _matches_any(text_stripped, _HABIT_KEYWORDS)
    has_diet = _matches_any(text_stripped, _DIET_KEYWORDS)

    if has_habit and has_diet:
        return RouteDecision(
            model="sonnet",
            context_tier="full",
            tool_set="all",
            reason="Habits + Diet gemischt",
        )

    if has_habit:
        return RouteDecision(
            model="haiku",
            context_tier="habits",
            tool_set="habits",
            reason="Habit-bezogene Nachricht",
        )

    if has_diet:
        return RouteDecision(
            model="haiku",
            context_tier="diet",
            tool_set="diet",
            reason="Ernährungs-bezogene Nachricht",
        )

    # 4. Längere Nachrichten (>150 Zeichen) → eher komplex → Sonnet
    if len(text_stripped) > 150:
        return RouteDecision(
            model="sonnet",
            context_tier="full",
            tool_set="all",
            reason="Lange Nachricht, vermutlich komplex",
        )

    # 5. Keine Keywords → LLM-Fallback nötig
    return RouteDecision(
        model="haiku",
        context_tier="habits",
        tool_set="habits",
        reason="_needs_llm",
    )


def route_analysis() -> RouteDecision:
    """Routing für die Nacht-Analyse."""
    return RouteDecision(
        model="sonnet",
        context_tier="full",
        tool_set="none",
        reason="Nacht-Analyse braucht vollen Kontext",
    )


def route_memory_summary() -> RouteDecision:
    """Routing für Gedächtnis-Zusammenfassungen."""
    return RouteDecision(
        model="haiku",
        context_tier="minimal",
        tool_set="none",
        reason="Zusammenfassung: Haiku reicht",
    )
