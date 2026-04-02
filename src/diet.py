"""
diet.py – Ernährungstagebuch und Diät-Ziel-Analyse
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).parent.parent / "data"
DIARY_FILE   = DATA_DIR / "diet_diary.csv"
PLANNED_FILE = DATA_DIR / "planned_meals.csv"
GOALS_FILE   = DATA_DIR / "diet_goals.yaml"

PLANNED_FIELDS = ["date", "meal_type", "description", "tags", "notes", "target_met"]

MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack", "other"]
MEAL_LABELS = {
    "breakfast": "Frühstück",
    "lunch":     "Mittagessen",
    "dinner":    "Abendessen",
    "snack":     "Snack",
    "other":     "Sonstiges",
}


# ---------------------------------------------------------------------------
# Interne Hilfsfunktionen
# ---------------------------------------------------------------------------

def _load_goals() -> dict:
    if not GOALS_FILE.exists():
        return {"diet_goals": [], "diet_cheat_goals": []}
    with open(GOALS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "diet_goals": data.get("diet_goals", []),
        "diet_cheat_goals": data.get("diet_cheat_goals", []),
    }


def _load_diary() -> list[dict]:
    if not DIARY_FILE.exists():
        return []
    with open(DIARY_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_diary_entry(entry: dict) -> None:
    fieldnames = ["date", "meal_type", "description", "tags", "notes"]
    file_exists = DIARY_FILE.exists() and DIARY_FILE.stat().st_size > 0
    with open(DIARY_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: entry.get(k, "") for k in fieldnames})


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _entries_this_week(entries: list[dict]) -> list[dict]:
    monday = _monday_of_week(date.today())
    result = []
    for e in entries:
        try:
            entry_date = date.fromisoformat(e["date"])
            if entry_date >= monday:
                result.append(e)
        except (ValueError, KeyError):
            pass
    return result


def _tags_of(entry: dict) -> list[str]:
    raw = entry.get("tags", "")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Cheat-Auswertung
# ---------------------------------------------------------------------------

def get_cheat_status() -> dict[str, dict]:
    """
    Gibt pro Cheat-Goal zurück wie oft es diese Woche ausgelöst wurde
    und ob das Limit erreicht/überschritten ist.

    Returns:
        {cheat_goal_id: {"title": str, "count": int, "limit": int, "exceeded": bool, "tags": list}}
    """
    goals = _load_goals()
    entries = _entries_this_week(_load_diary())
    result = {}

    for cg in goals["diet_cheat_goals"]:
        cg_tags = [t.lower() for t in cg.get("cheat_tags", [])]
        count = sum(
            1 for e in entries
            if any(tag in _tags_of(e) for tag in cg_tags)
        )
        limit = cg.get("limit_per_week", 1)
        result[cg["id"]] = {
            "title":    cg["title"],
            "count":    count,
            "limit":    limit,
            "exceeded": count >= limit,
            "tags":     cg_tags,
        }
    return result


def check_meal_against_cheats(description: str, tags: str) -> list[str]:
    """
    Prüft ob eine geplante Mahlzeit Cheat-Limits verletzt.
    Gibt Liste von Warnungstexten zurück (leer = alles ok).
    """
    goals = _load_goals()
    entries = _entries_this_week(_load_diary())
    input_tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
    desc_lower = description.lower()
    warnings = []

    for cg in goals["diet_cheat_goals"]:
        cg_tags = [t.lower() for t in cg.get("cheat_tags", [])]
        # Treffer: Tag explizit gesetzt ODER Keyword im Beschreibungstext
        hit = any(tag in input_tags for tag in cg_tags) or \
              any(tag in desc_lower for tag in cg_tags)
        if not hit:
            continue

        count = sum(
            1 for e in entries
            if any(tag in _tags_of(e) for tag in cg_tags)
        )
        limit = cg.get("limit_per_week", 1)
        remaining = limit - count
        if remaining <= 0:
            warnings.append(
                f"⚠️ Limit erreicht: \"{cg['title']}\" – diese Woche bereits {count}x "
                f"(Limit: {limit}x). {cg.get('notes', '')}"
            )
        elif remaining == 1:
            warnings.append(
                f"ℹ️ Letzte erlaubte Gelegenheit diese Woche: \"{cg['title']}\" "
                f"({count}/{limit} verbraucht)."
            )
    return warnings


# ---------------------------------------------------------------------------
# Public API – Tools für Claude
# ---------------------------------------------------------------------------

def log_meal(
    meal_type: str,
    description: str,
    tags: str = "",
    notes: str = "",
    meal_date: str = "",
    force: bool = False,
) -> str:
    """Loggt eine Mahlzeit ins Ernährungstagebuch."""
    if meal_type not in MEAL_TYPES:
        meal_type = "other"
    entry_date = meal_date if meal_date else date.today().isoformat()

    try:
        date.fromisoformat(entry_date)
    except ValueError:
        entry_date = date.today().isoformat()

    # Duplikat-Schutz
    if not force:
        existing = [
            e for e in _load_diary()
            if e.get("date") == entry_date and e.get("meal_type") == meal_type
        ]
        if existing:
            label = MEAL_LABELS.get(meal_type, meal_type)
            prev = existing[-1]
            return (
                f"⚠️ Für {entry_date} wurde bereits ein *{label}* eingetragen: "
                f"_{prev.get('description', '')}_.\n"
                f"Wenn du wirklich einen weiteren Eintrag möchtest (z.B. 2x gegessen), bestätige das ausdrücklich."
            )

    _save_diary_entry({
        "date":        entry_date,
        "meal_type":   meal_type,
        "description": description,
        "tags":        tags.lower(),
        "notes":       notes,
    })

    label = MEAL_LABELS.get(meal_type, meal_type)
    warnings = check_meal_against_cheats(description, tags)
    result = f"✅ {label} eingetragen: _{description}_"
    if warnings:
        result += "\n\n" + "\n".join(warnings)
    return result


def list_diary_entries(meal_date: str = "", meal_type: str = "") -> str:
    """Listet Tagebucheinträge mit Index auf (für delete/edit)."""
    entries = _load_diary()
    if meal_date:
        entries = [e for e in entries if e.get("date") == meal_date]
    if meal_type:
        entries = [e for e in entries if e.get("meal_type") == meal_type]
    if not entries:
        return "Keine Einträge gefunden."
    lines = []
    for i, e in enumerate(entries):
        label = MEAL_LABELS.get(e.get("meal_type", ""), e.get("meal_type", ""))
        tags_str = f" [{e['tags']}]" if e.get("tags") else ""
        notes_str = f" – {e['notes']}" if e.get("notes") else ""
        lines.append(f"#{i}  {e['date']}  {label}: {e['description']}{tags_str}{notes_str}")
    return "\n".join(lines)


def delete_diary_entry(meal_date: str, meal_type: str, occurrence: int = 0) -> str:
    """Löscht einen Tagebucheintrag. Bei mehreren gleichen: occurrence=0 für ersten."""
    entries = _load_diary()
    matches = [
        (i, e) for i, e in enumerate(entries)
        if e.get("date") == meal_date and e.get("meal_type") == meal_type
    ]
    label = MEAL_LABELS.get(meal_type, meal_type)
    if not matches:
        return f"Kein Eintrag gefunden für {label} am {meal_date}."
    if occurrence >= len(matches):
        return f"Nur {len(matches)} Eintrag/Einträge vorhanden (occurrence 0–{len(matches) - 1})."

    idx_to_remove = matches[occurrence][0]
    removed = entries.pop(idx_to_remove)

    fieldnames = ["date", "meal_type", "description", "tags", "notes"]
    with open(DIARY_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(entries)

    removed_label = MEAL_LABELS.get(removed.get("meal_type", ""), removed.get("meal_type", ""))
    return f"✅ Gelöscht: {removed['date']} – {removed_label}: {removed.get('description', '')}"


def edit_diary_entry(
    meal_date: str,
    meal_type: str,
    new_meal_type: str | None = None,
    new_description: str | None = None,
    new_tags: str | None = None,
    occurrence: int = 0,
) -> str:
    """Ändert einen bestehenden Tagebucheintrag (z.B. meal_type korrigieren)."""
    entries = _load_diary()
    matches = [
        (i, e) for i, e in enumerate(entries)
        if e.get("date") == meal_date and e.get("meal_type") == meal_type
    ]
    label = MEAL_LABELS.get(meal_type, meal_type)
    if not matches:
        return f"Kein Eintrag gefunden für {label} am {meal_date}."
    if occurrence >= len(matches):
        return f"Nur {len(matches)} Eintrag/Einträge vorhanden."

    idx = matches[occurrence][0]
    if new_meal_type and new_meal_type in MEAL_TYPES:
        entries[idx]["meal_type"] = new_meal_type
    if new_description is not None:
        entries[idx]["description"] = new_description
    if new_tags is not None:
        entries[idx]["tags"] = new_tags.lower()

    fieldnames = ["date", "meal_type", "description", "tags", "notes"]
    with open(DIARY_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(entries)

    e = entries[idx]
    new_label = MEAL_LABELS.get(e["meal_type"], e["meal_type"])
    return f"✅ Aktualisiert: {e['date']} – {new_label}: {e['description']}"


def get_recent_diary(days: int = 7) -> str:
    """Gibt die letzten N Tage des Tagebuchs als lesbaren Text zurück."""
    entries = _load_diary()
    cutoff = date.today() - timedelta(days=days)
    recent = [
        e for e in entries
        if e.get("date", "") >= cutoff.isoformat()
    ]
    if not recent:
        return f"Keine Einträge in den letzten {days} Tagen."

    lines = []
    current_date = None
    for e in sorted(recent, key=lambda x: (x.get("date", ""), x.get("meal_type", ""))):
        if e["date"] != current_date:
            current_date = e["date"]
            lines.append(f"\n*{current_date}*")
        label = MEAL_LABELS.get(e["meal_type"], e["meal_type"])
        tags_str = f" [{e['tags']}]" if e.get("tags") else ""
        lines.append(f"  {label}: {e['description']}{tags_str}")
    return "\n".join(lines).strip()


def get_diet_context() -> str:
    """Vollständiger Diet-Kontext für den Claude System-Prompt."""
    goals_data = _load_goals()
    cheat_status = get_cheat_status()

    # Ernährungsziele
    goal_lines = ["### Ernährungsziele"]
    for g in goals_data["diet_goals"]:
        goal_lines.append(f"- **{g['title']}**: {g['description']}")
        if g.get("focus_foods"):
            goal_lines.append(f"  Empfohlen: {', '.join(g['focus_foods'])}")
        if g.get("avoid_foods"):
            goal_lines.append(f"  Vermeiden: {', '.join(g['avoid_foods'])}")

    # Cheat-Goals mit aktuellem Stand
    goal_lines.append("\n### Cheat-Limits (diese Woche)")
    for cg in goals_data["diet_cheat_goals"]:
        status = cheat_status.get(cg["id"], {})
        count = status.get("count", 0)
        limit = status.get("limit", cg.get("limit_per_week", 1))
        flag = " ⚠️ LIMIT ERREICHT" if count >= limit else f" ({count}/{limit} verbraucht)"
        goal_lines.append(f"- {cg['title']}{flag}")
        goal_lines.append(f"  Auslöser-Tags: {', '.join(cg.get('cheat_tags', []))}")

    # Mahlzeitenplan heute + morgen
    plan_text = get_todays_plan_for_context()
    goal_lines.append(f"\n### Mahlzeitenplan (heute & morgen)\n{plan_text}")

    # Letzte 7 Tage Tagebuch
    diary_text = get_recent_diary(7)
    goal_lines.append(f"\n### Ernährungstagebuch (letzte 7 Tage)\n{diary_text}")

    return "\n".join(goal_lines)


# ---------------------------------------------------------------------------
# Planned Meals – CRUD
# ---------------------------------------------------------------------------

def _load_planned() -> list[dict]:
    if not PLANNED_FILE.exists():
        return []
    with open(PLANNED_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_planned(rows: list[dict]) -> None:
    with open(PLANNED_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PLANNED_FIELDS)
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in PLANNED_FIELDS} for r in rows)


def check_plan_against_goals(description: str, tags: str, meal_date: str) -> list[str]:
    """
    Prüft ob eine geplante Mahlzeit gegen Cheat-Goals verstößt.
    Berücksichtigt sowohl bereits eingetragene Pläne als auch das Diary für die betreffende Woche.
    Gibt Warnungstexte zurück (leer = alles ok).
    """
    goals = _load_goals()
    try:
        plan_date = date.fromisoformat(meal_date) if meal_date else date.today()
    except ValueError:
        plan_date = date.today()

    monday = _monday_of_week(plan_date)
    week_end = monday + timedelta(days=6)

    # Alle Einträge aus Diary + Planned für diese Woche zusammenführen
    diary_week = [
        e for e in _load_diary()
        if monday.isoformat() <= e.get("date", "") <= week_end.isoformat()
    ]
    planned_week = [
        e for e in _load_planned()
        if monday.isoformat() <= e.get("date", "") <= week_end.isoformat()
    ]
    all_entries = diary_week + planned_week

    input_tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
    desc_lower = description.lower()
    warnings = []

    for cg in goals["diet_cheat_goals"]:
        cg_tags = [t.lower() for t in cg.get("cheat_tags", [])]
        hit = any(tag in input_tags for tag in cg_tags) or \
              any(tag in desc_lower for tag in cg_tags)
        if not hit:
            continue

        count = sum(
            1 for e in all_entries
            if any(tag in _tags_of(e) for tag in cg_tags)
        )
        limit = cg.get("limit_per_week", 1)
        if count >= limit:
            warnings.append(
                f"⚠️ Cheat-Limit überschritten: \"{cg['title']}\" – "
                f"diese Woche bereits {count}x eingeplant/gegessen (Limit: {limit}x)."
            )
        elif count == limit - 1:
            warnings.append(
                f"ℹ️ Letzte erlaubte Gelegenheit diese Woche: \"{cg['title']}\" "
                f"({count}/{limit} verbraucht)."
            )
    return warnings


def plan_meal(
    meal_type: str,
    description: str,
    tags: str = "",
    notes: str = "",
    meal_date: str = "",
) -> str:
    """Plant eine Mahlzeit und trägt sie in planned_meals.csv ein. Keine Konfliktprüfung hier –
    die muss Claude vorher mit check_meal_plan_conflicts erledigen und ggf. bestätigen lassen."""
    if meal_type not in MEAL_TYPES:
        meal_type = "other"
    entry_date = meal_date if meal_date else date.today().isoformat()
    try:
        date.fromisoformat(entry_date)
    except ValueError:
        entry_date = date.today().isoformat()

    rows = _load_planned()
    rows.append({
        "date":        entry_date,
        "meal_type":   meal_type,
        "description": description,
        "tags":        tags.lower(),
        "notes":       notes,
        "target_met":  "",
    })
    _save_planned(rows)

    label = MEAL_LABELS.get(meal_type, meal_type)
    return f"📅 Geplant für {entry_date} – {label}: *{description}*"


def check_meal_plan_conflicts(
    description: str,
    tags: str,
    meal_date: str = "",
) -> str:
    """
    Prüft ob eine geplante Mahlzeit gegen Ziele verstößt.
    Gibt einen leeren String zurück wenn alles ok ist,
    sonst einen Warnungstext den Claude dem User zeigen soll (mit Rückfrage).
    """
    warnings = check_plan_against_goals(description, tags, meal_date)
    if not warnings:
        return ""
    return "\n".join(warnings)


def get_planned_meals(meal_date: str = "") -> str:
    """Gibt den Mahlzeitenplan für ein bestimmtes Datum zurück."""
    target = meal_date if meal_date else date.today().isoformat()
    rows = [r for r in _load_planned() if r.get("date") == target]

    if not rows:
        return f"Für {target} sind noch keine Mahlzeiten geplant."

    lines = [f"*Mahlzeitenplan für {target}:*"]
    for r in sorted(rows, key=lambda x: MEAL_TYPES.index(x.get("meal_type", "other")) if x.get("meal_type") in MEAL_TYPES else 99):
        label = MEAL_LABELS.get(r["meal_type"], r["meal_type"])
        met = r.get("target_met", "")
        status = " ✅" if met == "true" else (" ❌" if met == "false" else "")
        tags_str = f" [{r['tags']}]" if r.get("tags") else ""
        lines.append(f"  {label}: {r['description']}{tags_str}{status}")
    return "\n".join(lines)


def get_todays_plan_for_context() -> str:
    """Kompakter Plan für heute + morgen – wird in den Coach-Kontext eingebettet."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def _day_summary(d: str, label: str) -> str:
        rows = [r for r in _load_planned() if r.get("date") == d]
        if not rows:
            return f"{label}: nichts geplant"
        meals = []
        for r in sorted(rows, key=lambda x: MEAL_TYPES.index(x.get("meal_type", "other")) if x.get("meal_type") in MEAL_TYPES else 99):
            ml = MEAL_LABELS.get(r["meal_type"], r["meal_type"])
            meals.append(f"{ml}: {r['description']}")
        return f"{label}: " + " | ".join(meals)

    return "\n".join([_day_summary(today, "Heute"), _day_summary(tomorrow, "Morgen")])


