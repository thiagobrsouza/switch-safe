import difflib
import io
import zipfile

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from ..backup import resolve_backup_path
from ..models import Backup, Switch, db, utcnow
from ..utils import to_local

bp = Blueprint("backups", __name__, url_prefix="/backups")

PER_PAGE = 25


def _get_success_or_404(backup_id):
    backup = db.session.get(Backup, backup_id)
    if backup is None or backup.status != "success":
        abort(404)
    path = resolve_backup_path(backup.file_path)
    if path is None or not path.exists():
        abort(404)
    return backup, path


def _download_name(backup):
    stamp = to_local(backup.created_at).strftime("%Y%m%d-%H%M%S")
    return f"{backup.switch.slug}_{stamp}.cfg"


@bp.get("/", endpoint="list")
def index():
    switch_id = request.args.get("switch_id", type=int)
    status = request.args.get("status", "")
    query = Backup.query.order_by(Backup.created_at.desc())
    if switch_id:
        query = query.filter(Backup.switch_id == switch_id)
    if status in {"success", "failed"}:
        query = query.filter(Backup.status == status)
    page = db.paginate(query, page=request.args.get("page", 1, type=int), per_page=PER_PAGE, error_out=False)
    switches = Switch.query.order_by(Switch.name).all()
    return render_template("backups/list.html", page=page, switches=switches, switch_id=switch_id, status=status)


@bp.get("/<int:backup_id>")
def view(backup_id):
    backup, path = _get_success_or_404(backup_id)
    content = path.read_text(encoding="utf-8", errors="replace")
    return render_template("backups/view.html", backup=backup, content=content)


@bp.get("/<int:backup_id>/download")
def download(backup_id):
    backup, path = _get_success_or_404(backup_id)
    return send_file(path, mimetype="text/plain", as_attachment=True, download_name=_download_name(backup))


@bp.get("/<int:backup_id>/diff")
def diff(backup_id):
    backup, path = _get_success_or_404(backup_id)
    previous = (
        Backup.query.filter(
            Backup.switch_id == backup.switch_id,
            Backup.status == "success",
            Backup.created_at < backup.created_at,
        )
        .order_by(Backup.created_at.desc())
        .first()
    )
    lines = None
    if previous:
        prev_path = resolve_backup_path(previous.file_path)
        if prev_path and prev_path.exists():
            old = prev_path.read_text(encoding="utf-8", errors="replace").splitlines()
            new = path.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = [*difflib.unified_diff(
                old, new, fromfile=_download_name(previous), tofile=_download_name(backup), lineterm="", n=3
            )]
    return render_template("backups/diff.html", backup=backup, previous=previous, lines=lines)


@bp.post("/<int:backup_id>/delete")
def delete(backup_id):
    backup = db.session.get(Backup, backup_id)
    if backup is None:
        abort(404)
    path = resolve_backup_path(backup.file_path)
    if path and path.exists():
        path.unlink()
    db.session.delete(backup)
    db.session.commit()
    flash("Registro de backup removido.", "success")
    return redirect(request.referrer or url_for("backups.list"))


@bp.get("/latest.zip")
def latest_zip():
    """Último backup bem-sucedido de cada switch, compactado."""
    buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for sw in Switch.query.order_by(Switch.name).all():
            latest = sw.backups.filter_by(status="success").order_by(Backup.created_at.desc()).first()
            path = resolve_backup_path(latest.file_path) if latest else None
            if path and path.exists():
                zf.write(path, arcname=_download_name(latest))
                added += 1
    if not added:
        flash("Ainda não há backups para baixar.", "warning")
        return redirect(url_for("backups.list"))
    buffer.seek(0)
    stamp = to_local(utcnow()).strftime("%Y%m%d-%H%M")
    return send_file(buffer, mimetype="application/zip", as_attachment=True,
                     download_name=f"switch-safe_ultimos_{stamp}.zip")
