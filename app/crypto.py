"""Proteção de segredos.

- Senhas de login dos usuários: hash Argon2id (irreversível).
- Senhas dos switches / enable / SMTP: precisam ser reutilizadas para autenticar
  no equipamento, portanto um hash não serviria. São cifradas com Fernet
  (AES-128-CBC + HMAC-SHA256) usando uma chave mantida fora do banco de dados.
"""

import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

_hasher = PasswordHasher()
_fernet = None

# Hash fixo usado para equalizar o tempo de resposta quando o usuário não existe.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def _load_or_create(path: Path, generate) -> bytes:
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(value)
    return value


def load_secret_key(keys_dir: Path) -> bytes:
    return _load_or_create(keys_dir / "session.key", lambda: secrets.token_hex(32).encode())


def init_encryption(keys_dir: Path) -> None:
    global _fernet
    env_key = os.environ.get("SWITCHSAFE_ENCRYPTION_KEY", "").strip()
    key = env_key.encode() if env_key else _load_or_create(keys_dir / "encryption.key", Fernet.generate_key)
    _fernet = Fernet(key)


def encrypt(plain: str | None) -> str | None:
    if not plain:
        return None
    return _fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str:
    """Levanta cryptography.fernet.InvalidToken se a chave não corresponder."""
    if not token:
        return ""
    return _fernet.decrypt(token.encode("ascii")).decode("utf-8")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
