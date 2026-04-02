"""
coach.py – Kontext-Aufbau und Claude-API-Integration
"""

import os
from datetime import datetime, date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
import yaml

from .habits import (
    add_habit,
    delete_habit_confirmed,
    done_habit,
    edit_habit,
    list_habits,
)
from .habit_tracker import (
    get_habit_context,
    get_habits_status_text,
    get_planned_habits,
    list_habit_entries,
    delete_habit_entry,
    edit_habit_entry,
    log_habit,
    mark_planned_habit_done,
    plan_habit,
    delete_planned_habit,
)
from .diet import (
    get_diet_context,
    get_plan_vs_diary_summary,
    get_planned_meals,
    get_recent_diary,
    get_todays_plan_for_context,
    check_meal_plan_conflicts,
    log_meal,
    plan_meal,
    update_target_met,
    list_diary_entries,
    delete_diary_entry,
    edit_diary_entry,
    delete_planned_meal,
    edit_planned_meal,
)

DATA_DIR = Path(__file__).parent.parent / "data"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

CheckType = Literal["morning", "noon", "evening", "user_reply"]


def _load_file(filename: str) -> str:
    path = DATA_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"[Datei {filename} nicht gefunden]"




def _load_goals_summary() -> str:
    goals_path = DATA_DIR / "goals.yaml"
    if not goals_path.exists():
        return "[goals.yaml nicht gefunden]"
    with open(goals_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    goals = data.get("goals", [])
    lines = []
    for g in goals:
        lines.append(
            f"- [{g.get('priority', '').upper()}] {g.get('title')} "
            f"(Zieldatum: {g.get('target_date')}, Streak: {g.get('current_streak', 0)} Tage)"
        )
    return "\n".join(lines) if lines else "Keine Ziele definiert."


def build_context() -> str:
    tz = ZoneInfo("Europe/Berlin")
    now_local = datetime.now(tz)
    today = now_local.strftime("%d.%m.%Y")
    time_str = now_local.strftime("%H:%M")
    weekday = now_local.strftime("%A")
    weekday_de = {
        "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
        "Thursday": "Donnerstag", "Friday": "Freitag",
        "Saturday": "Samstag", "Sunday": "Sonntag",
    }.get(weekday, weekday)

    from .memory import get_memory_context

    health_profile = _load_file("health_profile.md")
    goals_summary  = _load_goals_summary()
    habit_context  = get_habit_context()
    diet_context   = get_diet_context()
    memory_context = get_memory_context()

    return f"""## Aktuelles Datum & Uhrzeit (Europe/Berlin)
{weekday_de}, {today}, {time_str} Uhr

## Gesundheitsprofil des Klienten
{health_profile}

## Ziele (Überblick)
{goals_summary}

## Habit-Tracking (Wochenstand)
{habit_context}

## Ernährung
{diet_context}

## Gedächtnis (vergangene Gespräche)
{memory_context}
"""


def get_check_label(check_type: CheckType) -> str:
    labels = {
        "morning": "Morgen-Check-in (07:00 Uhr)",
        "noon": "Mittags-Check-in (13:00 Uhr)",
        "evening": "Abend-Check-in (20:00 Uhr)",
        "user_reply": "Antwort des Nutzers",
    }
    return labels.get(check_type, check_type)


# ---------------------------------------------------------------------------
# Tool-Definitionen für Habit-Management (Claude Function Calling)
# ---------------------------------------------------------------------------

HABIT_TOOLS = [
    {
        "name": "list_habits",
        "description": "Listet alle Gewohnheiten und Ziele des Nutzers mit ihren Nummern auf. Vor edit_habit aufrufen, um die richtige Nummer zu ermitteln.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_habit",
        "description": "Fügt eine neue Gewohnheit oder ein neues Ziel hinzu.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titel der neuen Gewohnheit"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "edit_habit",
        "description": (
            "Bearbeitet ein Feld einer bestehenden Gewohnheit. "
            "Erlaubte Felder: title, description, category, target_date (YYYY-MM-DD), "
            "priority (hoch/mittel/niedrig), notes, target_value, target_unit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Nummer der Gewohnheit (1-basiert)"},
                "field": {"type": "string", "description": "Feldname"},
                "value": {"type": "string", "description": "Neuer Wert"},
            },
            "required": ["index", "field", "value"],
        },
    },
    {
        "name": "done_habit",
        "description": "Markiert eine Gewohnheit als heute erledigt und erhöht den Streak um 1.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Nummer der Gewohnheit (1-basiert)"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "log_habit_completion",
        "description": (
            "Trägt eine erledigte Habit-Ausführung in habit_tracking.csv ein. "
            "Aufrufen wenn der User berichtet, dass er ein Goal umgesetzt hat "
            "(z.B. 'War heute joggen', 'Habe Magnesium genommen', 'Keine Zigarette heute'). "
            "goal_id aus goals.yaml verwenden (goal_001, goal_002, ...). "
            "WICHTIG für value: "
            "Bei Goals in 'einheiten/woche' oder 'tage/woche' (z.B. Joggen, Gym, Yoga, Büro, Haushalt, Magnesium, Schlaf) "
            "immer value=1 setzen – die Anzahl der Einheiten wird automatisch aus der Anzahl der Einträge berechnet. "
            "Distanz (km), Dauer (Minuten) o.ä. gehören in notes, NICHT in value. "
            "Nur bei 'liter/tag' oder 'zigaretten/tag' den tatsächlichen Messwert als value eintragen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":    {"type": "string", "description": "ID aus goals.yaml (z.B. goal_001)"},
                "value":      {"type": "number", "description": "Für einheiten/woche und tage/woche: immer 1. Für liter/tag oder zigaretten/tag: tatsächlicher Wert."},
                "notes":      {"type": "string", "description": "Optionale Notiz – hier Distanz, Dauer etc. eintragen (z.B. '3km, 30 Minuten')"},
                "entry_date": {"type": "string", "description": "Datum YYYY-MM-DD, leer = heute"},
                "force":      {"type": "boolean", "description": "true = Duplikat trotzdem eintragen, nur nach expliziter Nutzerbestätigung"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "get_habit_status",
        "description": "Gibt den aktuellen Wochenstand aller Goals zurück. Aufrufen wenn der User nach seinem Fortschritt fragt.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_habit_entries",
        "description": "Listet bestehende Tracking-Einträge auf, gefiltert nach goal_id und/oder Datum. Aufrufen bevor delete/edit, damit der User weiß welche Einträge existieren.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":    {"type": "string", "description": "Goal-ID filtern (optional)"},
                "entry_date": {"type": "string", "description": "Datum YYYY-MM-DD filtern (optional)"},
            },
            "required": [],
        },
    },
    {
        "name": "delete_habit_entry",
        "description": "Löscht einen einzelnen Tracking-Eintrag. Bei Duplikaten occurrence=1 für den zweiten Eintrag angeben.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":    {"type": "string", "description": "Goal-ID des Eintrags"},
                "entry_date": {"type": "string", "description": "Datum YYYY-MM-DD"},
                "occurrence": {"type": "integer", "description": "0-basierter Index bei mehreren Einträgen (Standard: 0)"},
            },
            "required": ["goal_id", "entry_date"],
        },
    },
    {
        "name": "edit_habit_entry",
        "description": "Ändert value oder notes eines bestehenden Tracking-Eintrags.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":    {"type": "string", "description": "Goal-ID des Eintrags"},
                "entry_date": {"type": "string", "description": "Datum YYYY-MM-DD"},
                "new_value":  {"type": "number",  "description": "Neuer Wert (optional)"},
                "new_notes":  {"type": "string",  "description": "Neue Notiz (optional)"},
                "occurrence": {"type": "integer", "description": "0-basierter Index bei mehreren Einträgen (Standard: 0)"},
            },
            "required": ["goal_id", "entry_date"],
        },
    },
    {
        "name": "plan_habit",
        "description": (
            "Plant eine Habit-Ausführung für ein zukünftiges Datum. Aufrufen wenn der User sagt er "
            "plant etwas zu tun (z.B. 'Ich plane morgen zu joggen', 'Ich will Mittwoch ins Gym'). "
            "Standard: morgen, wenn kein Datum angegeben."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":      {"type": "string", "description": "Goal-ID aus goals.yaml"},
                "planned_date": {"type": "string", "description": "Datum YYYY-MM-DD (Standard: morgen)"},
                "notes":        {"type": "string", "description": "Optionale Notiz"},
                "force":        {"type": "boolean", "description": "true = Duplikat trotzdem eintragen, nur nach expliziter Nutzerbestätigung"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "get_planned_habits",
        "description": "Zeigt geplante Habits für ein bestimmtes Datum. Aufrufen wenn der User fragt was er geplant hat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "planned_date": {"type": "string", "description": "Datum YYYY-MM-DD (Standard: heute)"},
            },
            "required": [],
        },
    },
    {
        "name": "mark_planned_habit_done",
        "description": (
            "Markiert einen geplanten Habit als erledigt oder nicht erledigt. "
            "Aufrufen wenn der User bestätigt dass er einen geplanten Habit umgesetzt hat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":      {"type": "string", "description": "Goal-ID"},
                "planned_date": {"type": "string", "description": "Datum YYYY-MM-DD"},
                "done":         {"type": "boolean", "description": "true = erledigt, false = nicht erledigt"},
            },
            "required": ["goal_id", "planned_date", "done"],
        },
    },
    {
        "name": "delete_planned_habit",
        "description": "Löscht einen geplanten Habit-Eintrag aus planned_habits.csv (z.B. wenn ein Plan storniert wird).",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id":      {"type": "string", "description": "Goal-ID (z.B. goal_001)"},
                "planned_date": {"type": "string", "description": "Datum YYYY-MM-DD"},
            },
            "required": ["goal_id", "planned_date"],
        },
    },
    {
        "name": "delete_habit",
        "description": "Löscht eine Gewohnheit dauerhaft. Nur aufrufen, wenn der Nutzer dies ausdrücklich bestätigt hat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Nummer der Gewohnheit (1-basiert)"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "log_meal",
        "description": (
            "Loggt eine Mahlzeit ins Ernährungstagebuch und prüft automatisch ob Cheat-Limits verletzt werden. "
            "Immer aufrufen wenn der Nutzer eine konkrete Mahlzeit nennt (geplant oder bereits gegessen). "
            "meal_type: breakfast, lunch, dinner, snack, other. "
            "tags: kommagetrennte Schlagwörter z.B. 'fastfood,döner' oder 'vegetarisch,protein'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_type":   {"type": "string", "description": "breakfast | lunch | dinner | snack | other"},
                "description": {"type": "string", "description": "Beschreibung der Mahlzeit"},
                "tags":        {"type": "string", "description": "Kommagetrennte Tags (z.B. 'fastfood,döner')"},
                "notes":       {"type": "string", "description": "Optionale Notizen"},
                "meal_date":   {"type": "string", "description": "Datum YYYY-MM-DD, leer = heute"},
                "force":       {"type": "boolean", "description": "true = Duplikat trotzdem eintragen, nur nach expliziter Nutzerbestätigung"},
            },
            "required": ["meal_type", "description"],
        },
    },
    {
        "name": "get_diet_diary",
        "description": "Gibt das Ernährungstagebuch der letzten N Tage zurück. Nützlich um nachzuschauen was der Nutzer zuletzt gegessen hat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Anzahl Tage (Standard: 7)"},
            },
            "required": [],
        },
    },
    {
        "name": "check_meal_plan_conflicts",
        "description": (
            "Prüft ob eine geplante Mahlzeit gegen Cheat-Limits oder Ernährungsziele verstößt. "
            "IMMER vor plan_meal aufrufen. "
            "Gibt leeren String zurück wenn alles ok. "
            "Gibt Warnungstext zurück wenn Konflikt – dann den User fragen ob er es trotzdem einplanen will. "
            "plan_meal nur aufrufen wenn User explizit bestätigt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Beschreibung der geplanten Mahlzeit"},
                "tags":        {"type": "string", "description": "Kommagetrennte Tags"},
                "meal_date":   {"type": "string", "description": "Datum YYYY-MM-DD, leer = heute"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "plan_meal",
        "description": (
            "Trägt eine Mahlzeit in den Mahlzeitenplan (planned_meals.csv) ein. "
            "Nur aufrufen NACHDEM check_meal_plan_conflicts keine Konflikte gemeldet hat "
            "ODER der User Konflikte explizit bestätigt hat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_type":   {"type": "string", "description": "breakfast | lunch | dinner | snack | other"},
                "description": {"type": "string", "description": "Beschreibung der Mahlzeit"},
                "tags":        {"type": "string", "description": "Kommagetrennte Tags"},
                "notes":       {"type": "string", "description": "Optionale Notizen"},
                "meal_date":   {"type": "string", "description": "Datum YYYY-MM-DD, leer = heute"},
            },
            "required": ["meal_type", "description"],
        },
    },
    {
        "name": "get_planned_meals",
        "description": "Gibt den Mahlzeitenplan für ein bestimmtes Datum zurück.",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date": {"type": "string", "description": "Datum YYYY-MM-DD, leer = heute"},
            },
            "required": [],
        },
    },
    {
        "name": "update_target_met",
        "description": (
            "Markiert im Mahlzeitenplan ob eine geplante Mahlzeit tatsächlich eingehalten wurde. "
            "Beim Abend-Check-in aufrufen nachdem der Nutzer berichtet was er gegessen hat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date":  {"type": "string", "description": "Datum YYYY-MM-DD"},
                "meal_type":  {"type": "string", "description": "breakfast | lunch | dinner | snack | other"},
                "met":        {"type": "boolean", "description": "true = Ziel eingehalten, false = nicht eingehalten"},
            },
            "required": ["meal_date", "meal_type", "met"],
        },
    },
    {
        "name": "get_plan_vs_diary",
        "description": (
            "Vergleicht den Mahlzeitenplan mit dem tatsächlichen Tagebuch für ein Datum. "
            "Beim Abend-Check-in aufrufen um den Tagesabschluss zu bewerten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date": {"type": "string", "description": "Datum YYYY-MM-DD, leer = heute"},
            },
            "required": [],
        },
    },
    {
        "name": "list_diary_entries",
        "description": (
            "Listet Einträge aus dem Ernährungstagebuch auf, mit Index für delete/edit. "
            "IMMER aufrufen bevor delete_diary_entry oder edit_diary_entry, "
            "damit klar ist welcher Eintrag gemeint ist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date": {"type": "string", "description": "Datum YYYY-MM-DD (optional, leer = alle)"},
                "meal_type": {"type": "string", "description": "breakfast|lunch|dinner|snack|other (optional)"},
            },
            "required": [],
        },
    },
    {
        "name": "delete_diary_entry",
        "description": (
            "Löscht einen Eintrag aus dem Ernährungstagebuch. "
            "Vorher list_diary_entries aufrufen. "
            "Bei mehreren gleichen Einträgen: occurrence=0 für ersten, 1 für zweiten."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date": {"type": "string", "description": "Datum YYYY-MM-DD"},
                "meal_type": {"type": "string", "description": "breakfast|lunch|dinner|snack|other"},
                "occurrence": {"type": "integer", "description": "0-basierter Index (Standard: 0)"},
            },
            "required": ["meal_date", "meal_type"],
        },
    },
    {
        "name": "edit_diary_entry",
        "description": (
            "Ändert einen bestehenden Tagebucheintrag – z.B. meal_type von dinner auf lunch korrigieren. "
            "Effizienter als löschen + neu anlegen. Vorher list_diary_entries aufrufen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date":       {"type": "string", "description": "Datum YYYY-MM-DD"},
                "meal_type":       {"type": "string", "description": "Bisheriger meal_type"},
                "new_meal_type":   {"type": "string", "description": "Neuer meal_type (optional)"},
                "new_description": {"type": "string", "description": "Neue Beschreibung (optional)"},
                "new_tags":        {"type": "string", "description": "Neue Tags kommagetrennt (optional)"},
                "occurrence":      {"type": "integer", "description": "0-basierter Index (Standard: 0)"},
            },
            "required": ["meal_date", "meal_type"],
        },
    },
    {
        "name": "delete_planned_meal",
        "description": "Löscht einen geplanten Mahlzeiteintrag aus planned_meals.csv (z.B. Plan geändert oder storniert).",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date":  {"type": "string",  "description": "Datum YYYY-MM-DD"},
                "meal_type":  {"type": "string",  "description": "breakfast|lunch|dinner|snack|other"},
                "occurrence": {"type": "integer", "description": "0-basierter Index bei mehreren gleichen (Standard: 0)"},
            },
            "required": ["meal_date", "meal_type"],
        },
    },
    {
        "name": "edit_planned_meal",
        "description": "Ändert einen geplanten Mahlzeiteintrag (z.B. Beschreibung oder meal_type korrigieren).",
        "input_schema": {
            "type": "object",
            "properties": {
                "meal_date":       {"type": "string",  "description": "Datum YYYY-MM-DD"},
                "meal_type":       {"type": "string",  "description": "Bisheriger meal_type"},
                "new_meal_type":   {"type": "string",  "description": "Neuer meal_type (optional)"},
                "new_description": {"type": "string",  "description": "Neue Beschreibung (optional)"},
                "new_tags":        {"type": "string",  "description": "Neue Tags kommagetrennt (optional)"},
                "occurrence":      {"type": "integer", "description": "0-basierter Index (Standard: 0)"},
            },
            "required": ["meal_date", "meal_type"],
        },
    },
]