def delete_planned_meal(meal_date: str, meal_type: str, occurrence: int = 0) -> str:
    """Löscht einen geplanten Mahlzeiteintrag."""
    rows = _load_planned()
    matches = [(i, r) for i, r in enumerate(rows)
               if r.get("date") == meal_date and r.get("meal_type") == meal_type]
    label = MEAL_LABELS.get(meal_type, meal_type)
    if not matches:
        return f"Kein geplanter Eintrag für {label} am {meal_date} gefunden."
    if occurrence >= len(matches):
        return f"Nur {len(matches)} Eintrag/Einträge vorhanden (occurrence 0–{len(matches) - 1})."
    idx = matches[occurrence][0]
    removed = rows.pop(idx)
    _save_planned(rows)
    return f"✅ Geplante Mahlzeit gelöscht: {removed['date']} – {label}: {removed.get('description', '')}"


def edit_planned_meal(
    meal_date: str,
    meal_type: str,
    new_meal_type: str | None = None,
    new_description: str | None = None,
    new_tags: str | None = None,
    occurrence: int = 0,
) -> str:
    """Ändert einen geplanten Mahlzeiteintrag."""
    rows = _load_planned()
    matches = [(i, r) for i, r in enumerate(rows)
               if r.get("date") == meal_date and r.get("meal_type") == meal_type]
    label = MEAL_LABELS.get(meal_type, meal_type)
    if not matches:
        return f"Kein geplanter Eintrag für {label} am {meal_date} gefunden."
    if occurrence >= len(matches):
        return f"Nur {len(matches)} Eintrag/Einträge vorhanden."
    idx = matches[occurrence][0]
    if new_meal_type and new_meal_type in MEAL_TYPES:
        rows[idx]["meal_type"] = new_meal_type
    if new_description is not None:
        rows[idx]["description"] = new_description
    if new_tags is not None:
        rows[idx]["tags"] = new_tags.lower()
    _save_planned(rows)
    r = rows[idx]
    new_label = MEAL_LABELS.get(r["meal_type"], r["meal_type"])
    return f"✅ Aktualisiert: {r['date']} – {new_label}: {r['description']}"


