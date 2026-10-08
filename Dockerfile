FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    APP_PORT=8080 \
    TZ=America/Sao_Paulo

WORKDIR /app

# As pastas são criadas com o dono certo para que volumes novos montados nelas herdem essa permissão.
RUN useradd --system --uid 1000 --no-create-home switchsafe \
    && mkdir -p /data/backups /data/firewalls /data/keys \
    && chown -R switchsafe:switchsafe /data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY wsgi.py gunicorn.conf.py ./

USER switchsafe
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('APP_PORT','8080'), timeout=4)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
