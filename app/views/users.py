from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, logout_user

from .. import crypto
from ..models import User, db
from ..utils import validate_password, validate_username

bp = Blueprint("users", __name__, url_prefix="/users")


@bp.get("/", endpoint="list")
def index():
    users = User.query.order_by(User.username).all()
    return render_template("users/list.html", users=users)


@bp.post("/new")
def create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    error = validate_username(username) or validate_password(password, request.form.get("confirm", ""))
    if not error and User.query.filter_by(username=username).first():
        error = "Já existe um usuário com esse nome."
    if error:
        flash(error, "error")
    else:
        db.session.add(User(username=username, password_hash=crypto.hash_password(password)))
        db.session.commit()
        flash(f"Usuário {username} criado.", "success")
    return redirect(url_for("users.list"))


@bp.post("/<int:user_id>/password")
def change_password(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id and not crypto.verify_password(
        user.password_hash, request.form.get("current_password", "")
    ):
        flash("Senha atual incorreta.", "error")
        return redirect(url_for("users.list"))
    password = request.form.get("password", "")
    error = validate_password(password, request.form.get("confirm", ""))
    if error:
        flash(error, "error")
    else:
        user.password_hash = crypto.hash_password(password)
        db.session.commit()
        flash(f"Senha de {user.username} alterada.", "success")
    return redirect(url_for("users.list"))


@bp.post("/<int:user_id>/delete")
def delete(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if User.query.count() <= 1:
        flash("Não é possível remover o único usuário.", "error")
        return redirect(url_for("users.list"))
    is_self = user.id == current_user.id
    username = user.username
    db.session.delete(user)
    db.session.commit()
    if is_self:
        logout_user()
        return redirect(url_for("auth.login"))
    flash(f"Usuário {username} removido.", "success")
    return redirect(url_for("users.list"))