_HABIT_TOOL_NAMES = {
    "list_habits", "add_habit", "edit_habit", "done_habit", "delete_habit",
    "log_habit_completion", "get_habit_status", "list_habit_entries",
    "delete_habit_entry", "edit_habit_entry", "plan_habit", "get_planned_habits",
    "mark_planned_habit_done", "delete_planned_habit",
}
_DIET_TOOL_NAMES = {
    "log_meal", "get_diet_diary", "check_meal_plan_conflicts",
    "plan_meal", "get_planned_meals", "update_target_met", "get_plan_vs_diary",
    "list_diary_entries", "delete_diary_entry", "edit_diary_entry",
    "delete_planned_meal", "edit_planned_meal",
}


def _filter_tools(tool_set: str) -> list[dict]:
    if tool_set == "none":
        return []
    if tool_set == "habits":
        return [t for t in HABIT_TOOLS if t["name"] in _HABIT_TOOL_NAMES]
    if tool_set == "diet":
        return [t for t in HABIT_TOOLS if t["name"] in _DIET_TOOL_NAMES]
    return HABIT_TOOLS  # "all"


def _build_client() -> anthropic.AsyncAnthropic:
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_api_key:
        return anthropic.AsyncAnthropic(
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api",
            default_headers={"HTTP-Referer": "health-coach-bot"},
        )
    return anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _resolve_model(choice: str) -> str:
    if choice == "haiku":
        base = os.environ.get("HAIKU_MODEL", HAIKU_MODEL)
    else:
        base = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
    if os.environ.get("OPENROUTER_API_KEY") and "/" not in base:
        return f"anthropic/{base}"
    return base


