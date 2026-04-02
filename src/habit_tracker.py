"""
habit_tracker.py – Tracking der Goals aus goals.yaml

Datenmodell (habit_tracking.csv):
  date     – YYYY-MM-DD
  goal_id  – z.B. goal_001
  value    – numerischer Wert (1 = erledigt, 0 = nicht erledigt, oder Menge)
  notes    – optionaler Freitext
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import yaml

DATA_DIR       = Path(__file__).parent.parent / "data"
TRACKING_FILE  = DATA_DIR / "habit_tracking.csv"
PLANNED_FILE   = DATA_DIR / "planned_habits.csv"
GOALS_FILE     = DATA_DIR / "goals.yaml"

TRACKING_FIELDS = ["date", "goal_id", "value", "notes"]
PLANNED_FIELDS  = ["date", "goal_id", "notes", "done"]


# ---------------------------------------------------------------------------
# Interne Hilfsfunktionen
# ---------------------------------------------------------------------------

def _load_goals() -> list[dict]:
    if not GOALS_FILE.exists():
        return []
    with open(GOALS_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("goals", [])


def _load_tracking() -> list[dict]:
    if not TRACKING_FILE.exists():
        return []
    with open(TRACKING_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_entry(entry: dict) -> None:
    file_exists = TRACKING_FILE.exists() and TRACKING_FILE.stat().st_size > 0
    with open(TRACKING_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKING_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: entry.get(k, "") for k in TRACKING_FIELDS})


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _entries_for_week(entries: list[dict], ref_date: date) -> list[dict]:
    monday = _monday_of_week(ref_date)
    sunday = monday + timedelta(days=6)
    return [
        e for e in entries
        if monday.isoformat() <= e.get("date", "") <= sunday.isoformat()
    ]


# ---------------------------------------------------------------------------
# Wochenauswertung
# ---------------------------------------------------------------------------

def _is_daily_goal(target_unit: str) -> bool:
    """Tagesziele werden pro Tag ausgewertet, nicht wöchentlich summiert."""
    return target_unit.endswith("/tag")


def _daily_totals(goal_entries: list[dict]) -> dict[str, float]:
    """Summiert Einträge pro Tag: {date: sum_of_values}."""
    by_day: dict[str, float] = {}
    for e in goal_entries:
        d = e.get("date", "")
        try:
            by_day[d] = by_day.get(d, 0.0) + float(e.get("value", 0))
        except ValueError:
            pass
    return by_day


def get_week_status(ref_date: date | None = None) -> list[dict]:
    """
    Gibt für jedes Goal den aktuellen Wochenstand zurück.

    Returns:
        Liste von Dicts mit:
          goal_id, title, priority, target_value, target_unit,
          achieved, on_track (bool), entries (list)
          + für Tagesziele: today_total (float), days_met (int)
    """
    ref_date = ref_date or date.today()
    goals    = _load_goals()
    entries  = _entries_for_week(_load_tracking(), ref_date)
    result   = []

    for g in goals:
        gid         = g["id"]
        target_unit = g.get("target_unit", "")
        raw_target  = g.get("target_value", 1)
        target      = float(raw_target) if raw_target not in (None, "") else 1.0
        goal_entries = [e for e in entries if e.get("goal_id") == gid]

        if _is_daily_goal(target_unit):
            # Tagesziel: pro Tag aggregieren, dann zählen wie viele Tage erreicht
            by_day      = _daily_totals(goal_entries)
            today_total = by_day.get(ref_date.isoformat(), 0.0)
            days_met    = sum(1 for v in by_day.values() if v >= target)
            achieved    = today_total   # primäre Metrik: heutiger Wert
            on_track    = today_total >= target
            result.append({
                "goal_id":     gid,
                "title":       g.get("title", ""),
                "priority":    g.get("priority", "mittel"),
                "target_value": target,
                "target_unit": target_unit,
                "achieved":    achieved,
                "on_track":    on_track,
                "entries":     goal_entries,
                "today_total": today_total,
                "days_met":    days_met,
            })
        else:
            # Wochenziel: alle Einträge der Woche summieren
            try:
                achieved = sum(float(e.get("value", 0)) for e in goal_entries)
            except ValueError:
                achieved = len(goal_entries)

            if target == 0:
                on_track = achieved == 0
            else:
                on_track = achieved >= target

            result.append({
                "goal_id":     gid,
                "title":       g.get("title", ""),
                "priority":    g.get("priority", "mittel"),
                "target_value": target,
                "target_unit": target_unit,
                "achieved":    achieved,
                "on_track":    on_track,
                "entries":     goal_entries,
            })

    return result


def get_habit_context() -> str:
    """Formatierter Wochenstand für den Claude-Kontext."""
    status = get_week_status()
    if not status:
        return "Keine Goals definiert."

    monday = _monday_of_week(date.today())
    sunday = monday + timedelta(days=6)
    lines  = [f"Woche {monday.strftime('%d.%m.')} – {sunday.strftime('%d.%m.')}:\n"]

    for s in status:
        target      = s["target_value"]
        achieved    = s["achieved"]
        target_unit = s["target_unit"]

        if target == 0:
            icon     = "✅" if s["on_track"] else "❌"
            progress = f"{int(achieved)} Vorfälle (Ziel: 0)"
        elif _is_daily_goal(target_unit):
            today_total = s.get("today_total", 0.0)
            days_met    = s.get("days_met", 0)
            icon        = "✅" if s["on_track"] else ("🟡" if today_total > 0 else "❌")
            progress    = f"Heute: {today_total:.1f}/{target:.1f} {target_unit} ({days_met} Tage diese Woche erreicht)"
        else:
            icon     = "✅" if s["on_track"] else ("🟡" if achieved > 0 else "❌")
            progress = f"{achieved:.0f}/{target:.0f} {target_unit}"

        prio = s["priority"].upper()
        lines.append(f"{icon} [{prio}] {s['title']}: {progress}")

        # Letzte Einträge als Kontext
        for e in sorted(s["entries"], key=lambda x: x.get("date", ""))[-3:]:
            note = f" – {e['notes']}" if e.get("notes") else ""
            lines.append(f"     • {e['date']}{note}")

    # Tagesplan heute + morgen
    plan = get_planned_habits_context()
    lines.append(f"\n### Habit-Plan (heute & morgen)\n{plan}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API – Tools für Claude
# ---------------------------------------------------------------------------

def log_habit(
    goal_id: str,
    value: float = 1.0,
    notes: str = "",
    entry_date: str = "",
    force: bool = False,
) -> str:
    """Loggt eine Habit-Ausführung."""
    goals  = _load_goals()
    goal   = next((g for g in goals if g["id"] == goal_id), None)

    if not goal:
        # Fallback: goal_id als Titel-Suche
        goal = next(
            (g for g in goals if goal_id.lower() in g.get("title", "").lower()),
            None,
        )
        if not goal:
            ids = ", ".join(g["id"] for g in goals)
            return f"Goal '{goal_id}' nicht gefunden. Verfügbare IDs: {ids}"
        goal_id = goal["id"]

    entry_date = entry_date or date.today().isoformat()

    # Für sessionsbasierte Goals (einheiten/woche, tage/woche) immer value=1.
    # Die tatsächliche Menge (km, Minuten) gehört in notes.
    target_unit = goal.get("target_unit", "")
    if target_unit in ("einheiten/woche", "tage/woche") and value != 1.0:
        if not notes:
            notes = str(value)
        value = 1.0

    # Duplikat-Schutz
    if not force:
        existing = [
            e for e in _load_tracking()
            if e.get("goal_id") == goal_id and e.get("date") == entry_date
        ]
        if existing:
            title = goal["title"]
            prev = existing[-1]
            note_str = f" ({prev['notes']})" if prev.get("notes") else ""
            return (
                f"⚠️ *{title}* wurde am {entry_date} bereits eingetragen "
                f"(Wert: {prev['value']}{note_str}).\n"
                f"Wenn du es wirklich nochmal eintragen möchtest, bestätige das ausdrücklich."
            )

    _save_entry({"date": entry_date, "goal_id": goal_id, "value": value, "notes": notes})

    raw_target  = goal.get("target_value", 1)
    target      = float(raw_target) if raw_target not in (None, "") else 1.0
    title       = goal["title"]
    ref         = date.fromisoformat(entry_date)
    week_entries = [
        e for e in _entries_for_week(_load_tracking(), ref)
        if e.get("goal_id") == goal_id
    ]

    if target == 0:
        week_sum   = sum(float(e.get("value", 0)) for e in week_entries)
        status_str = f"⚠️ {int(week_sum)} Vorfall(e) diese Woche eingetragen."
    elif _is_daily_goal(target_unit):
        # Tagesziel: heutigen Tagesstand zeigen + Wochenübersicht
        by_day     = _daily_totals(week_entries)
        day_total  = by_day.get(entry_date, 0.0)
        days_met   = sum(1 for v in by_day.values() if v >= target)
        remaining  = max(0.0, target - day_total)
        if day_total >= target:
            status_str = f"Heute: {day_total:.1f}/{target:.1f} {target_unit} ✅ – Tagesziel erreicht! ({days_met} Tage diese Woche)"
        else:
            status_str = f"Heute: {day_total:.1f}/{target:.1f} {target_unit} – noch {remaining:.1f} ausstehend."
    else:
        week_sum  = sum(float(e.get("value", 0)) for e in week_entries)
        remaining = max(0, target - week_sum)
        status_str = (
            f"✅ Wochenstand: {week_sum:.0f}/{target:.0f} – Wochenziel erreicht!"
            if week_sum >= target
            else f"Wochenstand: {week_sum:.0f}/{target:.0f} – noch {remaining:.0f} ausstehend."
        )

    return f"✅ *{title}* eingetragen ({entry_date}).\n{status_str}"


def list_habit_entries(goal_id: str = "", entry_date: str = "") -> str:
    """Listet Einträge auf, optional gefiltert nach goal_id und/oder Datum."""
    entries = _load_tracking()
    if goal_id:
        entries = [e for e in entries if e.get("goal_id") == goal_id]
    if entry_date:
        entries = [e for e in entries if e.get("date") == entry_date]
    if not entries:
        return "Keine Einträge gefunden."
    lines = []
    for i, e in enumerate(entries):
        note = f" – {e['notes']}" if e.get("notes") else ""
        lines.append(f"#{i}  {e['date']}  {e['goal_id']}  {e['value']}{note}")
    return "\n".join(lines)


def delete_habit_entry(goal_id: str, entry_date: str, occurrence: int = 0) -> str:
    """Löscht einen Eintrag aus habit_tracking.csv.

    occurrence: 0-basierter Index falls mehrere Einträge mit gleichem goal_id+date
    existieren (z.B. 0 = ersten löschen, 1 = zweiten löschen).
    """
    entries = _load_tracking()
    matches = [
        (i, e) for i, e in enumerate(entries)
        if e.get("goal_id") == goal_id and e.get("date") == entry_date
    ]
    if not matches:
        return f"Kein Eintrag gefunden für {goal_id} am {entry_date}."
    if occurrence >= len(matches):
        return f"Nur {len(matches)} Eintrag/Einträge vorhanden (occurrence 0–{len(matches)-1})."

    idx_to_remove = matches[occurrence][0]
    removed = entries.pop(idx_to_remove)

    # Datei neu schreiben
    with open(TRACKING_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKING_FIELDS)
        writer.writeheader()
        writer.writerows(entries)

    note = f" ({removed.get('notes')})" if removed.get("notes") else ""
    return f"✅ Eintrag gelöscht: {entry_date}  {goal_id}  {removed.get('value')}{note}"


def edit_habit_entry(
    goal_id: str,
    entry_date: str,
    new_value: float | None = None,
    new_notes: str | None = None,
    occurrence: int = 0,
) -> str:
    """Ändert value und/oder notes eines bestehenden Eintrags."""
    entries = _load_tracking()
    matches = [
        (i, e) for i, e in enumerate(entries)
        if e.get("goal_id") == goal_id and e.get("date") == entry_date
    ]
    if not matches:
        return f"Kein Eintrag gefunden für {goal_id} am {entry_date}."
    if occurrence >= len(matches):
        return f"Nur {len(matches)} Eintrag/Einträge vorhanden (occurrence 0–{len(matches)-1})."

    idx = matches[occurrence][0]
    if new_value is not None:
        entries[idx]["value"] = new_value
    if new_notes is not None:
        entries[idx]["notes"] = new_notes

    with open(TRACKING_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKING_FIELDS)
        writer.writeheader()
        writer.writerows(entries)

    e = entries[idx]
    return f"✅ Eintrag aktualisiert: {e['date']}  {e['goal_id']}  {e['value']}  {e.get('notes','')}"


# ---------------------------------------------------------------------------
# Planned Habits – CRUD
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


def plan_habit(goal_id: str, planned_date: str = "", notes: str = "", force: bool = False) -> str:
    """Plant eine Habit-Ausführung für ein bestimmtes Datum."""
    goals = _load_goals()
    goal = next((g for g in goals if g["id"] == goal_id), None)
    if not goal:
        goal = next(
            (g for g in goals if goal_id.lower() in g.get("title", "").lower()), None
        )
        if not goal:
            ids = ", ".join(g["id"] for g in goals)
            return f"Goal '{goal_id}' nicht gefunden. Verfügbare IDs: {ids}"
        goal_id = goal["id"]

    entry_date = planned_date or (date.today() + timedelta(days=1)).isoformat()
    try:
        date.fromisoformat(entry_date)
    except ValueError:
        entry_date = (date.today() + timedelta(days=1)).isoformat()

    # Duplikat-Schutz
    if not force:
        existing = [
            r for r in _load_planned()
            if r.get("goal_id") == goal_id and r.get("date") == entry_date
        ]
        if existing:
            prev = existing[-1]
            note_str = f" ({prev['notes']})" if prev.get("notes") else ""
            return (
                f"⚠️ *{goal['title']}* ist für {entry_date} bereits geplant{note_str}.\n"
                f"Wenn du es trotzdem nochmal eintragen möchtest, bestätige das ausdrücklich."
            )

    rows = _load_planned()
    rows.append({"date": entry_date, "goal_id": goal_id, "notes": notes, "done": ""})
    _save_planned(rows)

    return f"📅 Geplant für {entry_date}: *{goal['title']}*"


def get_planned_habits(planned_date: str = "") -> str:
    """Gibt geplante Habits für ein Datum zurück."""
    target = planned_date or date.today().isoformat()
    goals = _load_goals()
    goal_map = {g["id"]: g.get("title", g["id"]) for g in goals}

    rows = [r for r in _load_planned() if r.get("date") == target]
    if not rows:
        return f"Für {target} sind keine Habits geplant."

    lines = [f"*Geplante Habits für {target}:*"]
    for r in rows:
        title = goal_map.get(r["goal_id"], r["goal_id"])
        done = r.get("done", "")
        status = " ✅" if done == "true" else (" ❌" if done == "false" else "")
        note = f" – {r['notes']}" if r.get("notes") else ""
        lines.append(f"  • {title}{note}{status}")
    return "\n".join(lines)


def mark_planned_habit_done(goal_id: str, planned_date: str, done: bool) -> str:
    """Markiert einen geplanten Habit als erledigt oder nicht erledigt."""
    rows = _load_planned()
    updated = 0
    goals = _load_goals()
    goal = next((g for g in goals if g["id"] == goal_id), None)
    title = goal["title"] if goal else goal_id

    for r in rows:
        if r.get("goal_id") == goal_id and r.get("date") == planned_date:
            r["done"] = "true" if done else "false"
            updated += 1

    if updated == 0:
        return f"Kein Plan-Eintrag für {goal_id} am {planned_date} gefunden."
    _save_planned(rows)
    status = "✅ erledigt" if done else "❌ nicht erledigt"
    return f"*{title}* am {planned_date}: {status}."


def delete_planned_habit(goal_id: str, planned_date: str) -> str:
    """Löscht einen geplanten Habit-Eintrag."""
    rows = _load_planned()
    matches = [(i, r) for i, r in enumerate(rows)
               if r.get("goal_id") == goal_id and r.get("date") == planned_date]
    if not matches:
        return f"Kein Plan-Eintrag für {goal_id} am {planned_date} gefunden."
    idx = matches[0][0]
    rows.pop(idx)
    _save_planned(rows)
    goals = _load_goals()
    goal = next((g for g in goals if g["id"] == goal_id), None)
    title = goal["title"] if goal else goal_id
    return f"✅ Geplanter Habit gelöscht: {planned_date} – {title}"


def get_planned_habits_context() -> str:
    """Kompakter Plan für heute + morgen – wird in den Coach-Kontext eingebettet."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    goals = _load_goals()
    goal_map = {g["id"]: g.get("title", g["id"]) for g in goals}

    def _day_summary(d: str, label: str) -> str:
        rows = [r for r in _load_planned() if r.get("date") == d]
        if not rows:
            return f"{label}: nichts geplant"
        items = []
        for r in rows:
            title = goal_map.get(r["goal_id"], r["goal_id"])
            done = r.get("done", "")
            status = " ✅" if done == "true" else (" ❌" if done == "false" else "")
            items.append(f"{title}{status}")
        return f"{label}: " + ", ".join(items)

    return "\n".join([_day_summary(today, "Heute"), _day_summary(tomorrow, "Morgen")])


def get_habits_status_text() -> str:
    """Kurzübersicht für /habits oder Nutzeranfrage."""
    status = get_week_status()
    if not status:
        return "Keine Goals definiert."

    on_track  = [s for s in status if s["on_track"]]
    off_track = [s for s in status if not s["on_track"]]

    lines = ["*Habit-Status diese Woche:*\n"]
    if off_track:
        lines.append("*Nachholbedarf:*")
        for s in off_track:
            target      = s["target_value"]
            achieved    = s["achieved"]
            target_unit = s["target_unit"]
            if target == 0:
                lines.append(f"  ❌ {s['title']}: {int(achieved)} Vorfälle")
            elif _is_daily_goal(target_unit):
                today_total = s.get("today_total", 0.0)
                lines.append(f"  ❌ {s['title']}: heute {today_total:.1f}/{target:.1f} {target_unit}")
            else:
                lines.append(f"  ❌ {s['title']}: {achieved:.0f}/{target:.0f} {target_unit}")

    if on_track:
        lines.append("\n*Auf Kurs:*")
        for s in on_track:
            lines.append(f"  ✅ {s['title']}")

    lines.append(
        '\n_Eintragen: z.B. "Heute war ich joggen, 4km"_'
    )
    return "\n".join(lines)
