import os

bind = f"0.0.0.0:{os.environ.get('APP_PORT', '8080')}"

# O agendador roda dentro do processo da aplicação: manter 1 worker
# evita backups duplicados. Concorrência fica por conta das threads.
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = 120
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