def _execute_tool(name: str, inputs: dict) -> str:
    if name == "list_habits":
        return list_habits()
    if name == "add_habit":
        return add_habit(inputs["title"])
    if name == "edit_habit":
        return edit_habit(inputs["index"], inputs["field"], str(inputs["value"]))
    if name == "done_habit":
        return done_habit(inputs["index"])
    if name == "delete_habit":
        return delete_habit_confirmed(inputs["index"])
    if name == "log_meal":
        return log_meal(
            meal_type=inputs.get("meal_type", "other"),
            description=inputs["description"],
            tags=inputs.get("tags", ""),
            notes=inputs.get("notes", ""),
            meal_date=inputs.get("meal_date", ""),
            force=bool(inputs.get("force", False)),
        )
    if name == "get_diet_diary":
        return get_recent_diary(days=inputs.get("days", 7))
    if name == "check_meal_plan_conflicts":
        return check_meal_plan_conflicts(
            description=inputs["description"],
            tags=inputs.get("tags", ""),
            meal_date=inputs.get("meal_date", ""),
        )
    if name == "plan_meal":
        return plan_meal(
            meal_type=inputs.get("meal_type", "other"),
            description=inputs["description"],
            tags=inputs.get("tags", ""),
            notes=inputs.get("notes", ""),
            meal_date=inputs.get("meal_date", ""),
        )
    if name == "get_planned_meals":
        return get_planned_meals(meal_date=inputs.get("meal_date", ""))
    if name == "update_target_met":
        return update_target_met(
            meal_date=inputs["meal_date"],
            meal_type=inputs["meal_type"],
            met=inputs["met"],
        )
    if name == "get_plan_vs_diary":
        return get_plan_vs_diary_summary(meal_date=inputs.get("meal_date", ""))
    if name == "list_diary_entries":
        return list_diary_entries(
            meal_date=inputs.get("meal_date", ""),
            meal_type=inputs.get("meal_type", ""),
        )
    if name == "delete_diary_entry":
        return delete_diary_entry(
            meal_date=inputs["meal_date"],
            meal_type=inputs["meal_type"],
            occurrence=int(inputs.get("occurrence", 0)),
        )
    if name == "edit_diary_entry":
        return edit_diary_entry(
            meal_date=inputs["meal_date"],
            meal_type=inputs["meal_type"],
            new_meal_type=inputs.get("new_meal_type"),
            new_description=inputs.get("new_description"),
            new_tags=inputs.get("new_tags"),
            occurrence=int(inputs.get("occurrence", 0)),
        )
    if name == "log_habit_completion":
        return log_habit(
            goal_id=inputs["goal_id"],
            value=float(inputs.get("value", 1)),
            notes=inputs.get("notes", ""),
            entry_date=inputs.get("entry_date", ""),
            force=bool(inputs.get("force", False)),
        )
    if name == "get_habit_status":
        return get_habits_status_text()
    if name == "list_habit_entries":
        return list_habit_entries(
            goal_id=inputs.get("goal_id", ""),
            entry_date=inputs.get("entry_date", ""),
        )
    if name == "delete_habit_entry":
        return delete_habit_entry(
            goal_id=inputs["goal_id"],
            entry_date=inputs["entry_date"],
            occurrence=int(inputs.get("occurrence", 0)),
        )
    if name == "edit_habit_entry":
        return edit_habit_entry(
            goal_id=inputs["goal_id"],
            entry_date=inputs["entry_date"],
            new_value=inputs.get("new_value"),
            new_notes=inputs.get("new_notes"),
            occurrence=int(inputs.get("occurrence", 0)),
        )
    if name == "plan_habit":
        return plan_habit(
            goal_id=inputs["goal_id"],
            planned_date=inputs.get("planned_date", ""),
            notes=inputs.get("notes", ""),
            force=bool(inputs.get("force", False)),
        )
    if name == "get_planned_habits":
        return get_planned_habits(inputs.get("planned_date", ""))
    if name == "mark_planned_habit_done":
        return mark_planned_habit_done(
            goal_id=inputs["goal_id"],
            planned_date=inputs["planned_date"],
            done=bool(inputs["done"]),
        )
    if name == "delete_planned_habit":
        return delete_planned_habit(
            goal_id=inputs["goal_id"],
            planned_date=inputs["planned_date"],
        )
    if name == "delete_planned_meal":
        return delete_planned_meal(
            meal_date=inputs["meal_date"],
            meal_type=inputs["meal_type"],
            occurrence=int(inputs.get("occurrence", 0)),
        )
    if name == "edit_planned_meal":
        return edit_planned_meal(
            meal_date=inputs["meal_date"],
            meal_type=inputs["meal_type"],
            new_meal_type=inputs.get("new_meal_type"),
            new_description=inputs.get("new_description"),
            new_tags=inputs.get("new_tags"),
            occurrence=int(inputs.get("occurrence", 0)),
        )
    return f"[Unbekanntes Tool: {name}]"


