import re
import shutil

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func

from .. import crypto, scheduler
from ..backup import backup_root
from ..devices import DEFAULT_DEVICE_TYPE, DEVICE_TYPES, default_port
from ..models import Backup, Switch, db, utcnow

bp = Blueprint("switches", __name__, url_prefix="/switches")

HOST_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,255}$")


def _get_or_404(switch_id):
    sw = db.session.get(Switch, switch_id)
    if sw is None:
        abort(404)
    return sw


def _values_from_switch(sw):
    return {
        "name": sw.name,
        "host": sw.host,
        "port": sw.port,
        "device_type": sw.device_type,
        "username": sw.username,
        "requires_enable": sw.requires_enable,
        "command": sw.command or "",
        "description": sw.description or "",
        "enabled": sw.enabled,
    }


def _values_from_form(form):
    values = {k: form.get(k, "").strip() for k in ("name", "host", "port", "device_type", "username", "command", "description")}
    values["requires_enable"] = form.get("requires_enable") == "on"
    values["enabled"] = form.get("enabled") == "on"
    return values


def _apply_form(sw, form, is_new):
    v = _values_from_form(form)
    password = form.get("password", "")
    enable_password = form.get("enable_password", "")
    errors = []

    if not v["name"]:
        errors.append("Informe o nome do switch.")
    elif Switch.query.filter(Switch.name == v["name"], Switch.id != (sw.id or 0)).first():
        errors.append("Já existe um switch com esse nome.")
    if not HOST_RE.match(v["host"]):
        errors.append("Endereço IP / hostname inválido.")
    if v["device_type"] not in DEVICE_TYPES:
        errors.append("Selecione um tipo de equipamento válido.")
    port = default_port(v["device_type"])
    if v["port"]:
        try:
            port = int(v["port"])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            errors.append("Porta inválida.")
    if not v["username"]:
        errors.append("Informe o usuário de acesso.")
    if is_new and not password:
        errors.append("Informe a senha de acesso.")
    if v["requires_enable"] and not enable_password and not sw.enable_password_enc:
        errors.append("Informe a senha de enable ou desmarque a opção.")
    if errors:
        return errors

    sw.name = v["name"]
    sw.host = v["host"]
    sw.port = port
    sw.device_type = v["device_type"]
    sw.username = v["username"]
    sw.command = v["command"] or None
    sw.description = v["description"] or None
    sw.enabled = v["enabled"]
    if password:
        sw.password_enc = crypto.encrypt(password)
    if not v["requires_enable"]:
        sw.enable_password_enc = None
    elif enable_password:
        sw.enable_password_enc = crypto.encrypt(enable_password)
    sw.updated_at = utcnow()
    return []


@bp.get("/", endpoint="list")
def index():
    switches = Switch.query.order_by(Switch.name).all()
    counts = dict(
        db.session.query(Backup.switch_id, func.count(Backup.id))
        .filter(Backup.status == "success")
        .group_by(Backup.switch_id)
        .all()
    )
    return render_template("switches/list.html", switches=switches, counts=counts)


@bp.route("/new", methods=["GET", "POST"])
def create():
    sw = Switch()
    if request.method == "POST":
        errors = _apply_form(sw, request.form, is_new=True)
        if not errors:
            db.session.add(sw)
            db.session.commit()
            flash(f"Switch {sw.name} cadastrado.", "success")
            if request.form.get("run_now") == "on":
                scheduler.run_now([sw.id], trigger="manual")
                flash("Primeiro backup iniciado em segundo plano.", "info")
            return redirect(url_for("switches.list"))
        for e in errors:
            flash(e, "error")
        values = _values_from_form(request.form)
    else:
        values = {"device_type": DEFAULT_DEVICE_TYPE, "enabled": True, "port": ""}
    return render_template("switches/form.html", sw=None, values=values)


@bp.route("/<int:switch_id>/edit", methods=["GET", "POST"])
def edit(switch_id):
    sw = _get_or_404(switch_id)
    if request.method == "POST":
        errors = _apply_form(sw, request.form, is_new=False)
        if not errors:
            db.session.commit()
            flash(f"Switch {sw.name} atualizado.", "success")
            return redirect(url_for("switches.list"))
        db.session.rollback()
        for e in errors:
            flash(e, "error")
        values = _values_from_form(request.form)
    else:
        values = _values_from_switch(sw)
    return render_template("switches/form.html", sw=sw, values=values)


@bp.post("/<int:switch_id>/delete")
def delete(switch_id):
    sw = _get_or_404(switch_id)
    name = sw.name
    folder = backup_root() / f"switch_{sw.id}"
    db.session.delete(sw)
    db.session.commit()
    shutil.rmtree(folder, ignore_errors=True)
    flash(f"Switch {name} e seus backups foram removidos.", "success")
    return redirect(url_for("switches.list"))


@bp.post("/<int:switch_id>/run")
def run(switch_id):
    sw = _get_or_404(switch_id)
    scheduler.run_now([sw.id], trigger="manual")
    flash(f"Backup de {sw.name} iniciado em segundo plano. Atualize a página em alguns segundos.", "info")
    return redirect(request.referrer or url_for("switches.list"))


@bp.post("/run-all")
def run_all():
    scheduler.run_now(None, trigger="manual")
    flash("Backup de todos os switches ativos iniciado em segundo plano.", "info")
    return redirect(request.referrer or url_for("main.dashboard"))
