import hashlib
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import InvalidToken
from flask import current_app
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from . import crypto
from .models import Backup, Settings, Switch, db, utcnow
from .utils import to_local

log = logging.getLogger(__name__)

# Uma execução por vez; execuções manuais aguardam a agendada terminar.
_run_lock = threading.Lock()

CONNECT_TIMEOUT = 30
READ_TIMEOUT = 180

ERROR_MARKERS = (
    "% invalid", "% unknown", "% incomplete", "% ambiguous", "% bad",
    "invalid input", "unrecognized command", "syntax error", "error:",
)

# Linhas que mudam a cada coleta sem representar mudança real de configuração.
VOLATILE_LINES = re.compile(
    r"^(! Last configuration change|! NVRAM config last updated|! No configuration change"
    r"|ntp clock-period|Current configuration\s*:|Building configuration"
    r"|#.*by RouterOS|## Last (commit|changed))",
    re.IGNORECASE,
)


class BackupError(Exception):
    pass


@dataclass
class DeviceJob:
    switch_id: int
    name: str
    device_type: str
    host: str
    port: int
    username: str
    password: str
    enable_password: str | None
    command: str


@dataclass
class FetchResult:
    ok: bool
    output: str | None = None
    error: str | None = None
    duration: float = 0.0


def fetch_config(job: DeviceJob) -> FetchResult:
    """Conecta no equipamento e coleta a configuração. Nunca levanta exceção."""
    started = time.monotonic()

    def result(ok, output=None, error=None):
        return FetchResult(ok, output, error, round(time.monotonic() - started, 2))

    params = {
        "device_type": job.device_type,
        "host": job.host,
        "port": job.port,
        "username": job.username,
        "password": job.password,
        "timeout": CONNECT_TIMEOUT,
        "conn_timeout": CONNECT_TIMEOUT,
        "auth_timeout": CONNECT_TIMEOUT,
        "banner_timeout": CONNECT_TIMEOUT,
    }
    if job.enable_password:
        params["secret"] = job.enable_password

    try:
        socket.getaddrinfo(job.host, job.port)
    except socket.gaierror:
        return result(False, error=f"Host não encontrado: {job.host} (falha na resolução DNS).")

    try:
        with ConnectHandler(**params) as conn:
            if job.enable_password:
                try:
                    conn.enable()
                except Exception as exc:
                    raise BackupError("Falha ao entrar no modo enable. Verifique a senha de enable.") from exc
            output = conn.send_command(job.command, read_timeout=READ_TIMEOUT)
    except BackupError as exc:
        return result(False, error=str(exc))
    except NetmikoAuthenticationException:
        return result(False, error="Falha de autenticação: usuário ou senha incorretos.")
    except NetmikoTimeoutException:
        return result(False, error=f"Tempo esgotado ao conectar em {job.host}:{job.port}.")
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira registro de erro
        log.exception("Erro inesperado no backup de %s", job.name)
        return result(False, error=f"{type(exc).__name__}: {exc}")

    output = (output or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    problem = _detect_error(output)
    if problem:
        return result(False, error=problem)
    return result(True, output=output + "\n")


def _detect_error(output: str) -> str | None:
    stripped = output.strip()
    if not stripped:
        return "O equipamento retornou uma saída vazia."
    lines = [line.strip().lower() for line in stripped.splitlines() if line.strip()]
    if len(lines) <= 5 and any(line.startswith(m) for line in lines for m in ERROR_MARKERS):
        return ("O equipamento rejeitou o comando (verifique se ele exige enable e se o comando é suportado): "
                + stripped[:300])
    return None


def config_hash(text: str) -> str:
    relevant = "\n".join(line for line in text.splitlines() if not VOLATILE_LINES.match(line.strip()))
    return hashlib.sha256(relevant.encode("utf-8")).hexdigest()


def backup_root() -> Path:
    return Path(current_app.config["BACKUP_DIR"]).resolve()


def resolve_backup_path(relative: str | None) -> Path | None:
    if not relative:
        return None
    base = backup_root()
    path = (base / relative).resolve()
    if base not in path.parents:
        return None
    return path


def _build_job(sw: Switch) -> DeviceJob:
    return DeviceJob(
        switch_id=sw.id,
        name=sw.name,
        device_type=sw.device_type,
        host=sw.host,
        port=sw.port,
        username=sw.username,
        password=crypto.decrypt(sw.password_enc),
        enable_password=crypto.decrypt(sw.enable_password_enc) or None,
        command=sw.effective_command,
    )


def _write_file(sw: Switch, when, data: bytes) -> str:
    base = backup_root()
    folder = base / f"switch_{sw.id}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = to_local(when).strftime("%Y%m%d-%H%M%S")
    path = folder / f"{sw.slug}_{stamp}.cfg"
    n = 1
    while path.exists():
        path = folder / f"{sw.slug}_{stamp}_{n}.cfg"
        n += 1
    path.write_bytes(data)
    return path.relative_to(base).as_posix()


def _store_result(sw: Switch, res: FetchResult, trigger: str) -> Backup:
    now = utcnow()
    backup = Backup(switch_id=sw.id, created_at=now, trigger=trigger, duration=res.duration)

    if res.ok:
        previous = (
            Backup.query.filter_by(switch_id=sw.id, status="success")
            .order_by(Backup.created_at.desc())
            .first()
        )
        data = res.output.encode("utf-8")
        digest = config_hash(res.output)
        backup.status = "success"
        backup.file_path = _write_file(sw, now, data)
        backup.size = len(data)
        backup.config_hash = digest
        backup.changed = previous is not None and previous.config_hash != digest
        sw.last_status = "success"
        sw.last_success_at = now
        sw.last_error = None
    else:
        backup.status = "failed"
        backup.error = res.error
        sw.last_status = "failed"
        sw.last_error = res.error

    sw.last_backup_at = now
    db.session.add(backup)
    return backup


def run_backups(switch_ids=None, trigger="agendado"):
    """Executa o backup dos switches (todos os ativos, ou os ids informados).

    Deve ser chamada dentro de um app context.
    """
    from .notifier import notify_run
    from .retention import apply_retention

    with _run_lock:
        query = Switch.query.order_by(Switch.name)
        if switch_ids is None:
            query = query.filter(Switch.enabled.is_(True))
        else:
            query = query.filter(Switch.id.in_(switch_ids))
        switches = query.all()
        if not switches:
            log.info("Nenhum switch para executar backup (%s).", trigger)
            return []

        log.info("Iniciando backup de %d switch(es) (%s).", len(switches), trigger)
        jobs, results = {}, {}
        for sw in switches:
            try:
                jobs[sw.id] = _build_job(sw)
            except InvalidToken:
                results[sw.id] = FetchResult(
                    False, error="Não foi possível descriptografar as credenciais. A chave de criptografia mudou?"
                )

        if jobs:
            workers = max(1, min(current_app.config["BACKUP_WORKERS"], len(jobs)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="backup") as pool:
                futures = {sid: pool.submit(fetch_config, job) for sid, job in jobs.items()}
                for sid, future in futures.items():
                    results[sid] = future.result()

        records = [_store_result(sw, results[sw.id], trigger) for sw in switches]
        ok = sum(1 for r in records if r.status == "success")
        settings = Settings.get()
        settings.last_run_at = utcnow()
        settings.last_run_summary = f"{ok} sucesso(s), {len(records) - ok} falha(s) — {trigger}"
        db.session.commit()
        log.info("Backup concluído: %s", settings.last_run_summary)

        try:
            apply_retention()
        except Exception:
            log.exception("Falha ao aplicar retenção")
        try:
            notify_run(records, trigger)
        except Exception:
            log.exception("Falha ao enviar notificação por e-mail")
        return records
