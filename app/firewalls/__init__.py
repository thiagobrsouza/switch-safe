"""Módulo Firewalls: os firewalls enviam o backup por FTP para o servidor embutido.

- models.py      Firewall e FirewallBackup
- storage.py     registro dos uploads, retenção, verificação de atrasos
- ftp_server.py  servidor FTP (pyftpdlib) com autenticação por firewall
- views.py       telas do módulo
"""
