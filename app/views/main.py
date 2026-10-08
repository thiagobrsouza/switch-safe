from flask import Blueprint, render_template
from sqlalchemy import func

from ..models import Backup, Settings, Switch, db
from ..scheduler import next_run_time

bp = Blueprint("main", __name__)


@bp.get("/")
def dashboard():
    switches = Switch.query.order_by(Switch.name).all()
    stats = {
        "total": len(switches),
        "enabled": sum(1 for s in switches if s.enabled),
        "failing": sum(1 for s in switches if s.enabled and s.last_status == "failed"),
        "backups": Backup.query.filter_by(status="success").count(),
        "storage": db.session.query(func.coalesce(func.sum(Backup.size), 0)).scalar(),
    }
    recent = Backup.query.order_by(Backup.created_at.desc()).limit(10).all()
    return render_template(
        "dashboard.html",
        switches=switches,
        stats=stats,
        recent=recent,
        settings=Settings.get(),
        next_run=next_run_time(),
    )
