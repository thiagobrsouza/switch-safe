"""Servidor FTP embutido para receber os backups dos firewalls.

Cada firewall autentica com o próprio usuário (senha guardada como hash Argon2),
fica restrito ao seu diretório e só pode listar e enviar arquivos.
"""

import logging
import os
import socket
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from pyftpdlib.authorizers import AuthenticationFailed, DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from .. import crypto
from ..models import db, utcnow
from . import storage
from .models import Firewall

log = logging.getLogger(__name__)

# e = entrar em diretório, l = listar, w = gravar. Sem leitura, remoção ou renomeação.
FTP_PERMS = "elw"
MAX_FAILURES = 5
FAILURE_WINDOW = 600

_server = None


def parse_port_range(value: str) -> range:
    start, _, end = value.partition("-")
    start = int(start)
    end = int(end or start)
    return range(start, end + 1)


class FirewallAuthorizer(DummyAuthorizer):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._failures = defaultdict(deque)
        self._lock = threading.Lock()

    def _blocked(self, ip):
        now = time.monotonic()
        with self._lock:
            attempts = self._failures[ip]
            while attempts and now - attempts[0] > FAILURE_WINDOW:
                attempts.popleft()
            return len(attempts) >= MAX_FAILURES

    def _fail(self, ip, username, reason):
        with self._lock:
            self._failures[ip].append(time.monotonic())
        log.warning("FTP: login recusado para %r de %s (%s)", username, ip, reason)
        raise AuthenticationFailed("Authentication failed.")

    def validate_authentication(self, username, password, handler):
        ip = handler.remote_ip
        if self._blocked(ip):
            log.warning("FTP: %s bloqueado por excesso de tentativas", ip)
            raise AuthenticationFailed("Too many failed attempts. Try again later.")

        with self.app.app_context():
            fw = Firewall.query.filter_by(ftp_username=username).first()
            if not crypto.verify_password(fw.ftp_password_hash if fw else None, password):
                self._fail(ip, username, "usuário ou senha inválidos")
            if not fw.enabled:
                self._fail(ip, username, "firewall inativo")
            if not fw.ip_allowed(ip):
                self._fail(ip, username, f"IP {ip} não autorizado")
            home = storage.firewall_dir(fw)
            if not storage.ensure_firewall_dir(fw):
                raise AuthenticationFailed("Server storage not writable. Contact the administrator.")
            fw.last_login_at = utcnow()
            fw.last_login_ip = ip
            db.session.commit()
            firewall_id, name = fw.id, fw.name

        with self._lock:
            self._failures.pop(ip, None)
        if self.has_user(username):
            self.remove_user(username)
        self.add_user(username, "", str(home), perm=FTP_PERMS)
        handler.firewall_id = firewall_id
        log.info("FTP: %s (%s) conectado de %s", name, username, ip)


class FirewallFTPHandler(FTPHandler):
    firewall_id = None
    banner = "Switch Safe FTP"
    max_login_attempts = 3
    timeout = 300
    permit_foreign_addresses = False

    def on_file_received(self, file):
        app = self.authorizer.app
        try:
            with app.app_context():
                storage.register_upload(self.firewall_id, Path(file), self.remote_ip)
        except Exception:
            log.exception("FTP: falha ao registrar o arquivo %s", file)

    def on_incomplete_file_received(self, file):
        log.warning("FTP: upload incompleto descartado: %s", file)
        try:
            os.remove(file)
        except OSError:
            pass


def _resolve(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError:
        log.error("FTP: não foi possível resolver FTP_PUBLIC_HOST=%r", host)
        return host


def start_ftp_server(app):
    global _server
    if _server is not None:
        return
    cfg = app.config
    handler = type("Handler", (FirewallFTPHandler,), {})
    handler.authorizer = FirewallAuthorizer(app)
    handler.passive_ports = parse_port_range(cfg["FTP_PASSIVE_PORTS"])
    if cfg["FTP_PUBLIC_HOST"]:
        handler.masquerade_address = _resolve(cfg["FTP_PUBLIC_HOST"])
    else:
        log.warning("FTP: FTP_PUBLIC_HOST não definido; o modo passivo pode não funcionar através do Docker.")

    root = Path(cfg["FIREWALL_DIR"])
    if not storage.root_writable(root):
        log.error("FTP: a pasta %s não tem permissão de escrita para o UID %d — os firewalls não conseguirão "
                  "enviar backups. Corrija com: chown -R %d:%d <pasta do host>", root, os.getuid(), os.getuid(), os.getuid())

    logging.getLogger("pyftpdlib").setLevel(logging.WARNING)
    _server = FTPServer(("0.0.0.0", cfg["FTP_LISTEN_PORT"]), handler)
    _server.max_cons = 50
    _server.max_cons_per_ip = 5
    threading.Thread(target=_server.serve_forever, kwargs={"handle_exit": False},
                     name="ftp-server", daemon=True).start()
    log.info("FTP: servidor iniciado na porta %s (passivas %s).", cfg["FTP_LISTEN_PORT"], cfg["FTP_PASSIVE_PORTS"])


def is_running() -> bool:
    return _server is not None
