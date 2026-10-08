import re

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import crypto, scheduler
from ..models import Settings, db
from ..notifier import send_mail
from ..retention import apply_retention
from ..utils import filesize

bp = Blueprint("settings", __name__, url_prefix="/settings")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _int(form, name, minimum, maximum, label, errors):
    try:
        value = int(form.get(name, "").strip())
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        errors.append(f"{label}: informe um número entre {minimum} e {maximum}.")
        return None


@bp.route("/retention", methods=["GET", "POST"])
def retention():
    settings = Settings.get()
    if request.method == "POST":
        errors = []
        cron = " ".join(request.form.get("schedule_cron", "").split())
        try:
            scheduler.cron_trigger(cron)
        except ValueError as exc:
            errors.append(f"Agendamento inválido: {exc}")
        days = _int(request.form, "retention_days", 0, 3650, "Dias de retenção", errors)
        max_keep = _int(request.form, "retention_max_per_switch", 0, 10000, "Quantidade por switch", errors)

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            settings.schedule_enabled = request.form.get("schedule_enabled") == "on"
            settings.schedule_cron = cron
            settings.retention_days = days
            settings.retention_max_per_switch = max_keep
            db.session.commit()
            scheduler.apply_schedule(settings)
            flash("Configurações de agendamento e retenção salvas.", "success")
            return redirect(url_for("settings.retention"))

    return render_template("settings/retention.html", settings=settings, next_run=scheduler.next_run_time())


@bp.post("/retention/apply")
def retention_apply():
    removed, freed = apply_retention()
    flash(f"Retenção aplicada: {removed} registro(s) removido(s), {filesize(freed)} liberados.", "success")
    return redirect(url_for("settings.retention"))


@bp.route("/alerts", methods=["GET", "POST"])
def alerts():
    settings = Settings.get()
    if request.method == "POST":
        form = request.form
        errors = []
        host = form.get("smtp_host", "").strip()
        port = _int(form, "smtp_port", 1, 65535, "Porta SMTP", errors)
        security = form.get("smtp_security", "starttls")
        if security not in {"none", "starttls", "ssl"}:
            errors.append("Tipo de segurança inválido.")
        stale_days = _int(form, "alert_stale_days", 0, 365, "Dias sem backup", errors)
        sender = form.get("smtp_from", "").strip()
        if sender and not EMAIL_RE.match(re.sub(r"^.*<(.+)>$", r"\1", sender)):
            errors.append("Remetente inválido.")
        recipients = [r for r in re.split(r"[,;\s]+", form.get("smtp_recipients", "")) if r]
        bad = [r for r in recipients if not EMAIL_RE.match(r)]
        if bad:
            errors.append("Destinatário(s) inválido(s): " + ", ".join(bad))
        enabled = form.get("smtp_enabled") == "on"
        if enabled and (not host or not recipients):
            errors.append("Para ativar os alertas informe o servidor SMTP e ao menos um destinatário.")

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            settings.smtp_enabled = enabled
            settings.smtp_host = host or None
            settings.smtp_port = port
            settings.smtp_security = security
            settings.smtp_verify_tls = form.get("smtp_verify_tls") == "on"
            settings.smtp_username = form.get("smtp_username", "").strip() or None
            if form.get("smtp_password_clear") == "on":
                settings.smtp_password_enc = None
            elif form.get("smtp_password"):
                settings.smtp_password_enc = crypto.encrypt(form.get("smtp_password"))
            settings.smtp_from = sender or None
            settings.smtp_recipients = ", ".join(recipients) or None
            settings.alert_on_failure = form.get("alert_on_failure") == "on"
            settings.alert_on_success = form.get("alert_on_success") == "on"
            settings.alert_on_change = form.get("alert_on_change") == "on"
            settings.alert_stale_days = stale_days
            db.session.commit()
            flash("Configurações de alerta salvas.", "success")
            return redirect(url_for("settings.alerts"))

    return render_template("settings/alerts.html", settings=settings)


@bp.post("/alerts/test")
def alerts_test():
    settings = Settings.get()
    try:
        send_mail(
            settings,
            "E-mail de teste",
            "Este é um e-mail de teste do Switch Safe.\n\nSe você recebeu esta mensagem, "
            "os alertas de backup estão configurados corretamente.\n\n— Switch Safe",
        )
    except Exception as exc:  # noqa: BLE001 - mostrar o erro ao usuário
        flash(f"Falha ao enviar e-mail de teste: {exc}", "error")
    else:
        flash("E-mail de teste enviado para " + ", ".join(settings.recipient_list) + ".", "success")
    return redirect(url_for("settings.alerts"))
