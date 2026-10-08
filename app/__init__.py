import logging
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, request, url_for
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.middleware.proxy_fix import ProxyFix

from . import crypto, utils
from .devices import DEVICE_TYPES
from .models import Settings, User, db

__version__ = "1.0.0"

login_manager = LoginManager()
csrf = CSRFProtect()

PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "static", "health"}


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    if isinstance(dbapi_conn, sqlite3.Connection):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


@login_manager.user_loader
def _load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def create_app():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    backup_dir = data_dir / "backups"
    firewall_dir = data_dir / "firewalls"
    keys_dir = data_dir / "keys"
    backup_dir.mkdir(parents=True, exist_ok=True)
    firewall_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=crypto.load_secret_key(keys_dir),
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(data_dir / 'switchsafe.db').as_posix()}",
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"check_same_thread": False, "timeout": 30}},
        BACKUP_DIR=str(backup_dir),
        BACKUP_WORKERS=int(os.environ.get("BACKUP_WORKERS", "4")),
        FIREWALL_DIR=str(firewall_dir),
        FIREWALL_CHECK_SECONDS=int(os.environ.get("FIREWALL_CHECK_SECONDS", "3600")),
        FTP_ENABLED=_env_bool("FTP_ENABLED", True),
        FTP_LISTEN_PORT=int(os.environ.get("FTP_LISTEN_PORT", "2121")),
        FTP_PORT=int(os.environ.get("FTP_PORT", "21")),
        FTP_PUBLIC_HOST=os.environ.get("FTP_PUBLIC_HOST", "").strip(),
        FTP_PASSIVE_PORTS=os.environ.get("FTP_PASSIVE_PORTS", "30000-30009").strip(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_bool("SESSION_COOKIE_SECURE"),
        SESSION_COOKIE_NAME="switchsafe_session",
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=int(os.environ.get("SESSION_LIFETIME_MINUTES", "480"))),
        WTF_CSRF_TIME_LIMIT=None,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )

    if _env_bool("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    crypto.init_encryption(keys_dir)
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .firewalls import models as _firewall_models  # noqa: F401 - registra as tabelas do módulo
    from .migrate import auto_migrate

    with app.app_context():
        db.create_all()
        auto_migrate(db)
        Settings.get()

    from .views import register_blueprints

    register_blueprints(app)

    app.jinja_env.filters["localdt"] = utils.localdt
    app.jinja_env.filters["filesize"] = utils.filesize

    @app.context_processor
    def _inject_globals():
        return {"app_version": __version__, "device_types": DEVICE_TYPES}

    @app.before_request
    def _require_login():
        if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    if _env_bool("SCHEDULER_ENABLED", True):
        from .scheduler import init_scheduler

        init_scheduler(app)

    if app.config["FTP_ENABLED"]:
        from .firewalls.ftp_server import start_ftp_server

        start_ftp_server(app)

    return app
