"""Teste ponta a ponta do Switch Safe. Execute com: bash tests/run_e2e.sh"""
import http.cookiejar
import io
import os
import sys
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "http://127.0.0.1:8080"
DB = "/data/switchsafe.db"
EXTRA_FILE = os.environ.get("FAKE_EXTRA_FILE", "/scratch/extra.txt")
FAKE_HOST = os.environ.get("FAKE_HOST", "fake-switch")
RESULTS = []
MAILS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  OK   " if cond else "  FALHA") + f" {name}" + (f" -> {detail}" if detail and not cond else ""), flush=True)


# ---------- SMTP falso ----------
def smtp_sink():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 2525))
    srv.listen(5)
    while True:
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        f.write(b"220 sink\r\n"); f.flush()
        while True:
            line = f.readline()
            if not line:
                break
            cmd = line.decode().strip().upper()
            if cmd.startswith(("EHLO", "HELO")):
                f.write(b"250 sink\r\n")
            elif cmd.startswith("DATA"):
                f.write(b"354 go\r\n"); f.flush()
                data = []
                while True:
                    l = f.readline()
                    if l in (b".\r\n", b".\n", b""):
                        break
                    data.append(l.decode(errors="replace"))
                import email, email.policy
                parsed = email.message_from_string("".join(data), policy=email.policy.default)
                MAILS.append("Subject: " + str(parsed["Subject"]) + "\n\n" + parsed.get_content())
                f.write(b"250 queued\r\n")
            elif cmd.startswith("QUIT"):
                f.write(b"221 bye\r\n"); f.flush()
                break
            else:
                f.write(b"250 ok\r\n")
            f.flush()
        conn.close()


threading.Thread(target=smtp_sink, daemon=True).start()


# ---------- HTTP ----------
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect)


def req(op, path, data=None, method=None):
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, method=method or ("POST" if data is not None else "GET"))
    try:
        resp = op.open(r, timeout=60)
        return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def token(op, path="/"):
    _, _, html = req(op, path)
    m = re.search(rb'name="csrf_token" value="([^"]+)"', html)
    return m.group(1).decode() if m else None


def post(op, path, data, page="/"):
    data = dict(data)
    data["csrf_token"] = token(op, page)
    status, headers, body = req(op, path, data)
    if status in (301, 302, 303):
        loc = headers["Location"]
        _, _, body = req(op, urllib.parse.urlparse(loc).path + ("?" + urllib.parse.urlparse(loc).query if urllib.parse.urlparse(loc).query else ""))
    return status, body.decode(errors="replace")


