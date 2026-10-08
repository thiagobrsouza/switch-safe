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
└── backups/
    └── switch_<id>/<nome>_<AAAAMMDD-HHMMSS>.cfg
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
e um servidor SMTP falso; executa ~57 verificações (login, criptografia, backup com/sem enable, falhas,
detecção de alteração, alertas, retenção, downloads) e remove tudo ao final. Não afeta a instância real.

## Observações

- Rode com **1 worker** do gunicorn (padrão): o agendador roda dentro do processo da aplicação.
- O container precisa alcançar os switches via SSH/Telnet a partir da rede do host Docker.
