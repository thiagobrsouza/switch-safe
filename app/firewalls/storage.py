import hashlib
import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path

from flask import current_app

from ..models import Settings, db, utcnow
from ..utils import localdt, to_local
from .models import Firewall, FirewallBackup

log = logging.getLogger(__name__)


def firewalls_root() -> Path:
    return Path(current_app.config["FIREWALL_DIR"]).resolve()


def firewall_dir(fw: Firewall) -> Path:
    return firewalls_root() / fw.dirname


def root_writable(root: Path | None = None) -> bool:
    root = root or firewalls_root()
    return root.is_dir() and os.access(root, os.W_OK | os.X_OK)


def ensure_firewall_dir(fw: Firewall) -> bool:
    """Cria o diretório do firewall. Retorna False (e registra o erro) se não houver permissão."""
    try:
        firewall_dir(fw).mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        log.error("Sem permissão para criar %s (%s). A pasta dos firewalls deve pertencer ao UID %d.",
                  firewall_dir(fw), exc, os.getuid())
        return False


def backup_file(backup: FirewallBackup) -> Path | None:
    root = firewalls_root()
    path = (root / backup.firewall.dirname / backup.filename).resolve()
    return path if root in path.parents else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_upload(firewall_id: int, path: Path, remote_ip: str) -> FirewallBackup | None:
    """Chamada pelo servidor FTP quando um arquivo termina de chegar."""
    fw = db.session.get(Firewall, firewall_id)
    folder = firewall_dir(fw) if fw else None
    if fw is None or path.parent.resolve() != folder.resolve():
        log.warning("Upload ignorado fora do diretório do firewall: %s", path)
        return None

    now = utcnow()
    stamp = to_local(now).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.stem}_{stamp}_{n}{path.suffix}")
        n += 1
    path.rename(target)

    digest = _sha256(target)
    previous = fw.backups.order_by(FirewallBackup.created_at.desc()).first()
    backup = FirewallBackup(
        firewall_id=fw.id,
        created_at=now,
        filename=target.name,
        original_name=path.name,
        size=target.stat().st_size,
        sha256=digest,
        changed=previous is not None and previous.sha256 != digest,
        remote_ip=remote_ip,
    )
    fw.last_backup_at = now
    fw.overdue_alerted = False
    db.session.add(backup)
    db.session.commit()
    log.info("Backup recebido de %s: %s (%d bytes)", fw.name, target.name, backup.size)
    apply_retention(fw.id)
    return backup


def delete_backup(backup: FirewallBackup) -> int:
    path = backup_file(backup)
    size = 0
    if path and path.exists():
        size = path.stat().st_size
        path.unlink()
    db.session.delete(backup)
    return size


def apply_retention(firewall_id: int | None = None):
    """Mesma política dos switches: por dias e/ou quantidade, preservando o mais recente."""
    settings = Settings.get()
    cutoff = utcnow() - timedelta(days=settings.fw_retention_days) if settings.fw_retention_days > 0 else None
    max_keep = settings.fw_retention_max
    query = Firewall.query if firewall_id is None else Firewall.query.filter_by(id=firewall_id)
    removed, freed = 0, 0
    for fw in query.all():
        backups = fw.backups.order_by(FirewallBackup.created_at.desc()).all()
        doomed = {}
        if max_keep > 0:
            doomed.update({b.id: b for b in backups[max_keep:]})
        if cutoff is not None:
            doomed.update({b.id: b for b in backups if b.created_at < cutoff})
        if backups:
            doomed.pop(backups[0].id, None)
        for backup in doomed.values():
            freed += delete_backup(backup)
            removed += 1
    db.session.commit()
    if removed:
        log.info("Retenção (firewalls): %d arquivo(s) removido(s), %d bytes liberados.", removed, freed)
    return removed, freed


def rename_directory(fw: Firewall, new_dirname: str) -> None:
    old = firewall_dir(fw)
    new = firewalls_root() / new_dirname
    if old.exists() and not new.exists():
        old.rename(new)
    fw.dirname = new_dirname


def remove_directory(fw: Firewall) -> None:
    shutil.rmtree(firewall_dir(fw), ignore_errors=True)


def check_overdue():
    """Alerta (uma vez) os firewalls cujo backup não chegou dentro do intervalo esperado."""
    from ..notifier import send_mail

    settings = Settings.get()
    overdue = [fw for fw in Firewall.query.order_by(Firewall.name).all() if fw.is_overdue and not fw.overdue_alerted]
    if not overdue:
        return []
    if settings.smtp_enabled and settings.alert_on_failure:
        lines = ["Os firewalls abaixo não enviaram backup dentro do intervalo esperado:", ""]
        for fw in overdue:
            last = localdt(fw.last_backup_at) if fw.last_backup_at else "nunca recebido"
            lines.append(f"  - {fw.name} (esperado a cada {fw.expected_hours} h) — último backup: {last}")
        lines += ["", "Verifique o agendamento de backup FTP no firewall.", "", "— Switch Safe"]
        try:
            send_mail(settings, f"Backup de {len(overdue)} firewall(s) não recebido", "\n".join(lines))
        except Exception:
            log.exception("Falha ao enviar alerta de firewalls atrasados")
            return overdue
    for fw in overdue:
        fw.overdue_alerted = True
    db.session.commit()
    log.warning("Firewalls sem backup no prazo: %s", ", ".join(fw.name for fw in overdue))
    return overdue
