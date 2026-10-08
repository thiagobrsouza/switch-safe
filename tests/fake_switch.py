"""Switch Cisco IOS simulado via SSH, para demonstração e testes do Switch Safe.

Porta 2201 -> "SW-ENABLE": entra em modo usuário (>) e exige enable.
Porta 2202 -> "SW-PRIV":   entra direto em modo privilegiado (#), sem enable.

Credenciais (podem ser trocadas por variável de ambiente):
    FAKE_USER=admin  FAKE_PASSWORD=cisco123  FAKE_ENABLE=enable123

Aceita um subconjunto do modo de configuração do IOS (configure terminal,
interface, vlan, line, description, shutdown, no ..., hostname, write memory).
As alterações ficam salvas em FAKE_STATE_DIR (padrão /state) e sobrevivem a
reinícios. Digite "?" no switch para ver os comandos disponíveis.

Também é possível injetar linhas extras de configuração pelo arquivo
FAKE_EXTRA_FILE (padrão /tests/extra.txt).
"""
import copy
import datetime
import json
import os
import re
import socket
import threading

import paramiko

USER = os.environ.get("FAKE_USER", "admin")
PASSWORD = os.environ.get("FAKE_PASSWORD", "cisco123")
ENABLE = os.environ.get("FAKE_ENABLE", "enable123")
EXTRA_FILE = os.environ.get("FAKE_EXTRA_FILE", "/tests/extra.txt")
STATE_DIR = os.environ.get("FAKE_STATE_DIR", "/state")

# Blocos que abrem um submodo de configuração: prefixo -> nome do modo no prompt
SUBMODES = {
    "interface": "config-if",
    "vlan": "config-vlan",
    "line": "config-line",
    "router": "config-router",
    "ip access-list": "config-acl",
    "spanning-tree mst configuration": "config-mst",
}
# Comandos que só podem existir uma vez dentro de um bloco (o novo substitui o anterior)
CHILD_SINGLE = [
    "description", "name", "ip address", "switchport mode", "switchport access vlan",
    "switchport trunk allowed vlan", "switchport trunk native vlan", "switchport voice vlan",
    "speed", "duplex", "transport input", "exec-timeout", "password", "spanning-tree portfast",
]
# Comandos globais de valor único
GLOBAL_SINGLE = [
    "hostname", "ip default-gateway", "ip domain-name", "ip domain name", "banner motd",
    "clock timezone", "snmp-server location", "snmp-server contact", "logging host",
]
ABBREVIATIONS = {
    "int": "interface", "inter": "interface", "desc": "description", "descr": "description",
    "shut": "shutdown", "sh": "show", "conf": "configure", "config": "configure",
    "sw": "switchport", "wr": "write", "ex": "exit",
}
IFACE_NAMES = [
    (r"(gi|gig|gigabit|gigabitethernet)", "GigabitEthernet"),
    (r"(fa|fast|fastethernet)", "FastEthernet"),
    (r"(te|ten|tengigabitethernet)", "TenGigabitEthernet"),
    (r"(po|port-channel)", "Port-channel"),
    (r"(vl|vlan)", "Vlan"),
    (r"(lo|loopback)", "Loopback"),
]

HELP = """Comandos disponíveis neste switch simulado:
  enable                         entrar no modo privilegiado (porta 2201)
  show running-config | sh run   exibir a configuração
  configure terminal  | conf t   entrar no modo de configuração
  write memory        | wr       "salvar" a configuração
  disable / exit

No modo de configuração (exemplos):
  hostname SW-NOVO
  interface GigabitEthernet0/3    (ou: int gi0/3)
   description SERVIDOR-ERP
   switchport access vlan 20
   no shutdown
  exit
  vlan 30
   name VOIP
  no vlan 20
  ntp server 10.0.0.1
  end                            (ou Ctrl+Z)
"""


def log(msg):
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S BRT %a %b %d %Y")


