import io
import ipaddress
import os
import re
import zipfile

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import func

from .. import crypto
from ..models import Settings, db, utcnow
from ..utils import filesize, to_local
from . import ftp_server, storage
from .models import VENDORS, Firewall, FirewallBackup, make_dirname

bp = Blueprint("firewalls", __name__, url_prefix="/firewalls")

FTP_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
RESERVED_USERS = {"anonymous", "ftp", "root", "admin"}
PER_PAGE = 25


def _get_or_404(firewall_id):
    fw = db.session.get(Firewall, firewall_id)
    if fw is None:
        abort(404)
    return fw


def ftp_info():
    cfg = current_app.config
    return {
        "running": ftp_server.is_running(),
        "host": cfg["FTP_PUBLIC_HOST"],
        "port": cfg["FTP_PORT"],
        "passive": cfg["FTP_PASSIVE_PORTS"],
        "writable": storage.root_writable(),
        "root": storage.firewalls_root(),
        "uid": os.getuid(),
    }


def _values_from_firewall(fw):
    return {
        "name": fw.name, "vendor": fw.vendor, "description": fw.description or "",
        "ftp_username": fw.ftp_username, "allowed_ips": fw.allowed_ips or "",
        "expected_hours": fw.expected_hours, "enabled": fw.enabled,
    }


def _values_from_form(form):
    values = {k: form.get(k, "").strip() for k in
              ("name", "vendor", "description", "ftp_username", "allowed_ips", "expected_hours")}
    values["enabled"] = form.get("enabled") == "on"
    return values


def _apply_form(fw, form, is_new):
    v = _values_from_form(form)
    password = form.get("ftp_password", "")
    errors = []

    dirname = make_dirname(v["name"])
    if not v["name"] or not dirname:
        errors.append("Informe o nome do firewall.")
    elif Firewall.query.filter(Firewall.name == v["name"], Firewall.id != (fw.id or 0)).first():
        errors.append("Já existe um firewall com esse nome.")
    elif Firewall.query.filter(Firewall.dirname == dirname, Firewall.id != (fw.id or 0)).first():
        errors.append("Esse nome geraria o mesmo diretório de outro firewall.")
    if v["vendor"] not in VENDORS:
        errors.append("Selecione o fabricante.")
    if not FTP_USER_RE.match(v["ftp_username"]):
        errors.append("Usuário FTP deve ter de 3 a 32 caracteres (letras, números, _ . -).")
    elif v["ftp_username"].lower() in RESERVED_USERS:
        errors.append("Esse nome de usuário FTP é reservado.")
    elif Firewall.query.filter(Firewall.ftp_username == v["ftp_username"], Firewall.id != (fw.id or 0)).first():
        errors.append("Esse usuário FTP já está em uso por outro firewall.")
    if is_new and not password:
        errors.append("Informe a senha FTP.")
    if password and len(password) < 8:
        errors.append("A senha FTP deve ter pelo menos 8 caracteres.")
    ip_entries = [e for e in re.split(r"[,;\s]+", v["allowed_ips"]) if e]
    bad = []
    for entry in ip_entries:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            bad.append(entry)
    if bad:
        errors.append("IP/rede de origem inválido: " + ", ".join(bad))
    try:
        expected = int(v["expected_hours"] or 0)
        if not 0 <= expected <= 24 * 90:
            raise ValueError
    except ValueError:
        errors.append("Intervalo esperado inválido (horas, 0 a 2160).")
        expected = 0
    if errors:
        return errors

    if is_new:
        fw.dirname = dirname
    elif fw.dirname != dirname:
        storage.rename_directory(fw, dirname)
    fw.name = v["name"]
    fw.vendor = v["vendor"]
    fw.description = v["description"] or None
    fw.ftp_username = v["ftp_username"]
    fw.allowed_ips = ", ".join(ip_entries) or None
    if fw.expected_hours != expected:
        fw.overdue_alerted = False
    fw.expected_hours = expected
    fw.enabled = v["enabled"]
    if password:
        fw.ftp_password_hash = crypto.hash_password(password)
    fw.updated_at = utcnow()
    return []


@bp.get("/", endpoint="list")
def index():
    firewalls = Firewall.query.order_by(Firewall.name).all()
    stats = dict(
        (fid, (count, size))
        for fid, count, size in db.session.query(
            FirewallBackup.firewall_id, func.count(FirewallBackup.id), func.coalesce(func.sum(FirewallBackup.size), 0)
        ).group_by(FirewallBackup.firewall_id)
    )
    return render_template("firewalls/list.html", firewalls=firewalls, stats=stats, ftp=ftp_info())


@bp.route("/new", methods=["GET", "POST"])
def create():
    fw = Firewall()
    if request.method == "POST":
        errors = _apply_form(fw, request.form, is_new=True)
        if not errors:
            db.session.add(fw)
            db.session.commit()
            if not storage.ensure_firewall_dir(fw):
                flash("Atenção: sem permissão de escrita na pasta dos firewalls; o envio por FTP vai falhar.", "error")
            flash(f"Firewall {fw.name} cadastrado. Configure o backup FTP no equipamento com os dados abaixo.", "success")
            return redirect(url_for("firewalls.view", firewall_id=fw.id))
        for e in errors:
            flash(e, "error")
        values = _values_from_form(request.form)
    else:
        values = {"vendor": "sonicwall", "expected_hours": 24, "enabled": True}
    return render_template("firewalls/form.html", fw=None, values=values, vendors=VENDORS)


