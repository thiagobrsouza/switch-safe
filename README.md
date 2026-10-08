# Switch Safe

Aplicação web (Docker) para backup automático da configuração de switches
(`show running-config` e equivalentes), com retenção automática e alertas por e-mail.

## Funcionalidades

- **Login com usuário e senha** (hash Argon2id, bloqueio após 5 tentativas em 10 min)
- **Painel**: status de cada switch, falhas, espaço usado, próxima execução
- **Switches monitorados**: listagem, backup manual ("Backup agora"), edição, exclusão
- **Cadastro de switches**: tipo de equipamento, IP, porta, usuário, senha e **senha de enable opcional**
- **Backups**: consulta, visualização, download individual, download ZIP dos últimos de cada switch e
  **comparação (diff) com o backup anterior**
- **Agendamento e retenção**: expressão cron, retenção por dias e/ou quantidade máxima por switch
- **Alertas SMTP**: falha de backup, configuração alterada, switch sem backup há N dias, resumo de sucesso
- **Usuários**: criação, troca de senha e remoção
- **Módulo Firewalls**: servidor FTP embutido para SonicWall/FortiGate enviarem seus backups, com usuário FTP
  por firewall, diretório próprio, retenção, download e alerta de backup não recebido
  ([detalhes](#módulo-firewalls-ftp))

Equipamentos suportados (via [Netmiko](https://github.com/ktbyers/netmiko)): Cisco IOS/IOS-XE/NX-OS/SMB,
HP ProCurve/Aruba OS-Switch, Aruba AOS-CX, HPE Comware/H3C, Huawei, Dell OS10/OS9, Juniper,
Extreme EXOS, Ruckus/Brocade, TP-Link JetStream, MikroTik e Fortinet (SSH; alguns também via Telnet).
Para incluir outro tipo, adicione uma entrada em [app/devices.py](app/devices.py).

## Subindo

```bash
cp .env.example .env      # ajuste APP_PORT e TZ
docker compose up -d --build
```

Acesse `http://SERVIDOR:8080` (ou a porta definida em `APP_PORT`). No **primeiro acesso** a aplicação
pede a criação do usuário administrador — faça isso logo após subir o container.

Logs: `docker compose logs -f switch-safe`

## Instalação em servidor (produção)

A imagem `switch-safe:latest` **não vem de um registro**: o `docker compose` a constrói a partir do código
do repositório (`build: .`) e só dá esse nome a ela. Por isso o servidor precisa do código.

```bash
# 1. Docker + plugin compose (Debian/Ubuntu; em outras distros veja docs.docker.com)
curl -fsSL https://get.docker.com | sudo sh

# 2. Código
sudo git clone <URL-DO-REPOSITORIO> /opt/switch-safe
cd /opt/switch-safe

# 3. Configuração
sudo cp .env.example .env
sudo nano .env          # APP_PORT, TZ, FTP_PUBLIC_HOST=<IP do servidor>, FIREWALL_BACKUP_PATH=/mnt/fw-backup/switch-safe

# 4. Pasta dos backups de firewall (o container roda como UID 1000)
sudo mkdir -p /mnt/fw-backup/switch-safe
sudo chown -R 1000:1000 /mnt/fw-backup/switch-safe

# 5. Subir
sudo docker compose up -d --build
sudo docker compose ps          # deve aparecer (healthy)
sudo docker compose logs -f     # Ctrl+C para sair
```

6. Libere no firewall do servidor a porta da aplicação (`APP_PORT`, padrão 8080) para quem administra, e a
   porta FTP (21) + faixa passiva (30000-30009) **somente a partir dos firewalls**.
7. Acesse `http://<IP>:8080` e crie o administrador **imediatamente** (o primeiro acesso cria o admin).
8. **Guarde uma cópia** de `/data/keys/encryption.key` (ver [Segurança das senhas](#segurança-das-senhas)):
   `sudo docker cp switch-safe:/data/keys/encryption.key ./encryption.key.bak`

**Atualizar** para uma nova versão:

```bash
cd /opt/switch-safe && sudo git pull && sudo docker compose up -d --build
```

O banco e os arquivos ficam nos volumes/pastas e são preservados; colunas novas do banco são migradas
automaticamente na inicialização.

Observações:
- Não use `--profile demo` em produção (é o switch simulado).
- Se `/mnt/fw-backup` for um compartilhamento de rede (NFS/SMB), monte-o com dono UID 1000
  (SMB: opções `uid=1000,gid=1000`) e garanta que esteja montado antes do Docker iniciar.
- Em RHEL/Rocky/Alma com SELinux, acrescente `:z` no bind mount (`...:/data/firewalls:z`).
- Se o servidor já tiver um FTP na porta 21, mude `FTP_PORT` no `.env`.

## Segurança das senhas

| O quê | Como é armazenado | Por quê |
|---|---|---|
| Senhas de login | Hash **Argon2id** (irreversível) | Só precisa ser comparada |
| Senhas dos switches, enable e SMTP | **Criptografia Fernet** (AES + HMAC) | Precisam ser enviadas ao equipamento, então um hash (irreversível) não funcionaria |

Nenhuma senha é gravada em texto puro, nem exibida de volta na interface. A chave de criptografia fica em
`/data/keys/encryption.key`, separada do banco (`/data/switchsafe.db`). Alternativamente, informe-a pela
variável `SWITCHSAFE_ENCRYPTION_KEY` (gere com
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) para que ela
não fique no mesmo volume dos dados.

> **Guarde uma cópia da chave.** Sem ela não é possível descriptografar as credenciais dos switches
> (os backups já feitos continuam legíveis, pois são arquivos de texto).

## Dados persistidos (volume `/data`)

```
/data
├── switchsafe.db         # banco SQLite (switches, histórico, configurações, usuários)
├── keys/
│   ├── encryption.key    # chave Fernet das credenciais
│   └── session.key       # chave de assinatura das sessões
├── backups/                                   # módulo Switches
│   └── switch_<id>/<nome>_<AAAAMMDD-HHMMSS>.cfg
└── firewalls/                                 # módulo Firewalls (home FTP de cada um)
    └── <NOME-DO-FIREWALL>/<arquivo>_<AAAAMMDD-HHMMSS>.<ext>
```

Para fazer backup do próprio Switch Safe, copie o volume:

```bash
docker run --rm -v switch-safe_switchsafe-data:/data -v "$PWD":/out alpine tar czf /out/switch-safe-data.tgz -C /data .
```

Se preferir um diretório do host (bind mount) em vez do volume nomeado, ele precisa pertencer ao UID 1000:
`mkdir -p ./data && sudo chown 1000:1000 ./data` e troque no compose para `- ./data:/data`.

## Retenção

Aplicada após cada execução e diariamente às 04:30:

- **Dias**: remove backups mais antigos que N dias (0 = sem limite);
- **Quantidade por switch**: mantém apenas os N mais recentes (0 = sem limite);
- O **backup bem-sucedido mais recente de cada switch nunca é removido**;
- Registros de falha: mantidos os 50 mais recentes por switch.

## Detecção de alteração

Cada backup é comparado ao anterior do mesmo switch. Linhas que mudam a cada coleta sem representar
alteração real (`! Last configuration change`, `ntp clock-period`, cabeçalho do MikroTik etc.) são
ignoradas na comparação, evitando falsos alertas de "configuração alterada".

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `APP_PORT` | `8080` | Porta HTTP |
| `TZ` | `America/Sao_Paulo` | Fuso do agendamento e das datas |
| `SESSION_COOKIE_SECURE` | `false` | `true` quando servido via HTTPS |
| `TRUST_PROXY` | `false` | `true` atrás de proxy reverso (usa `X-Forwarded-*`) |
| `BACKUP_WORKERS` | `4` | Switches copiados em paralelo |
| `SESSION_LIFETIME_MINUTES` | `480` | Duração da sessão |
| `SWITCHSAFE_ENCRYPTION_KEY` | — | Chave Fernet (opcional; senão usa o arquivo em `/data/keys`) |
| `LOG_LEVEL` | `INFO` | Nível de log |

## HTTPS

A aplicação fala HTTP. Para expor fora da rede de gerência, coloque-a atrás de um proxy reverso com TLS
(nginx, Traefik, Caddy) e defina `SESSION_COOKIE_SECURE=true` e `TRUST_PROXY=true`.

## Módulo Firewalls (FTP)

Firewalls como SonicWall e FortiGate **enviam** o backup agendado para um servidor FTP. O Switch Safe tem um
servidor FTP embutido para isso:

1. Em **Firewalls › Cadastrar firewall**, informe nome, fabricante, usuário FTP e senha (botão *Gerar*).
   Opcionalmente restrinja os IPs de origem e defina em quantas horas o backup deve chegar.
2. A tela do firewall mostra os dados para configurar no equipamento (servidor, porta, usuário, modo passivo).
3. No firewall, agende o backup/exportação de configuração via FTP com esses dados.

Como funciona:

- Cada firewall tem **usuário FTP próprio** (senha em **hash Argon2**, irreversível) e fica **preso ao seu
  diretório** `/data/firewalls/<NOME>/`. Pode apenas listar e enviar: não baixa, não apaga, não renomeia.
- Cada arquivo recebido é **renomeado com data/hora** (`backup_20261008-020000.exp`) para nunca sobrescrever o
  anterior, e registrado com tamanho, SHA-256, IP de origem e indicação de conteúdo alterado.
- Se o backup **não chegar no intervalo esperado**, é enviado um alerta por e-mail (uma vez, até voltar a chegar).
- Retenção própria do módulo em **Firewalls › Configurações** (dias e quantidade por firewall).
- Após 5 senhas erradas em 10 min o IP é bloqueado temporariamente.

Configuração no `.env`:

| Variável | Padrão | Descrição |
|---|---|---|
| `FTP_PUBLIC_HOST` | — | **IP deste servidor como os firewalls o enxergam.** Obrigatório para o modo passivo funcionar através do Docker |
| `FIREWALL_BACKUP_PATH` | volume Docker | Pasta do host para os arquivos (ex.: `/mnt/fw-backup/switch-safe`); uma subpasta por firewall. Dono UID 1000 |
| `FTP_PORT` | `21` | Porta FTP exposta no host |
| `FTP_PASSIVE_PORTS` | `30000-30009` | Faixa passiva (libere no firewall de rede; 10 portas = 10 envios simultâneos) |
| `FTP_ENABLED` | `true` | Desliga o servidor FTP |

> **FTP não é criptografado.** Mantenha o tráfego na rede de gerência e use a restrição de IP por firewall.

> **Restrição de IP e Docker:** confira em *Último login FTP* se o IP exibido é o do firewall. Se aparecer o
> gateway do Docker (ex.: `172.18.0.1`), o Docker está mascarando a origem (comum no Docker Desktop/WSL ou com
> `userland-proxy`); nesse caso a restrição por IP não funciona — use `network_mode: host` no serviço ou deixe o
> campo em branco.

## Switch simulado (demonstração)

O projeto inclui um switch Cisco IOS falso ([tests/fake_switch.py](tests/fake_switch.py)) para experimentar
a aplicação sem equipamento real:

```bash
docker compose --profile demo up -d
```

Cadastre no Switch Safe (tipo **Cisco IOS / IOS-XE**, host **`fake-switch`**):

| Switch | Porta | Usuário | Senha | Enable |
|---|---|---|---|---|
| SW-ENABLE | 2201 | admin | cisco123 | marcar e usar `enable123` |
| SW-PRIV | 2202 | admin | cisco123 | não marcar |

**Alterando a configuração** (para ver a diferença em *Backups → Comparar*): acesse o switch por SSH e use
os comandos de configuração do IOS. As alterações ficam salvas no volume `fake-switch-state`.

```text
ssh -p 2201 admin@localhost          # senha: cisco123
SW-ENABLE> enable                    # senha: enable123
SW-ENABLE# conf t
SW-ENABLE(config)# int gi0/3
SW-ENABLE(config-if)# description SERVIDOR-ERP
SW-ENABLE(config-if)# switchport access vlan 20
SW-ENABLE(config-if)# no shutdown
SW-ENABLE(config-if)# exit
SW-ENABLE(config)# vlan 30
SW-ENABLE(config-vlan)# name VOIP
SW-ENABLE(config-vlan)# end
SW-ENABLE# show run
SW-ENABLE# exit
```

Digite `?` no switch para ver os comandos aceitos (`hostname`, `interface`, `vlan`, `line`, `description`,
`shutdown`, `no ...`, `ntp server`, `do show run`, `write memory`…). Depois clique em **Backup agora**
e abra **Comparar**.

- Para simular **falhas**: senha errada, enable errado, ou porta 2201 sem marcar enable.
- Acompanhe as conexões: `docker logs -f switch-safe-fake-switch`
- Voltar à configuração original: `docker compose --profile demo down fake-switch && docker volume rm switch-safe_fake-switch-state`
- Desligar: `docker compose --profile demo stop fake-switch`

## Testes automatizados

```bash
bash tests/run_e2e.sh
```

Sobe um ambiente isolado (projeto `switch-safe-test`, com volume próprio) contendo o app, o switch simulado
e um servidor SMTP falso; executa ~106 verificações (login, criptografia, backup com/sem enable, falhas, módulo FTP de firewalls,
detecção de alteração, alertas, retenção, downloads) e remove tudo ao final. Não afeta a instância real.

## Observações

- Rode com **1 worker** do gunicorn (padrão): o agendador roda dentro do processo da aplicação.
- O container precisa alcançar os switches via SSH/Telnet a partir da rede do host Docker.
