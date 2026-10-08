import logging
import smtplib
import socket
import ssl
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import crypto
from .models import Settings, Switch, utcnow
from .utils import localdt

log = logging.getLogger(__name__)


def send_mail(settings: Settings, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise ValueError("Servidor SMTP não configurado.")
    recipients = settings.recipient_list
    if not recipients:
        raise ValueError("Nenhum destinatário configurado.")
    sender = settings.smtp_from or settings.smtp_username
    if not sender:
        raise ValueError("Informe o remetente (From).")

    msg = EmailMessage()
    msg["Subject"] = f"[Switch Safe] {subject}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="switch-safe.local")
    msg.set_content(body)

    context = ssl.create_default_context()
    if not settings.smtp_verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    if settings.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)

    with server:
        server.ehlo()
        if settings.smtp_security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, crypto.decrypt(settings.smtp_password_enc))
        server.send_message(msg)


def _stale_switches(days: int):
    limit = utcnow() - timedelta(days=days)
    return [
        sw
        for sw in Switch.query.filter(Switch.enabled.is_(True)).order_by(Switch.name).all()
        if sw.last_backup_at is not None and (sw.last_success_at or sw.created_at) < limit
    ]


def notify_run(records, trigger: str) -> None:
    settings = Settings.get()
    if not settings.smtp_enabled:
        return

    failed = [b for b in records if b.status != "success"]
    succeeded = [b for b in records if b.status == "success"]
    changed = [b for b in succeeded if b.changed]
    stale = _stale_switches(settings.alert_stale_days) if settings.alert_stale_days > 0 else []

    send = (
        (failed and settings.alert_on_failure)
        or stale
        or (changed and settings.alert_on_change)
        or (settings.alert_on_success and not failed)
    )
    if not send:
        return

    parts = []
    if failed:
        parts.append(f"FALHA no backup de {len(failed)} switch(es)")
    if stale:
        parts.append(f"{len(stale)} switch(es) sem backup recente")
    if changed and settings.alert_on_change:
        parts.append(f"Configuração alterada em {len(changed)} switch(es)")
    subject = " · ".join(parts) or f"Backup concluído com sucesso ({len(succeeded)} switch(es))"

    lines = [
        f"Execução: {trigger}",
        f"Data: {localdt(utcnow())}",
        f"Servidor: {socket.gethostname()}",
        f"Resultado: {len(succeeded)} sucesso(s), {len(failed)} falha(s)",
        "",
    ]
    if failed:
        lines.append("FALHAS:")
        for b in failed:
            lines.append(f"  - {b.switch.name} ({b.switch.host}): {b.error}")
        lines.append("")
    if stale:
        lines.append(f"SEM BACKUP BEM-SUCEDIDO HÁ MAIS DE {settings.alert_stale_days} DIA(S):")
        for sw in stale:
            lines.append(f"  - {sw.name} ({sw.host}) — último sucesso: {localdt(sw.last_success_at)}")
        lines.append("")
    if changed and settings.alert_on_change:
        lines.append("CONFIGURAÇÃO ALTERADA DESDE O BACKUP ANTERIOR:")
        for b in changed:
            lines.append(f"  - {b.switch.name} ({b.switch.host})")
        lines.append("")
    if succeeded and settings.alert_on_success:
        lines.append("SUCESSO:")
        for b in succeeded:
            lines.append(f"  - {b.switch.name} ({b.switch.host}) — {b.size} bytes")
        lines.append("")
    lines.append("— Switch Safe")

    send_mail(settings, subject, "\n".join(lines))
    log.info("Alerta enviado: %s", subject)
