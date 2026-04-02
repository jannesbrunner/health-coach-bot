"""
habits.py – CRUD-Operationen für goals.yaml
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent.parent / "data"
GOALS_FILE = DATA_DIR / "goals.yaml"

PRIORITY_MAP = {"hoch": "hoch", "mittel": "mittel", "niedrig": "niedrig"}
EDITABLE_FIELDS = {
    "title":        "Titel",
    "description":  "Beschreibung",
    "category":     "Kategorie",
    "target_date":  "Zieldatum (YYYY-MM-DD)",
    "priority":     "Priorität (hoch/mittel/niedrig)",
    "notes":        "Notizen",
    "target_value": "Zielwert",
    "target_unit":  "Einheit",
}


# ---------------------------------------------------------------------------
# Interne Hilfsfunktionen
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    if not GOALS_FILE.exists():
        return []
    with open(GOALS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("goals", [])


def _save(goals: list[dict]) -> None:
    with open(GOALS_FILE, "w", encoding="utf-8") as f:
        yaml.dump({"goals": goals}, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _next_id(goals: list[dict]) -> str:
    existing = {g.get("id", "") for g in goals}
    i = 1
    while True:
        candidate = f"goal_{i:03d}"
        if candidate not in existing:
            return candidate
        i += 1


def _fmt_priority(p: str) -> str:
    return {"hoch": "🔴 hoch", "mittel": "🟡 mittel", "niedrig": "🟢 niedrig"}.get(p, p)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_habits() -> str:
    goals = _load()
    if not goals:
        return "Keine Gewohnheiten / Ziele vorhanden.\n\nMit `/habit add Titel` ein neues Ziel hinzufügen."

    lines = ["*Deine Gewohnheiten & Ziele:*\n"]
    for i, g in enumerate(goals, start=1):
        streak = g.get("current_streak", 0)
        streak_str = f"🔥 {streak} Tage" if streak > 0 else "kein Streak"
        lines.append(
            f"*[{i}] {g.get('title')}*\n"
            f"  Kategorie: {g.get('category', '–')} | {_fmt_priority(g.get('priority', '–'))}\n"
            f"  Zieldatum: {g.get('target_date', '–')} | {streak_str}\n"
            f"  {g.get('description', '')}"
        )
    lines.append(
        "\n_Befehle:_\n"
        "`/habit add Titel` – neu\n"
        "`/habit edit 1 title Neuer Titel` – bearbeiten\n"
        "`/habit done 1` – Streak erhöhen\n"
        "`/habit delete 1` – löschen\n"
        "`/habit fields` – editierbare Felder"
    )
    return "\n".join(lines)


def add_habit(title: str) -> str:
    goals = _load()
    new_goal = {
        "id":            _next_id(goals),
        "title":         title,
        "category":      "allgemein",
        "description":   "",
        "target_value":  None,
        "target_unit":   "",
        "start_date":    date.today().isoformat(),
        "target_date":   "",
        "priority":      "mittel",
        "current_streak": 0,
        "notes":         "",
    }
    goals.append(new_goal)
    _save(goals)
    idx = len(goals)
    return (
        f"*Neue Gewohnheit hinzugefügt* (Nr. {idx}):\n"
        f"_{title}_\n\n"
        f"Details ergänzen mit z.B.:\n"
        f"`/habit edit {idx} description Kurze Beschreibung`\n"
        f"`/habit edit {idx} target_date 2026-06-30`\n"
        f"`/habit edit {idx} priority hoch`"
    )


def edit_habit(index: int, field: str, value: str) -> str:
    goals = _load()
    if not 1 <= index <= len(goals):
        return f"Ungültige Nummer. Es gibt {len(goals)} Gewohnheit(en)."

    if field not in EDITABLE_FIELDS:
        fields_str = "\n".join(f"  `{k}` – {v}" for k, v in EDITABLE_FIELDS.items())
        return f"Unbekanntes Feld `{field}`.\n\nEditierbare Felder:\n{fields_str}"

    if field == "priority" and value not in PRIORITY_MAP:
        return "Priorität muss `hoch`, `mittel` oder `niedrig` sein."

    if field == "target_date":
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return "Datum bitte im Format `YYYY-MM-DD` angeben, z.B. `2026-06-30`."

    if field == "target_value":
        try:
            value = float(value) if "." in value else int(value)
        except ValueError:
            return f"Zielwert muss eine Zahl sein, nicht `{value}`."

    goal = goals[index - 1]
    old_value = goal.get(field, "–")
    goal[field] = value
    _save(goals)

    field_label = EDITABLE_FIELDS[field]
    return (
        f"*[{index}] {goal['title']}*\n"
        f"{field_label} geändert:\n"
        f"  Vorher: _{old_value}_\n"
        f"  Jetzt:  _{value}_"
    )


def done_habit(index: int) -> str:
    goals = _load()
    if not 1 <= index <= len(goals):
        return f"Ungültige Nummer. Es gibt {len(goals)} Gewohnheit(en)."

    goal = goals[index - 1]
    goal["current_streak"] = goal.get("current_streak", 0) + 1
    streak = goal["current_streak"]
    _save(goals)

    milestones = {7: "Eine Woche!", 14: "Zwei Wochen!", 30: "Ein Monat!", 100: "100 Tage!"}
    extra = f"\n\n🏆 *{milestones[streak]}* Weiter so!" if streak in milestones else ""
    return (
        f"✅ *{goal['title']}*\n"
        f"Streak: 🔥 {streak} Tag{'e' if streak != 1 else ''}{extra}"
    )


def delete_habit(index: int) -> tuple[str, str]:
    """Gibt (title, bestätigungstext) zurück – löscht noch nicht."""
    goals = _load()
    if not 1 <= index <= len(goals):
        return "", f"Ungültige Nummer. Es gibt {len(goals)} Gewohnheit(en)."
    title = goals[index - 1]["title"]
    return title, f"Soll *{title}* wirklich gelöscht werden?\n\nMit `/habit delete {index} confirm` bestätigen."


def delete_habit_confirmed(index: int) -> str:
    goals = _load()
    if not 1 <= index <= len(goals):
        return f"Ungültige Nummer. Es gibt {len(goals)} Gewohnheit(en)."
    title = goals.pop(index - 1)["title"]
    _save(goals)
    return f"🗑 *{title}* wurde gelöscht."


def list_fields() -> str:
    lines = ["*Editierbare Felder:*\n"]
    for k, v in EDITABLE_FIELDS.items():
        lines.append(f"  `{k}` – {v}")
    lines.append("\nBeispiel: `/habit edit 1 priority hoch`")
    return "\n".join(lines)
