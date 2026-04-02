"""
scheduler.py – APScheduler-Konfiguration für die 3 täglichen Check-ins
"""

import json
import logging
import os
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
SCHEDULE_FILE = DATA_DIR / "schedule.json"

CHECK_LABELS = {
    "morning": "Morgen-Check-in",
    "noon":    "Mittags-Check-in",
    "evening": "Abend-Check-in",
}
DEFAULT_TIMES = {
    "morning": "07:00",
    "noon":    "13:00",
    "evening": "20:00",
}


def load_schedule() -> dict[str, str]:
    if SCHEDULE_FILE.exists():
        try:
            data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
            # Nur bekannte Keys übernehmen, fehlende mit Default auffüllen
            return {k: data.get(k, DEFAULT_TIMES[k]) for k in DEFAULT_TIMES}
        except Exception:
            logger.warning("schedule.json konnte nicht gelesen werden, nutze Defaults.")
    return dict(DEFAULT_TIMES)


def save_schedule(schedule: dict[str, str]) -> None:
    SCHEDULE_FILE.write_text(
        json.dumps(schedule, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parst 'HH:MM' und gibt (hour, minute) zurück. Wirft ValueError bei ungültigem Format."""
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError
    return hour, minute


def create_scheduler(
    send_proactive_message_fn,
    night_analysis_fn,
    dynamic_message_poll_fn,
) -> AsyncIOScheduler:
    timezone = os.environ.get("TIMEZONE", "Europe/Berlin")
    scheduler = AsyncIOScheduler(timezone=timezone)
    schedule = load_schedule()

    for check_type, time_str in schedule.items():
        hour, minute = _parse_time(time_str)
        scheduler.add_job(
            func=send_proactive_message_fn,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
            args=[check_type],
            id=f"{check_type}_checkin",
            name=f"{CHECK_LABELS[check_type]} ({time_str})",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Job geplant: %s um %s (%s)", CHECK_LABELS[check_type], time_str, timezone)

    # Nacht-Analyse um 23:00 Uhr
    scheduler.add_job(
        func=night_analysis_fn,
        trigger=CronTrigger(hour=23, minute=0, timezone=timezone),
        id="night_analysis",
        name="Nacht-Analyse & Tagesplanung (23:00)",
        replace_existing=True,
        misfire_grace_time=600,
    )
    logger.info("Job geplant: Nacht-Analyse um 23:00 (%s)", timezone)

    # Dynamische Nachrichten: alle 5 Minuten zwischen 08:00 und 22:00 pollen
    scheduler.add_job(
        func=dynamic_message_poll_fn,
        trigger=CronTrigger(hour="8-22", minute="*/5", timezone=timezone),
        id="dynamic_message_poll",
        name="Dynamische Nachrichten (Poll alle 5 Min)",
        replace_existing=True,
        misfire_grace_time=60,
    )
    logger.info("Job geplant: Dynamischer Nachrichten-Poll (08-22 Uhr, alle 5 Min)")

    return scheduler


def reschedule_job(
    scheduler: AsyncIOScheduler,
    check_type: str,
    time_str: str,
    send_proactive_message_fn,
) -> None:
    """Ändert die Uhrzeit eines laufenden Jobs und speichert sie in schedule.json."""
    timezone = os.environ.get("TIMEZONE", "Europe/Berlin")
    hour, minute = _parse_time(time_str)

    scheduler.add_job(
        func=send_proactive_message_fn,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
        args=[check_type],
        id=f"{check_type}_checkin",
        name=f"{CHECK_LABELS[check_type]} ({time_str})",
        replace_existing=True,
        misfire_grace_time=300,
    )

    schedule = load_schedule()
    schedule[check_type] = time_str
    save_schedule(schedule)

    logger.info("Job aktualisiert: %s → %s", CHECK_LABELS[check_type], time_str)
