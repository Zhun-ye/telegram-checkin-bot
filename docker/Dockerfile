FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    APP_DIR=/opt/telegram-checkin-bot \
    DATA_DIR=/data

WORKDIR ${APP_DIR}

COPY main.py README.md ${APP_DIR}/
COPY --chmod=755 docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends tzdata; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*; \
    pip install --no-cache-dir telethon apscheduler

VOLUME ["/data"]

ENTRYPOINT ["docker-entrypoint.sh"]