def build_user_message(check_type: CheckType, user_text: str | None = None) -> str:
    label = get_check_label(check_type)
    if check_type == "user_reply" and user_text:
        return f"[{label}]\n\nNachricht des Klienten:\n{user_text}"
    if check_type == "evening":
        return (
            f"[{label}]\n\n"
            "Bitte sende die Abend-Reflexion. Nutze dabei get_plan_vs_diary um den heutigen "
            "Mahlzeitenplan mit dem Tagebuch zu vergleichen. Falls geplante Mahlzeiten unklar sind, "
            "frage den Nutzer danach und aktualisiere anschließend target_met mit update_target_met."
        )
    return f"[{label}]\n\nBitte sende jetzt die passende proaktive Coach-Nachricht."


async def ask_claude(
    check_type: CheckType,
    user_text: str | None = None,
    conversation_history: list[dict] | None = None,
) -> str:
    from .router import route_checkin, route_user_message_async

    if check_type == "user_reply":
        last_assistant = next(
            (m["content"] for m in reversed(conversation_history or []) if m["role"] == "assistant"),
            None,
        )
        decision = await route_user_message_async(user_text or "", last_assistant=last_assistant)
    else:
        decision = route_checkin(check_type)

    client = _build_client()
    model = _resolve_model(decision.model)
    tools = _filter_tools(decision.tool_set)

    system_prompt = _load_file("system_prompt.md")
    context = build_context()
    full_system = f"{system_prompt}\n\n---\n\n## Aktueller Kontext\n{context}"

    messages: list[dict] = []
    if conversation_history:
        messages.extend(conversation_history)

    messages.append({
        "role": "user",
        "content": build_user_message(check_type, user_text),
    })

    # Tool-Use-Loop: max. 5 Runden (verhindert Endlosschleifen)
    for _ in range(5):
        kwargs: dict = dict(model=model, max_tokens=1024, system=full_system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        response = await client.messages.create(**kwargs)

        if response.stop_reason != "tool_use":
            # Finales Text-Ergebnis extrahieren
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        # Tool-Calls ausführen und Ergebnisse sammeln
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

    return "Entschuldigung, ich konnte die Anfrage nicht abschließen. Bitte versuche es nochmal."


