FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    APP_DIR=/opt/telegram-checkin-bot \
    DATA_DIR=/data

WORKDIR ${APP_DIR}

COPY main.py README.md ${APP_DIR}/
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    chmod +x /usr/local/bin/docker-entrypoint.sh; \
    pip install --no-cache-dir --upgrade pip; \
    pip install --no-cache-dir telethon apscheduler

VOLUME ["/data"]

ENTRYPOINT ["docker-entrypoint.sh"]
