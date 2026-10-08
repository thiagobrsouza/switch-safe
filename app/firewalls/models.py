import ipaddress
import re
from datetime import timedelta

from ..models import db, utcnow

VENDORS = {
    "sonicwall": "SonicWall",
    "fortigate": "FortiGate",
    "outro": "Outro",
}


def make_dirname(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_.")[:100]


class Firewall(db.Model):
    __tablename__ = "firewalls"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    dirname = db.Column(db.String(100), unique=True, nullable=False)
    vendor = db.Column(db.String(30), nullable=False, default="outro")
    description = db.Column(db.String(255))
    ftp_username = db.Column(db.String(64), unique=True, nullable=False)
    ftp_password_hash = db.Column(db.String(255), nullable=False)
    allowed_ips = db.Column(db.Text)
    expected_hours = db.Column(db.Integer, nullable=False, default=24)
    enabled = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_backup_at = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(64))
    overdue_alerted = db.Column(db.Boolean, nullable=False, default=False)

    backups = db.relationship(
        "FirewallBackup",
        back_populates="firewall",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="dynamic",
    )

    @property
    def vendor_label(self):
        return VENDORS.get(self.vendor, self.vendor)

    @property
    def allowed_ip_list(self):
        return [ip for ip in re.split(r"[,;\s]+", self.allowed_ips or "") if ip]

    def ip_allowed(self, ip: str) -> bool:
        entries = self.allowed_ip_list
        if not entries:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for entry in entries:
            try:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        return False

    @property
    def deadline(self):
        if not self.enabled or self.expected_hours <= 0:
            return None
        return (self.last_backup_at or self.created_at) + timedelta(hours=self.expected_hours)

    @property
    def is_overdue(self):
        deadline = self.deadline
        return deadline is not None and utcnow() > deadline

    @property
    def status(self):
        """inativo | atrasado | aguardando | ok"""
        if not self.enabled:
            return "inativo"
        if self.is_overdue:
            return "atrasado"
        if self.last_backup_at is None:
            return "aguardando"
        return "ok"


class FirewallBackup(db.Model):
    __tablename__ = "firewall_backups"

    id = db.Column(db.Integer, primary_key=True)
    firewall_id = db.Column(db.Integer, db.ForeignKey("firewalls.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    size = db.Column(db.BigInteger, default=0)
    sha256 = db.Column(db.String(64))
    changed = db.Column(db.Boolean, default=False)
    remote_ip = db.Column(db.String(64))

    firewall = db.relationship("Firewall", back_populates="backups")
