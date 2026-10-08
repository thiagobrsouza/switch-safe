import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
MIN_PASSWORD_LENGTH = 8


@lru_cache(maxsize=1)
def get_tz():
    try:
        return ZoneInfo(os.environ.get("TZ", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def to_local(dt: datetime | None):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_tz())


def localdt(dt, fmt="%d/%m/%Y %H:%M:%S"):
    local = to_local(dt)
    return local.strftime(fmt) if local else "—"


def filesize(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def validate_username(username: str) -> str | None:
    if not USERNAME_RE.match(username or ""):
        return "Usuário deve ter de 3 a 64 caracteres (letras, números, _ . @ -)."
    return None


def validate_password(password: str, confirm: str) -> str | None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    if password != confirm:
        return "As senhas não conferem."
    return None


def safe_next(target: str | None) -> str | None:
    """Aceita apenas caminhos relativos locais para evitar open redirect."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None