def update_target_met(meal_date: str, meal_type: str, met: bool) -> str:
    """Aktualisiert das target_met-Feld für eine geplante Mahlzeit."""
    rows = _load_planned()
    updated = 0
    for r in rows:
        if r.get("date") == meal_date and r.get("meal_type") == meal_type:
            r["target_met"] = "true" if met else "false"
            updated += 1
    if updated == 0:
        return f"Kein Plan-Eintrag für {meal_date} / {MEAL_LABELS.get(meal_type, meal_type)} gefunden."
    _save_planned(rows)
    label = MEAL_LABELS.get(meal_type, meal_type)
    status = "✅ eingehalten" if met else "❌ nicht eingehalten"
    return f"{label} am {meal_date}: Ziel {status}."


def get_plan_vs_diary_summary(meal_date: str = "") -> str:
    """Vergleicht den Plan mit dem tatsächlichen Tagebuch für das Abend-Briefing."""
    target = meal_date if meal_date else date.today().isoformat()
    planned = [r for r in _load_planned() if r.get("date") == target]
    eaten   = [r for r in _load_diary() if r.get("date") == target]

    if not planned and not eaten:
        return "Heute wurden keine Mahlzeiten geplant oder eingetragen."

    lines = [f"*Plan vs. Realität ({target}):*"]
    for p in sorted(planned, key=lambda x: MEAL_TYPES.index(x.get("meal_type", "other")) if x.get("meal_type") in MEAL_TYPES else 99):
        label = MEAL_LABELS.get(p["meal_type"], p["meal_type"])
        match = any(
            e.get("meal_type") == p["meal_type"] for e in eaten
        )
        status = "✅ gegessen" if match else "❓ unklar / nicht eingetragen"
        lines.append(f"  {label}: geplant \"{p['description']}\" - {status}")

    unplanned = [e for e in eaten if not any(p.get("meal_type") == e.get("meal_type") for p in planned)]
    if unplanned:
        lines.append("\n*Ungeplant gegessen:*")
        for e in unplanned:
            label = MEAL_LABELS.get(e["meal_type"], e["meal_type"])
            lines.append(f"  {label}: {e['description']}")

    return "\n".join(lines)


def format_cheat_status_summary() -> str:
    """Kurze Zusammenfassung für /status oder Morgen-Kontext."""
    status = get_cheat_status()
    if not status:
        return "Keine Cheat-Limits konfiguriert."
    lines = []
    for s in status.values():
        bar = "🟥" if s["exceeded"] else ("🟨" if s["count"] > 0 else "🟩")
        lines.append(f"{bar} {s['title']}: {s['count']}/{s['limit']}x diese Woche")
    return "\n".join(lines)
