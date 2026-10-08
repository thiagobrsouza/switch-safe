import logging
from datetime import timedelta

from .backup import resolve_backup_path
from .models import Backup, Settings, db, utcnow

log = logging.getLogger(__name__)

# Registros de falha não têm arquivo, mas não devem crescer indefinidamente.
FAILED_LOG_KEEP = 50


def apply_retention():
    """Remove backups antigos conforme a política global.

    O backup bem-sucedido mais recente de cada switch nunca é removido,
    mesmo que ultrapasse o limite de dias.
    """
    settings = Settings.get()
    cutoff = utcnow() - timedelta(days=settings.retention_days) if settings.retention_days > 0 else None
    max_keep = settings.retention_max_per_switch
    removed, freed = 0, 0

    switch_ids = [row[0] for row in db.session.query(Backup.switch_id).distinct().all()]
    for switch_id in switch_ids:
        backups = Backup.query.filter_by(switch_id=switch_id).order_by(Backup.created_at.desc()).all()
        successes = [b for b in backups if b.status == "success"]
        failures = [b for b in backups if b.status != "success"]

        doomed = {}
        if max_keep > 0:
            doomed.update({b.id: b for b in successes[max_keep:]})
        doomed.update({b.id: b for b in failures[FAILED_LOG_KEEP:]})
        if cutoff is not None:
            doomed.update({b.id: b for b in backups if b.created_at < cutoff})
        if successes:
            doomed.pop(successes[0].id, None)

        for backup in doomed.values():
            path = resolve_backup_path(backup.file_path)
            if path and path.exists():
                freed += path.stat().st_size
                path.unlink()
            db.session.delete(backup)
            removed += 1

    db.session.commit()
    if removed:
        log.info("Retenção: %d registro(s) removido(s), %d bytes liberados.", removed, freed)
    return removed, freed
