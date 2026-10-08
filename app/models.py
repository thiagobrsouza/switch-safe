import re
from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

from .devices import default_command, device_label

db = SQLAlchemy()


def utcnow():
    """Datas são gravadas em UTC (naive) e convertidas para o fuso local na exibição."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime)


class Switch(db.Model):
    __tablename__ = "switches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False, default=22)
    device_type = db.Column(db.String(50), nullable=False)
    username = db.Column(db.String(100), nullable=False)
    password_enc = db.Column(db.Text, nullable=False)
    enable_password_enc = db.Column(db.Text)
    command = db.Column(db.String(255))
    description = db.Column(db.String(255))
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_backup_at = db.Column(db.DateTime)
    last_success_at = db.Column(db.DateTime)
    last_status = db.Column(db.String(20))
    last_error = db.Column(db.Text)

    backups = db.relationship(
        "Backup",
        back_populates="switch",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="dynamic",
    )

    @property
    def requires_enable(self):
        return bool(self.enable_password_enc)

    @property
    def effective_command(self):
        return self.command or default_command(self.device_type)

    @property
    def device_label(self):
        return device_label(self.device_type)

    @property
    def slug(self):
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", self.name).strip("_.") or f"switch{self.id}"


class Backup(db.Model):
    __tablename__ = "backups"

    id = db.Column(db.Integer, primary_key=True)
    switch_id = db.Column(db.Integer, db.ForeignKey("switches.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)  # success | failed
    trigger = db.Column(db.String(20), nullable=False, default="agendado")
    file_path = db.Column(db.String(512))
    size = db.Column(db.Integer, default=0)
    config_hash = db.Column(db.String(64))
    changed = db.Column(db.Boolean, default=False)
    error = db.Column(db.Text)
    duration = db.Column(db.Float)

    switch = db.relationship("Switch", back_populates="backups")


class Settings(db.Model):
    """Configuração global (linha única, id=1)."""

    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)

    # Agendamento e retenção
    schedule_enabled = db.Column(db.Boolean, nullable=False, default=True)
    schedule_cron = db.Column(db.String(100), nullable=False, default="0 2 * * *")
    retention_days = db.Column(db.Integer, nullable=False, default=90)
    retention_max_per_switch = db.Column(db.Integer, nullable=False, default=30)

    # SMTP
    smtp_enabled = db.Column(db.Boolean, nullable=False, default=False)
    smtp_host = db.Column(db.String(255))
    smtp_port = db.Column(db.Integer, nullable=False, default=587)
    smtp_security = db.Column(db.String(10), nullable=False, default="starttls")  # none | starttls | ssl
    smtp_verify_tls = db.Column(db.Boolean, nullable=False, default=True)
    smtp_username = db.Column(db.String(255))
    smtp_password_enc = db.Column(db.Text)
    smtp_from = db.Column(db.String(255))
    smtp_recipients = db.Column(db.Text)

    # Regras de alerta
    alert_on_failure = db.Column(db.Boolean, nullable=False, default=True)
    alert_on_success = db.Column(db.Boolean, nullable=False, default=False)
    alert_on_change = db.Column(db.Boolean, nullable=False, default=False)
    alert_stale_days = db.Column(db.Integer, nullable=False, default=2)

    last_run_at = db.Column(db.DateTime)
    last_run_summary = db.Column(db.String(255))

    @classmethod
    def get(cls):
        settings = db.session.get(cls, 1)
        if settings is None:
            settings = cls(id=1)
            db.session.add(settings)
            db.session.commit()
        return settings

    @property
    def recipient_list(self):
        return [r for r in re.split(r"[,;\s]+", self.smtp_recipients or "") if r]
