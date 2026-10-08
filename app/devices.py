"""Tipos de equipamento suportados (device_type do Netmiko) e o comando de backup padrão."""

DEVICE_TYPES = {
    "cisco_ios": {"label": "Cisco IOS / IOS-XE", "command": "show running-config", "port": 22},
    "cisco_ios_telnet": {"label": "Cisco IOS (Telnet)", "command": "show running-config", "port": 23},
    "cisco_nxos": {"label": "Cisco NX-OS", "command": "show running-config", "port": 22},
    "cisco_s300": {"label": "Cisco SG/CBS (Small Business)", "command": "show running-config", "port": 22},
    "hp_procurve": {"label": "HP ProCurve / Aruba OS-Switch", "command": "show running-config", "port": 22},
    "hp_procurve_telnet": {"label": "HP ProCurve (Telnet)", "command": "show running-config", "port": 23},
    "aruba_aoscx": {"label": "Aruba AOS-CX", "command": "show running-config", "port": 22},
    "hp_comware": {"label": "HPE Comware / H3C", "command": "display current-configuration", "port": 22},
    "huawei": {"label": "Huawei VRP", "command": "display current-configuration", "port": 22},
    "huawei_telnet": {"label": "Huawei VRP (Telnet)", "command": "display current-configuration", "port": 23},
    "dell_os10": {"label": "Dell OS10", "command": "show running-configuration", "port": 22},
    "dell_force10": {"label": "Dell Force10 / OS9", "command": "show running-config", "port": 22},
    "juniper_junos": {"label": "Juniper Junos", "command": "show configuration | display set", "port": 22},
    "extreme_exos": {"label": "Extreme EXOS", "command": "show configuration", "port": 22},
    "ruckus_fastiron": {"label": "Ruckus / Brocade FastIron", "command": "show running-config", "port": 22},
    "tplink_jetstream": {"label": "TP-Link JetStream", "command": "show running-config", "port": 22},
    "mikrotik_routeros": {"label": "MikroTik RouterOS", "command": "/export", "port": 22},
    "fortinet": {"label": "Fortinet FortiSwitch / FortiGate", "command": "show", "port": 22},
}

DEFAULT_DEVICE_TYPE = "cisco_ios"


def device_label(device_type: str) -> str:
    return DEVICE_TYPES.get(device_type, {}).get("label", device_type)


def default_command(device_type: str) -> str:
    return DEVICE_TYPES.get(device_type, {}).get("command", "show running-config")


def default_port(device_type: str) -> int:
    return DEVICE_TYPES.get(device_type, {}).get("port", 22)
