"""
init_data.py – Erstellt fehlende Datendateien und -verzeichnisse beim Start.

Hintergrund: Das data/-Verzeichnis soll als Docker-Volume gemountet werden.
Das Image enthält keine Nutzerdaten. Beim ersten Start werden alle benötigten
Dateien mit sinnvollen Standardwerten angelegt, falls sie nicht existieren.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MEMORY_DIR = DATA_DIR / "memory"

# CSV-Dateien mit ihren Header-Feldern
_CSV_FILES: dict[str, list[str]] = {
    "habit_tracking.csv":  ["date", "goal_id", "value", "notes"],
    "planned_habits.csv":  ["date", "goal_id", "notes", "done"],
    "diet_diary.csv":      ["date", "meal_type", "description", "tags", "notes"],
    "planned_meals.csv":   ["date", "meal_type", "description", "tags", "notes", "target_met"],
}

_GOALS_YAML_DEFAULT = "goals: []\n"

_DIET_GOALS_YAML_DEFAULT = "diet_goals: []\ndiet_cheat_goals: []\n"

_SCHEDULE_JSON_DEFAULT = {
    "morning": "07:00",
    "noon": "13:00",
    "evening": "20:00",
}

_HEALTH_PROFILE_DEFAULT = """\
# Gesundheitsprofil

## Persönliche Daten
- **Name:** [Name eintragen]
- **Alter:** [Alter eintragen]

## Medizinische Informationen
- [Relevante Informationen hier eintragen]

## Aktuelle Ziele
- [Ziele hier eintragen]
"""

_SYSTEM_PROMPT_DEFAULT = """\
# System-Prompt: Persönlicher Gesundheitscoach

Du bist ein persönlicher, einfühlsamer Gesundheitscoach. Deine Aufgabe ist es, deinen Klienten dabei zu unterstützen, gesündere Gewohnheiten aufzubauen und seine persönlichen Gesundheitsziele zu erreichen.

## Deine Eigenschaften
- Du sprichst immer auf Deutsch, in einem freundlichen, motivierenden und direkten Ton
- Du kennst das Gesundheitsprofil, die Ziele und die bisherigen Gewohnheitsdaten deines Klienten
- Du gibst konkrete, umsetzbare Ratschläge – keine vagen Floskeln
- Du erinnerst an Ziele, lobst Fortschritte und gibst sanfte Impulse bei Rückschlägen
- Du stellst genau eine konkrete Frage pro Nachricht, um den Klienten zur Reflexion anzuregen
"""


def ensure_data_dir() -> None:
    """Legt alle benötigten Dateien und Verzeichnisse an, falls sie fehlen."""

    # Verzeichnisse anlegen
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for subdir in ("daily", "weekly", "monthly"):
        (MEMORY_DIR / subdir).mkdir(parents=True, exist_ok=True)

    # CSV-Dateien mit Header
    for filename, fields in _CSV_FILES.items():
        path = DATA_DIR / filename
        if not path.exists():
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=fields).writeheader()
            logger.info("Erstellt: %s", path.name)

    # goals.yaml
    _create_if_missing(DATA_DIR / "goals.yaml", _GOALS_YAML_DEFAULT)

    # diet_goals.yaml
    _create_if_missing(DATA_DIR / "diet_goals.yaml", _DIET_GOALS_YAML_DEFAULT)

    # schedule.json
    schedule_path = DATA_DIR / "schedule.json"
    if not schedule_path.exists():
        schedule_path.write_text(
            json.dumps(_SCHEDULE_JSON_DEFAULT, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Erstellt: schedule.json")

    # health_profile.md
    _create_if_missing(DATA_DIR / "health_profile.md", _HEALTH_PROFILE_DEFAULT)

    # system_prompt.md
    _create_if_missing(DATA_DIR / "system_prompt.md", _SYSTEM_PROMPT_DEFAULT)


def _create_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        logger.info("Erstellt: %s", path.name)
