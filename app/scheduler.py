import atexit
import logging
import re
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import current_app

from .utils import get_tz

log = logging.getLogger(__name__)

JOB_ID = "scheduled_backup"
_scheduler: BackgroundScheduler | None = None
_app = None

_DOW_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def cron_trigger(expression: str) -> CronTrigger:
    """Cria um CronTrigger a partir de uma expressão cron padrão (5 campos).

    Converte o dia da semana numérico do cron (0/7 = domingo) para nomes, pois o
    APScheduler 3 usa 0 = segunda-feira.
    """
    fields = (expression or "").split()
    if len(fields) != 5:
        raise ValueError("A expressão deve ter 5 campos: minuto hora dia mês dia-da-semana.")
    minute, hour, day, month, dow = fields
    dow = re.sub(r"(?<![/\d])\d+", lambda m: _DOW_NAMES[int(m.group()) % 7], dow)
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow, timezone=get_tz())


def _scheduled_job():
    from .backup import run_backups

    with _app.app_context():
        run_backups(trigger="agendado")


def _manual_job(app, switch_ids, trigger):
    from .backup import run_backups

    with app.app_context():
        run_backups(switch_ids=switch_ids, trigger=trigger)


def _retention_job():
    from .firewalls import storage as firewall_storage
    from .retention import apply_retention

    with _app.app_context():
        apply_retention()
        firewall_storage.apply_retention()


def _firewall_check_job():
    from .firewalls import storage as firewall_storage

    with _app.app_context():
        firewall_storage.check_overdue()


def init_scheduler(app):
    global _scheduler, _app
    if _scheduler is not None:
        return
    _app = app
    _scheduler = BackgroundScheduler(
        timezone=get_tz(),
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))

    from .models import Settings

    with app.app_context():
        apply_schedule(Settings.get())
    _scheduler.add_job(_retention_job, CronTrigger(hour=4, minute=30, timezone=get_tz()),
                       id="daily_retention", replace_existing=True)
    _scheduler.add_job(_firewall_check_job, "interval", seconds=app.config["FIREWALL_CHECK_SECONDS"],
                       id="firewall_overdue_check", replace_existing=True)
    log.info("Agendador iniciado (fuso %s).", get_tz())


def apply_schedule(settings):
    if _scheduler is None:
        return
    if settings.schedule_enabled:
        try:
            trigger = cron_trigger(settings.schedule_cron)
        except ValueError:
            log.error("Expressão cron inválida: %r", settings.schedule_cron)
            return
        _scheduler.add_job(_scheduled_job, trigger, id=JOB_ID, replace_existing=True)
        log.info("Backup agendado: %s", settings.schedule_cron)
    elif _scheduler.get_job(JOB_ID):
        _scheduler.remove_job(JOB_ID)
        log.info("Backup agendado desativado.")


def run_now(switch_ids=None, trigger="manual"):
    app = _app or current_app._get_current_object()
    if _scheduler is not None:
        _scheduler.add_job(_manual_job, args=[app, switch_ids, trigger])
    else:
        threading.Thread(target=_manual_job, args=(app, switch_ids, trigger), daemon=True).start()


def next_run_time():
    if _scheduler is None:
        return None
    job = _scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None
