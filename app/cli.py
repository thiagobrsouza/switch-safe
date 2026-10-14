"""Comandos de manutenção, executados dentro do container.

    docker exec -it switch-safe python -m app.cli list-users
    docker exec -it switch-safe python -m app.cli reset-password [usuario]

A nova senha é digitada no terminal (sem eco) e gravada como hash Argon2.
"""

import getpass
import os
import sys

# Não iniciar agendador nem servidor FTP: o container principal já os executa.
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["FTP_ENABLED"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"

from . import create_app, crypto  # noqa: E402
from .models import User, db  # noqa: E402
from .utils import localdt, validate_password  # noqa: E402

USAGE = __doc__


def list_users():
    users = User.query.order_by(User.username).all()
    if not users:
        print("Nenhum usuário cadastrado. Acesse a aplicação para criar o administrador.")
        return 0
    for user in users:
        print(f"  {user.username:<30} último login: {localdt(user.last_login_at)}")
    return 0


def reset_password(username=None):
    users = User.query.order_by(User.username).all()
    if not users:
        print("Nenhum usuário cadastrado. Acesse a aplicação para criar o administrador.")
        return 1
    if username is None:
        if len(users) > 1:
            print("Informe qual usuário. Usuários existentes:")
            list_users()
            return 1
        username = users[0].username
    user = User.query.filter_by(username=username).first()
    if user is None:
        print(f"Usuário '{username}' não encontrado. Usuários existentes:")
        list_users()
        return 1

    print(f"Redefinindo a senha de '{user.username}'.")
    for _ in range(3):
        password = getpass.getpass("Nova senha: ")
        confirm = getpass.getpass("Confirme a nova senha: ")
        error = validate_password(password, confirm)
        if not error:
            user.password_hash = crypto.hash_password(password)
            db.session.commit()
            print("Senha alterada. Faça login com a nova senha.")
            return 0
        print(error)
    print("Nenhuma alteração feita.")
    return 1


def main(argv):
    if not argv or argv[0] not in {"list-users", "reset-password"}:
        print(USAGE)
        return 2
    app = create_app()
    with app.app_context():
        if argv[0] == "list-users":
            return list_users()
        return reset_password(argv[1] if len(argv) > 1 else None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