def default_blocks(hostname, last_octet):
    return [
        ["version 15.2", []],
        ["service timestamps log datetime msec", []],
        [f"hostname {hostname}", []],
        ["enable secret 5 $1$abcd$EXAMPLEHASH", []],
        ["username admin privilege 15 secret 5 $1$efgh$EXAMPLEHASH", []],
        ["vlan 10", ["name USUARIOS"]],
        ["vlan 20", ["name SERVIDORES"]],
        ["interface GigabitEthernet0/1", ["description UPLINK-CORE", "switchport mode trunk"]],
        ["interface GigabitEthernet0/2", ["description ESTACAO-01", "switchport access vlan 10", "switchport mode access"]],
        ["interface GigabitEthernet0/3", ["shutdown"]],
        ["interface GigabitEthernet0/4", ["shutdown"]],
        ["interface Vlan1", [f"ip address 192.168.1.{last_octet} 255.255.255.0"]],
        ["ip default-gateway 192.168.1.1", []],
        ["snmp-server community public RO", []],
        ["ntp server 192.168.1.1", []],
        ["line vty 0 4", ["login local", "transport input ssh"]],
    ]


def submode_of(header):
    for prefix, mode in SUBMODES.items():
        if header == prefix or header.startswith(prefix + " "):
            return mode
    return None


def normalize(line):
    """Expande abreviações comuns e normaliza nomes de interface."""
    words = line.split()
    if not words:
        return ""
    idx = 1 if words[0] == "no" and len(words) > 1 else 0
    words[idx] = ABBREVIATIONS.get(words[idx].lower(), words[idx])
    if words[idx] == "interface" and len(words) > idx + 1:
        name = " ".join(words[idx + 1:])
        for pattern, full in IFACE_NAMES:
            m = re.match(pattern + r"\s*([\d/.:]+)$", name, re.IGNORECASE)
            if m:
                name = full + m.group(2)
                break
        words = words[: idx + 1] + [name]
    return " ".join(words)


def single_key(line, keys):
    for key in keys:
        if line == key or line.startswith(key + " "):
            return key
    return None


class Device:
    def __init__(self, key, hostname, last_octet, start_priv):
        self.key = key
        self.start_priv = start_priv
        self.lock = threading.Lock()
        self.path = os.path.join(STATE_DIR, f"{key}.json")
        self.blocks = default_blocks(hostname, last_octet)
        self.changed_at = now_str()
        self.saved = True
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.blocks, self.changed_at = data["blocks"], data["changed_at"]
        except (OSError, ValueError, KeyError):
            pass

    @property
    def hostname(self):
        for header, _ in self.blocks:
            if header.startswith("hostname "):
                return header.split(" ", 1)[1]
        return "Switch"

    def _persist(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"blocks": self.blocks, "changed_at": self.changed_at}, fh, indent=1)
        except OSError as exc:
            log(f"[{self.hostname}] não foi possível salvar o estado: {exc}")

    def _find(self, header):
        for block in self.blocks:
            if block[0].lower() == header.lower():
                return block
        return None

    def _insert_block(self, header):
        """Insere um bloco novo perto dos semelhantes (interfaces com interfaces etc.)."""
        kind = header.split()[0]
        position = None
        for i, (h, _) in enumerate(self.blocks):
            if h.split()[0] == kind:
                position = i + 1
        if position is None:
            position = next((i for i, (h, _) in enumerate(self.blocks) if h.startswith("line ")), len(self.blocks))
        block = [header, []]
        self.blocks.insert(position, block)
        return block

    def apply(self, ctx, line):
        """Aplica um comando de configuração. Retorna (novo_contexto, sair_do_config)."""
        line = normalize(line)
        with self.lock:
            before = copy.deepcopy(self.blocks)
            result = self._apply(ctx, line)
            if self.blocks != before:
                self.changed_at = now_str()
                self.saved = False
                self._persist()
                log(f"[{self.hostname}] configuração alterada: {line}")
            return result

    def _apply(self, ctx, line):
        if line in ("end", "\x1a"):
            return None, True
        if line == "exit":
            return (None, False) if ctx else (None, True)
        if line.startswith("do "):
            return ctx, False

        negate = line.startswith("no ")
        body = line[3:].strip() if negate else line

        # Abrir/remover bloco (válido também de dentro de outro submodo, como no IOS)
        if submode_of(body):
            if negate:
                self.blocks = [b for b in self.blocks if b[0].lower() != body.lower()]
                return None, False
            block = self._find(body) or self._insert_block(body)
            return block[0], False

        if body.startswith("hostname "):
            ctx = None

        if ctx:
            block = self._find(ctx)
            if block is None:
                return None, False
            children = block[1]
            if negate:
                if body == "shutdown":
                    block[1] = [c for c in children if c != "shutdown"]
                else:
                    block[1] = [c for c in children if not (c == body or c.startswith(body + " "))]
            else:
                key = single_key(body, CHILD_SINGLE)
                if key:
                    block[1] = [c for c in children if single_key(c, CHILD_SINGLE) != key]
                if body not in block[1]:
                    block[1].append(body)
            return ctx, False

        # Comandos globais
        if negate:
            self.blocks = [b for b in self.blocks if b[1] or not (b[0] == body or b[0].startswith(body + " "))]
            return None, False
        key = single_key(body, GLOBAL_SINGLE)
        if key:
            for block in self.blocks:
                if not block[1] and single_key(block[0], GLOBAL_SINGLE) == key:
                    block[0] = body
                    return None, False
        if not self._find(body):
            position = next((i for i, (h, _) in enumerate(self.blocks) if h.startswith("line ")), len(self.blocks))
            self.blocks.insert(position, [body, []])
        return None, False

    def running_config(self):
        with self.lock:
            out = []
            for header, children in self.blocks:
                if children or submode_of(header):
                    if out and out[-1] != "!":
                        out.append("!")
                    out.append(header)
                    out.extend(" " + c for c in children)
                    out.append("!")
                else:
                    out.append(header)
            changed_at = self.changed_at
        extra = ""
        if os.path.exists(EXTRA_FILE):
            with open(EXTRA_FILE, encoding="utf-8") as fh:
                extra = fh.read().strip()
        if extra:
            out.extend(extra.splitlines() + ["!"])
        body = "\n".join(out + ["end"])
        header = f"Building configuration...\n\nCurrent configuration : {len(body)} bytes\n!\n" \
                 f"! Last configuration change at {changed_at}\n!\n"
        return (header + body).replace("\n", "\r\n")