def q(sql, *args):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def wait_backups(expected, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        n = q("select count(*) from backups")[0][0]
        if n >= expected:
            return n
        time.sleep(1)
    return q("select count(*) from backups")[0][0]


def wait_port(host, port, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection((host, port), timeout=2).close()
            return True
        except OSError:
            time.sleep(1)
    return False


if os.path.exists(EXTRA_FILE):
    os.remove(EXTRA_FILE)
if not (wait_port("127.0.0.1", 8080) and wait_port(FAKE_HOST, 2201) and wait_port(FAKE_HOST, 2202)):
    sys.exit("Switch Safe ou fake-switch não ficaram disponíveis.")

op = client()

print("\n[1] Primeiro acesso / setup")
s, h, _ = req(op, "/")
check("raiz sem login redireciona para /login", s == 302 and "/login" in h["Location"])
s, h, _ = req(op, "/login")
check("login sem usuários redireciona para /setup", s == 302 and "/setup" in h["Location"])
s, body = post(op, "/setup", {"username": "admin", "password": "123", "confirm": "123"}, "/setup")
check("senha fraca rejeitada no setup", "pelo menos 8" in body)
s, body = post(op, "/setup", {"username": "admin", "password": "SenhaForte#1", "confirm": "SenhaForte#1"}, "/setup")
check("admin criado e logado", "Bem-vindo" in body and "Painel" in body)
ph = q("select password_hash from users")[0][0]
check("senha do usuário gravada como hash Argon2id", ph.startswith("$argon2id$") and "SenhaForte" not in ph)
s, h, _ = req(op, "/setup")
check("setup indisponível após criar usuário", s == 302)

print("\n[2] CSRF")
s, _, _ = req(op, "/switches/run-all", {"x": "1"})
check("POST sem token CSRF é rejeitado (400)", s == 400, s)

print("\n[3] Alertas SMTP")
s, body = post(op, "/settings/alerts", {
    "smtp_enabled": "on", "smtp_host": "127.0.0.1", "smtp_port": "2525", "smtp_security": "none",
    "smtp_username": "", "smtp_password": "", "smtp_from": "switch-safe@teste.local",
    "smtp_recipients": "noc@teste.local, infra@teste.local", "alert_on_failure": "on",
    "alert_on_change": "on", "alert_stale_days": "2",
}, "/settings/alerts")
check("configuração SMTP salva", "Configurações de alerta salvas" in body, body[:300])
s, body = post(op, "/settings/alerts/test", {}, "/settings/alerts")
time.sleep(1)
check("e-mail de teste enviado", "E-mail de teste enviado" in body and any("E-mail de teste" in m for m in MAILS), body[:300])

print("\n[4] Cadastro de switches")
base = {"device_type": "cisco_ios", "host": FAKE_HOST, "username": "admin", "enabled": "on"}
cases = [
    ("SW-ENABLE", {"port": "2201", "password": "cisco123", "requires_enable": "on", "enable_password": "enable123"}),
    ("SW-PRIV", {"port": "2202", "password": "cisco123"}),
    ("SW-ENABLE-ERRADO", {"port": "2201", "password": "cisco123", "requires_enable": "on", "enable_password": "errada"}),
    ("SW-SEM-ENABLE", {"port": "2201", "password": "cisco123"}),
    ("SW-SENHA-ERRADA", {"port": "2202", "password": "errada"}),
    ("SW-INEXISTENTE", {"host": "nao-existe.invalid", "port": "22", "password": "x"}),
]
for name, extra in cases:
    s, body = post(op, "/switches/new", {**base, "name": name, **extra}, "/switches/new")
    check(f"cadastro {name}", f"Switch {name} cadastrado" in body, body[:300])

s, body = post(op, "/switches/new", {**base, "name": "SW-X", "port": "2201", "password": "a", "requires_enable": "on"}, "/switches/new")
check("enable marcado sem senha é rejeitado", "senha de enable" in body)
s, body = post(op, "/switches/new", {**base, "name": "SW-ENABLE", "port": "2201", "password": "a"}, "/switches/new")
check("nome duplicado é rejeitado", "Já existe um switch" in body)

raw = open(DB, "rb").read()
check("nenhuma senha de switch em texto puro no banco", b"cisco123" not in raw and b"enable123" not in raw)
rows = q("select password_enc, enable_password_enc from switches where name='SW-ENABLE'")[0]
check("senhas de switch criptografadas (Fernet)", rows[0].startswith("gAAAA") and rows[1].startswith("gAAAA"))
check("switch sem enable não tem senha de enable", q("select enable_password_enc from switches where name='SW-PRIV'")[0][0] is None)

print("\n[5] Execução do backup")
MAILS.clear()
s, body = post(op, "/switches/run-all", {})
check("backup de todos iniciado", "iniciado em segundo plano" in body)
n = wait_backups(6)
check("6 execuções registradas", n == 6, n)
status = dict(q("select s.name, b.status || '|' || coalesce(b.error,'') from backups b join switches s on s.id=b.switch_id"))
for name, s_ in status.items():
    print(f"         {name:18} {s_[:110]}")
check("SW-ENABLE: sucesso usando enable", status["SW-ENABLE"].startswith("success"))
check("SW-PRIV: sucesso sem enable", status["SW-PRIV"].startswith("success"))
check("SW-ENABLE-ERRADO: falha de enable", "enable" in status["SW-ENABLE-ERRADO"] and status["SW-ENABLE-ERRADO"].startswith("failed"))
check("SW-SEM-ENABLE: comando rejeitado detectado", "rejeitou o comando" in status["SW-SEM-ENABLE"])
check("SW-SENHA-ERRADA: falha de autenticação", "autenticação" in status["SW-SENHA-ERRADA"])
check("SW-INEXISTENTE: falha de DNS identificada", "Host não encontrado" in status["SW-INEXISTENTE"])
time.sleep(2)
check("alerta de falha enviado por e-mail", any("FALHA no backup de 4" in m for m in MAILS), [m[:200] for m in MAILS])
check("sem falso alerta de 'sem backup recente' para switches recém-cadastrados",
      not any("sem backup recente" in m for m in MAILS), [m[:200] for m in MAILS])
print("         --- e-mail recebido ---")
print("\n".join("         " + l for l in MAILS[0].split("\n\n", 1)[-1].strip().splitlines()) if MAILS else "")

bid = q("select b.id from backups b join switches s on s.id=b.switch_id where s.name='SW-ENABLE'")[0][0]
s, _, body = req(op, f"/backups/{bid}")
check("visualizar backup mostra a configuração", s == 200 and b"hostname SW-ENABLE" in body)
s, h, body = req(op, f"/backups/{bid}/download")
check("download do backup", s == 200 and "attachment" in h.get("Content-Disposition", "") and b"interface GigabitEthernet0/1" in body)
check("arquivo sem prompt/eco do comando", not body.startswith(b"show running") and b"SW-ENABLE#" not in body)

print("\n[6] Detecção de alteração")
sw_id = q("select id from switches where name='SW-ENABLE'")[0][0]
time.sleep(1.2)
post(op, f"/switches/{sw_id}/run", {})
wait_backups(7)
last = q("select changed from backups where switch_id=? order by created_at desc limit 1", sw_id)[0][0]
check("2º backup sem mudança real não é marcado como alterado (linha volátil ignorada)", last == 0)

MAILS.clear()
with open(EXTRA_FILE, "w") as fh:
    fh.write("interface GigabitEthernet0/3\n description NOVO-SERVIDOR")
time.sleep(1.2)
post(op, f"/switches/{sw_id}/run", {})
wait_backups(8)
bid_new, changed = q("select id, changed from backups where switch_id=? order by created_at desc limit 1", sw_id)[0]
check("backup após mudança real é marcado como alterado", changed == 1)
time.sleep(2)
check("alerta de configuração alterada enviado", any("Configuração alterada" in m for m in MAILS), [m[:150] for m in MAILS])
s, _, body = req(op, f"/backups/{bid_new}/diff")
check("comparação mostra a linha adicionada", s == 200 and b'class="d-add">+ description NOVO-SERVIDOR' in body)

print("\n[6b] Alteração pela CLI do switch simulado (conf t)")
import paramiko
from netmiko import ConnectHandler

# Sessão interativa como um humano faria, com erro de digitação corrigido por backspace
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(FAKE_HOST, port=2201, username="admin", password="cisco123", look_for_keys=False, allow_agent=False)
shell = cli.invoke_shell()
for keys in ["en\r", "enable123\r", "conf t\r", "int gi0/4\r", "descc\x7f SERVIDOR-ERP\r",
             "sw access vlan 20\r", "no shut\r", "end\r", "wr\r"]:
    shell.send(keys)
    time.sleep(0.3)
time.sleep(0.5)
transcript = shell.recv(65535).decode(errors="replace")
cli.close()
check("CLI: prompt de configuração exibido", "SW-ENABLE(config-if)#" in transcript, transcript[-400:])
check("CLI: write memory responde [OK]", "[OK]" in transcript)

with ConnectHandler(device_type="cisco_ios", host=FAKE_HOST, port=2201, username="admin",
                    password="cisco123", secret="enable123") as conn:
    conn.enable()
    conn.send_config_set(["vlan 30", "name VOIP", "exit", "no vlan 20"])

time.sleep(1.2)
total = q("select count(*) from backups")[0][0]
post(op, f"/switches/{sw_id}/run", {})
wait_backups(total + 1)
bid_cli, changed = q("select id, changed from backups where switch_id=? order by created_at desc limit 1", sw_id)[0]
check("backup após alteração pela CLI marcado como alterado", changed == 1)
s, _, body = req(op, f"/backups/{bid_cli}/diff")
for expected in (b'class="d-add">+ description SERVIDOR-ERP', b'class="d-add">+ switchport access vlan 20',
                 b'class="d-del">- shutdown', b'class="d-add">+vlan 30', b'class="d-del">-vlan 20'):
    check(f"comparação mostra {expected.decode().split('>', 1)[1]}", expected in body)

print("\n[7] Download ZIP")
s, h, body = req(op, "/backups/latest.zip")
names = zipfile.ZipFile(io.BytesIO(body)).namelist() if s == 200 else []
check("ZIP com o último backup de cada switch com sucesso", sorted(n.split("_")[0] for n in names) == ["SW-ENABLE", "SW-PRIV"], names)

print("\n[8] Retenção")
for _ in range(2):
    time.sleep(1.2)
    post(op, f"/switches/{sw_id}/run", {})
    wait_backups(q("select count(*) from backups")[0][0] + 1)
s, body = post(op, "/settings/retention", {"schedule_enabled": "on", "schedule_cron": "30 1 * * 1-5",
                                          "retention_days": "30", "retention_max_per_switch": "2"}, "/settings/retention")
check("configuração de retenção salva", "salvas" in body, body[:300])
check("próxima execução recalculada (01:30 em dia útil)", "01:30" in body)
s, body = post(op, "/settings/retention", {"schedule_cron": "isso nao e cron", "retention_days": "30",
                                          "retention_max_per_switch": "2"}, "/settings/retention")
check("cron inválido rejeitado", "Agendamento inválido" in body)
files_before = q("select count(*) from backups where switch_id=? and status='success'", sw_id)[0][0]
s, body = post(op, "/settings/retention/apply", {}, "/settings/retention")
files_after = q("select count(*) from backups where switch_id=? and status='success'", sw_id)[0][0]
check(f"retenção manteve só 2 backups ({files_before} -> {files_after})", files_after == 2, body[:300])
on_disk = len(os.listdir(f"/data/backups/switch_{sw_id}"))
check("arquivos antigos removidos do disco", on_disk == 2, on_disk)

print("\n[9] Exclusão de switch")
priv_id = q("select id from switches where name='SW-PRIV'")[0][0]
s, body = post(op, f"/switches/{priv_id}/delete", {}, "/switches/")
check("switch excluído com seus backups", "removidos" in body and not os.path.exists(f"/data/backups/switch_{priv_id}")
      and q("select count(*) from backups where switch_id=?", priv_id)[0][0] == 0)

print("\n[10] Páginas")
for path in ["/", "/switches/", "/switches/new", f"/switches/{sw_id}/edit", "/backups/", "/backups/?status=failed",
             "/settings/retention", "/settings/alerts", "/users/"]:
    s, _, body = req(op, path)
    check(f"GET {path} = 200", s == 200, s)
s, _, body = req(op, f"/switches/{sw_id}/edit")
check("senhas nunca retornam ao formulário", b"cisco123" not in body and b"enable123" not in body)

print("\n[12] Módulo Firewalls (FTP)")
import ftplib
import hashlib
from datetime import datetime, timedelta, timezone

FTP_HOST, FTP_PORT = "127.0.0.1", 2121
FW_PASS = "FtpSenha#123"
fw_form = {"name": "FW-MATRIZ", "vendor": "sonicwall", "description": "Matriz", "ftp_username": "fw-matriz",
           "ftp_password": FW_PASS, "allowed_ips": "", "expected_hours": "24", "enabled": "on"}
s, body = post(op, "/firewalls/new", fw_form, "/firewalls/new")
check("firewall cadastrado", "FW-MATRIZ cadastrado" in body, body[:300])
fw_id, fw_hash = q("select id, ftp_password_hash from firewalls where name='FW-MATRIZ'")[0]
check("senha FTP gravada como hash Argon2id", fw_hash.startswith("$argon2id$") and FW_PASS not in fw_hash)
s, body = post(op, "/firewalls/new", {**fw_form, "name": "FW-OUTRO"}, "/firewalls/new")
check("usuário FTP duplicado rejeitado", "já está em uso" in body)
s, body = post(op, "/firewalls/new", {**fw_form, "name": "FW-OUTRO", "ftp_username": "fw-outro", "allowed_ips": "abc"}, "/firewalls/new")
check("IP de origem inválido rejeitado", "inválido" in body)
s, body = post(op, "/firewalls/new", {**fw_form, "name": "FW-FILIAL", "vendor": "fortigate", "ftp_username": "fw-filial",
                                      "allowed_ips": "10.99.99.0/24"}, "/firewalls/new")
check("firewall com restrição de IP cadastrado", "FW-FILIAL cadastrado" in body)
check("diretório criado com o nome do firewall", os.path.isdir("/data/firewalls/FW-MATRIZ"))


def ftp_login(user, password):
    ftp = ftplib.FTP()
    ftp.connect(FTP_HOST, FTP_PORT, timeout=15)
    ftp.login(user, password)
    return ftp


def ftp_refused(fn):
    try:
        fn()
        return False
    except ftplib.error_perm:
        return True


def fw_rows():
    return q("select id, filename, size, sha256, original_name, remote_ip, changed from firewall_backups "
             "where firewall_id=? order by created_at", fw_id)


def upload(data, name="backup.exp"):
    ftp = ftp_login("fw-matriz", FW_PASS)
    ftp.storbinary(f"STOR {name}", io.BytesIO(data))
    ftp.quit()
    time.sleep(0.8)


check("FTP: senha errada recusada", ftp_refused(lambda: ftp_login("fw-matriz", "errada")))
check("FTP: IP fora da lista permitida recusado", ftp_refused(lambda: ftp_login("fw-filial", FW_PASS)))

data1 = os.urandom(3 * 1024 * 1024)
ftp = ftp_login("fw-matriz", FW_PASS)
ftp.storbinary("STOR backup.exp", io.BytesIO(data1))
check("FTP: não permite baixar arquivos (RETR)", ftp_refused(lambda: ftp.retrbinary("RETR backup.exp", lambda b: None)))
check("FTP: não permite apagar (DELE)", ftp_refused(lambda: ftp.delete("qualquer.exp")))
check("FTP: não permite criar diretório (MKD)", ftp_refused(lambda: ftp.mkd("sub")))
try:
    ftp.cwd("..")
except ftplib.error_perm:
    pass
check("FTP: preso no próprio diretório (cd .. continua em /)", ftp.pwd() == "/")
ftp.quit()
time.sleep(0.8)

rows = fw_rows()
check("upload de 3 MB registrado", len(rows) == 1 and rows[0][2] == len(data1), rows)
_, fname, _, sha, orig, rip, _ = rows[0]
check("arquivo renomeado com data/hora (não sobrescreve)", fname.startswith("backup_") and fname.endswith(".exp") and orig == "backup.exp")
check("hash SHA-256 correto", sha == hashlib.sha256(data1).hexdigest())
check("IP de origem registrado", rip == "127.0.0.1")
check("arquivo salvo no diretório do firewall", os.path.exists(f"/data/firewalls/FW-MATRIZ/{fname}"))
s, h, body = req(op, f"/firewalls/backups/{rows[0][0]}/download")
check("download pela web idêntico ao enviado", s == 200 and body == data1)

upload(data1)
upload(os.urandom(1024 * 1024))
changed = [r[6] for r in fw_rows()]
check("mesmo conteúdo não é marcado como alterado; conteúdo novo é", changed == [0, 0, 1], changed)

for path in ["/firewalls/", f"/firewalls/{fw_id}", f"/firewalls/{fw_id}/edit", "/firewalls/backups",
             f"/firewalls/backups?firewall_id={fw_id}", "/firewalls/settings", "/firewalls/new", "/"]:
    s, _, _ = req(op, path)
    check(f"GET {path} = 200", s == 200, s)
_, _, body = req(op, "/firewalls/")
check("status 'Em dia' na listagem", b"Em dia" in body)

print("  -- atraso de backup")
MAILS.clear()
two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat(" ")
con = sqlite3.connect(DB)
con.execute("update firewalls set expected_hours=1, last_backup_at=? where id=?", (two_hours_ago, fw_id))
con.commit()
con.close()
time.sleep(6)
check("alerta de backup não recebido enviado", any("firewall(s) não recebido" in m and "FW-MATRIZ" in m for m in MAILS),
      [m[:120] for m in MAILS])
check("alerta enviado só uma vez", sum("FW-MATRIZ" in m for m in MAILS) == 1 and
      q("select overdue_alerted from firewalls where id=?", fw_id)[0][0] == 1)
_, _, body = req(op, "/firewalls/")
check("status 'Atrasado' na listagem", b"Atrasado" in body)
upload(b"novo backup")
check("novo envio normaliza o status", q("select overdue_alerted from firewalls where id=?", fw_id)[0][0] == 0)

print("  -- retenção")
s, body = post(op, "/firewalls/settings", {"fw_retention_days": "365", "fw_retention_max": "2"}, "/firewalls/settings")
check("retenção do módulo salva", "salvas" in body)
upload(b"mais um")
rows = fw_rows()
on_disk = os.listdir("/data/firewalls/FW-MATRIZ")
check(f"retenção manteve 2 arquivos (banco={len(rows)}, disco={len(on_disk)})", len(rows) == 2 and len(on_disk) == 2)

s, h, body = req(op, "/firewalls/backups/latest.zip")
names = zipfile.ZipFile(io.BytesIO(body)).namelist() if s == 200 else []
check("ZIP com o último arquivo de cada firewall", len(names) == 1 and names[0].startswith("FW-MATRIZ/"), names)

print("  -- renomear e excluir")
s, body = post(op, f"/firewalls/{fw_id}/edit", {**fw_form, "name": "FW-SP", "ftp_password": ""}, f"/firewalls/{fw_id}/edit")
check("renomear firewall renomeia o diretório", os.path.isdir("/data/firewalls/FW-SP") and not os.path.exists("/data/firewalls/FW-MATRIZ"))
check("senha FTP mantida ao editar sem informar", q("select ftp_password_hash from firewalls where id=?", fw_id)[0][0] == fw_hash)
s, _, body = req(op, f"/firewalls/backups/{fw_rows()[-1][0]}/download")
check("download continua funcionando após renomear", s == 200 and body == b"mais um")
upload(b"depois de renomear")
check("upload após renomear cai no novo diretório", any(f.startswith("backup_") for f in os.listdir("/data/firewalls/FW-SP")) and len(fw_rows()) == 2)
s, body = post(op, f"/firewalls/{fw_id}/delete", {}, "/firewalls/")
check("firewall excluído com seus arquivos", "removidos" in body and not os.path.exists("/data/firewalls/FW-SP")
      and q("select count(*) from firewall_backups where firewall_id=?", fw_id)[0][0] == 0)
check("usuário FTP de firewall excluído não entra mais", ftp_refused(lambda: ftp_login("fw-matriz", FW_PASS)))

print("\n[11] Proteção de login")
post(op, "/logout", {})
s, h, _ = req(op, "/switches/")
check("após logout, páginas exigem login", s == 302 and "/login" in h["Location"])
s, h, _ = req(op, "/login?next=//evil.com", {"csrf_token": token(op, "/login"), "username": "admin", "password": "SenhaForte#1"})
check("open redirect bloqueado (?next=//evil.com)", s == 302 and "evil" not in h["Location"], h.get("Location"))
op2 = client()
codes = []
for _ in range(6):
    s, body = post(op2, "/login", {"username": "admin", "password": "errada"}, "/login")
    codes.append(s)
check("6ª tentativa errada bloqueada (429)", codes[-1] == 429, codes)

print("\n" + "=" * 60)
ok = sum(1 for _, r in RESULTS if r)
print(f"RESULTADO: {ok}/{len(RESULTS)} verificações OK")
for name, r in RESULTS:
    if not r:
        print("  FALHOU:", name)
sys.exit(0 if ok == len(RESULTS) else 1)
