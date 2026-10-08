import logging
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from .. import crypto
from ..models import User, db, utcnow
from ..utils import safe_next, validate_password, validate_username

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 600
_attempts = defaultdict(deque)
_attempts_lock = threading.Lock()


def _client_key():
    return request.remote_addr or "unknown"


def _is_blocked(key):
    now = time.monotonic()
    with _attempts_lock:
        attempts = _attempts[key]
        while attempts and now - attempts[0] > WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= MAX_ATTEMPTS


def _register_failure(key):
    with _attempts_lock:
        _attempts[key].append(time.monotonic())


def _clear_failures(key):
    with _attempts_lock:
        _attempts.pop(key, None)


def _start_session(user):
    session.clear()
    login_user(user)
    session.permanent = True


@bp.route("/login", methods=["GET", "POST"])
def login():
    if User.query.count() == 0:
        return redirect(url_for("auth.setup"))
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        key = _client_key()
        if _is_blocked(key):
            flash("Muitas tentativas de login. Aguarde alguns minutos.", "error")
            return render_template("auth/login.html"), 429

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if crypto.verify_password(user.password_hash if user else None, password):
            _clear_failures(key)
            if crypto.needs_rehash(user.password_hash):
                user.password_hash = crypto.hash_password(password)
            user.last_login_at = utcnow()
            db.session.commit()
            _start_session(user)
            log.info("Login de %s a partir de %s", user.username, key)
            return redirect(safe_next(request.args.get("next")) or url_for("main.dashboard"))

        _register_failure(key)
        log.warning("Falha de login para %r a partir de %s", username, key)
        flash("Usuário ou senha inválidos.", "error")

    return render_template("auth/login.html")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Criação do primeiro administrador. Fica indisponível depois que existe um usuário."""
    if User.query.count() > 0:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = validate_username(username) or validate_password(password, request.form.get("confirm", ""))
        if error:
            flash(error, "error")
        else:
            user = User(username=username, password_hash=crypto.hash_password(password))
            db.session.add(user)
            db.session.commit()
            _start_session(user)
            flash("Administrador criado. Bem-vindo ao Switch Safe!", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("auth/setup.html")


@bp.post("/logout")
def logout():
    logout_user()
    session.clear()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("auth.login"))