DEVICES = {}


def load_host_key():
    path = os.path.join(STATE_DIR, "host_key")
    try:
        return paramiko.RSAKey.from_private_key_file(path)
    except (OSError, paramiko.SSHException):
        key = paramiko.RSAKey.generate(2048)
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            key.write_private_key_file(path)
        except OSError:
            pass
        return key


class Server(paramiko.ServerInterface):
    def __init__(self, device, peer):
        self.event = threading.Event()
        self.device = device
        self.peer = peer

    def check_auth_password(self, username, password):
        ok = (username, password) == (USER, PASSWORD)
        log(f"[{self.device.hostname}] login {'OK' if ok else 'RECUSADO'} usuário={username!r} de {self.peer}")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True


class Session:
    def __init__(self, chan, device):
        self.chan = chan
        self.dev = device
        self.priv = device.start_priv
        self.awaiting_enable = False
        self.config = False
        self.ctx = None

    def prompt(self):
        name = self.dev.hostname
        if self.config:
            mode = submode_of(self.ctx) if self.ctx else "config"
            return f"{name}({mode})#"
        return f"{name}#" if self.priv else f"{name}>"

    def send(self, text):
        self.chan.send(text)

    def handle_line(self, line):
        """Retorna False para encerrar a sessão."""
        if self.awaiting_enable:
            self.awaiting_enable = False
            if line == ENABLE:
                self.priv = True
                log(f"[{self.dev.hostname}] enable OK")
                self.send("\r\n" + self.prompt())
            else:
                log(f"[{self.dev.hostname}] enable RECUSADO")
                self.send("\r\n% Access denied\r\n\r\n" + self.prompt())
            return True

        if line in ("?", "help"):
            self.send("\r\n" + HELP.replace("\n", "\r\n") + "\r\n" + self.prompt())
            return True

        if self.config and line.startswith("do "):
            cmd = normalize(line[3:])
            if cmd in ("show running-config", "show run", "show running"):
                self.send("\r\n" + self.dev.running_config())
            elif cmd in ("write", "write memory"):
                self.send("\r\nBuilding configuration...\r\n[OK]")
            else:
                self.send("\r\n% Invalid input detected at '^' marker.\r\n")
            self.send("\r\n" + self.prompt())
            return True

        if self.config:
            self.ctx, leave = self.dev.apply(self.ctx, line) if line else (self.ctx, False)
            if leave:
                self.config, self.ctx = False, None
            self.send("\r\n" + self.prompt())
            return True

        cmd = normalize(line)
        if cmd == "enable" or cmd == "en":
            if self.priv:
                self.send("\r\n" + self.prompt())
            else:
                self.awaiting_enable = True
                self.send("\r\nPassword: ")
        elif cmd in ("exit", "logout", "quit"):
            return False
        elif cmd == "disable":
            self.priv = self.dev.start_priv and self.priv
            self.send("\r\n" + self.prompt())
        elif cmd == "" or cmd.startswith("terminal "):
            self.send("\r\n" + self.prompt())
        elif not self.priv:
            self.send("\r\n                ^\r\n% Invalid input detected at '^' marker.\r\n\r\n" + self.prompt())
        elif cmd in ("show running-config", "show run", "show running"):
            log(f"[{self.dev.hostname}] show running-config enviado")
            self.send("\r\n" + self.dev.running_config() + "\r\n" + self.prompt())
        elif cmd in ("configure terminal", "configure t", "configure term", "configure"):
            self.config = True
            self.send("\r\nEnter configuration commands, one per line.  End with CNTL/Z.\r\n" + self.prompt())
        elif cmd in ("write", "write memory", "copy running-config startup-config", "copy run start"):
            self.dev.saved = True
            self.send("\r\nBuilding configuration...\r\n[OK]\r\n" + self.prompt())
        else:
            self.send("\r\n                ^\r\n% Invalid input detected at '^' marker.\r\n\r\n" + self.prompt())
        return True

    def run(self):
        self.send("\r\n" + self.prompt())
        buf, last_cr, escape = "", False, False
        while True:
            data = self.chan.recv(1024)
            if not data:
                return
            for ch in data.decode(errors="ignore"):
                if escape:  # ignora sequências de escape (setas etc.)
                    if ch.isalpha() or ch == "~":
                        escape = False
                    continue
                if ch == "\x1b":
                    escape = True
                    continue
                if ch == "\n" and last_cr:
                    last_cr = False
                    continue
                last_cr = ch == "\r"
                if ch in ("\x7f", "\x08"):
                    if buf:
                        buf = buf[:-1]
                        if not self.awaiting_enable:
                            self.send("\b \b")
                    continue
                if ch == "\x03":  # Ctrl+C
                    buf = ""
                    self.send("^C\r\n" + self.prompt())
                    continue
                if ch == "\x1a":  # Ctrl+Z
                    buf = ""
                    if self.config:
                        self.config, self.ctx = False, None
                    self.send("^Z\r\n" + self.prompt())
                    continue
                if ch == "\t":
                    continue
                if ch not in "\r\n":
                    buf += ch
                    if not self.awaiting_enable:
                        self.send(ch)
                    continue
                line, buf = buf.strip(), ""
                if not self.handle_line(line):
                    self.send("\r\n")
                    return


def handle(client, peer, device, host_key):
    transport = paramiko.Transport(client)
    transport.add_server_key(host_key)
    server = Server(device, peer)
    try:
        transport.start_server(server=server)
        chan = transport.accept(30)
        if chan is None:
            return
        server.event.wait(10)
        Session(chan, device).run()
        chan.close()
    except Exception as exc:  # noqa: BLE001
        log(f"[{device.hostname}] erro: {exc}")
    finally:
        transport.close()


def serve(port, device, host_key):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen(20)
    mode = "modo privilegiado direto" if device.start_priv else "exige enable"
    log(f"{device.hostname} ouvindo na porta {port} ({mode})")
    while True:
        client, addr = sock.accept()
        threading.Thread(target=handle, args=(client, addr[0], device, host_key), daemon=True).start()


if __name__ == "__main__":
    log(f"Credenciais: usuário={USER} senha={PASSWORD} enable={ENABLE}")
    key = load_host_key()
    sw_enable = Device("sw-enable", "SW-ENABLE", 11, start_priv=False)
    sw_priv = Device("sw-priv", "SW-PRIV", 12, start_priv=True)
    threading.Thread(target=serve, args=(2201, sw_enable, key), daemon=True).start()
    serve(2202, sw_priv, key)