@bp.route("/<int:firewall_id>/edit", methods=["GET", "POST"])
def edit(firewall_id):
    fw = _get_or_404(firewall_id)
    if request.method == "POST":
        errors = _apply_form(fw, request.form, is_new=False)
        if not errors:
            db.session.commit()
            flash(f"Firewall {fw.name} atualizado.", "success")
            return redirect(url_for("firewalls.view", firewall_id=fw.id))
        db.session.rollback()
        for e in errors:
            flash(e, "error")
        values = _values_from_form(request.form)
    else:
        values = _values_from_firewall(fw)
    return render_template("firewalls/form.html", fw=fw, values=values, vendors=VENDORS)


@bp.post("/<int:firewall_id>/delete")
def delete(firewall_id):
    fw = _get_or_404(firewall_id)
    name = fw.name
    storage.remove_directory(fw)
    db.session.delete(fw)
    db.session.commit()
    flash(f"Firewall {name} e seus backups foram removidos.", "success")
    return redirect(url_for("firewalls.list"))


@bp.get("/<int:firewall_id>")
def view(firewall_id):
    fw = _get_or_404(firewall_id)
    page = db.paginate(fw.backups.order_by(FirewallBackup.created_at.desc()),
                       page=request.args.get("page", 1, type=int), per_page=PER_PAGE, error_out=False)
    total_size = db.session.query(func.coalesce(func.sum(FirewallBackup.size), 0)).filter(
        FirewallBackup.firewall_id == fw.id).scalar()
    return render_template("firewalls/view.html", fw=fw, page=page, total_size=total_size,
                           ftp=ftp_info(), location=storage.firewall_dir(fw))


@bp.get("/backups", endpoint="backups")
def backups_index():
    firewall_id = request.args.get("firewall_id", type=int)
    query = FirewallBackup.query.order_by(FirewallBackup.created_at.desc())
    if firewall_id:
        query = query.filter(FirewallBackup.firewall_id == firewall_id)
    page = db.paginate(query, page=request.args.get("page", 1, type=int), per_page=PER_PAGE, error_out=False)
    return render_template("firewalls/backups.html", page=page, firewall_id=firewall_id,
                           firewalls=Firewall.query.order_by(Firewall.name).all())


@bp.get("/backups/<int:backup_id>/download")
def download(backup_id):
    backup = db.session.get(FirewallBackup, backup_id)
    path = storage.backup_file(backup) if backup else None
    if path is None or not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=backup.filename,
                     mimetype="application/octet-stream")


@bp.post("/backups/<int:backup_id>/delete")
def delete_backup(backup_id):
    backup = db.session.get(FirewallBackup, backup_id)
    if backup is None:
        abort(404)
    storage.delete_backup(backup)
    db.session.commit()
    flash("Arquivo de backup removido.", "success")
    return redirect(request.referrer or url_for("firewalls.backups"))


@bp.get("/backups/latest.zip")
def latest_zip():
    buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fw in Firewall.query.order_by(Firewall.name).all():
            latest = fw.backups.order_by(FirewallBackup.created_at.desc()).first()
            path = storage.backup_file(latest) if latest else None
            if path and path.exists():
                zf.write(path, arcname=f"{fw.dirname}/{latest.filename}")
                added += 1
    if not added:
        flash("Ainda não há backups de firewall para baixar.", "warning")
        return redirect(url_for("firewalls.backups"))
    buffer.seek(0)
    stamp = to_local(utcnow()).strftime("%Y%m%d-%H%M")
    return send_file(buffer, mimetype="application/zip", as_attachment=True,
                     download_name=f"switch-safe_firewalls_{stamp}.zip")


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    s = Settings.get()
    if request.method == "POST":
        errors = []
        values = {}
        for field, label, maximum in (("fw_retention_days", "Dias de retenção", 3650),
                                      ("fw_retention_max", "Quantidade por firewall", 10000)):
            try:
                values[field] = int(request.form.get(field, ""))
                if not 0 <= values[field] <= maximum:
                    raise ValueError
            except ValueError:
                errors.append(f"{label}: informe um número entre 0 e {maximum}.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            s.fw_retention_days = values["fw_retention_days"]
            s.fw_retention_max = values["fw_retention_max"]
            db.session.commit()
            flash("Configurações do módulo Firewalls salvas.", "success")
            return redirect(url_for("firewalls.settings"))
    root = storage.firewalls_root()
    return render_template("firewalls/settings.html", settings=s, ftp=ftp_info(), root=root)


@bp.post("/settings/apply-retention")
def apply_retention():
    removed, freed = storage.apply_retention()
    flash(f"Retenção aplicada: {removed} arquivo(s) removido(s), {filesize(freed)} liberados.", "success")
    return redirect(url_for("firewalls.settings"))
