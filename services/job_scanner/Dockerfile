FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    SCANNER_PERSISTENT_ROOT=/mnt/spraytec-scanner \
    SCANNER_REQUIRE_SEEDED_CACHE=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        antiword \
        fonts-dejavu-core \
        libreoffice-calc \
        poppler-utils \
        postgresql-client \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /tmp/spraytec-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/spraytec-requirements.txt

COPY config /app/config
COPY dashboard /app/dashboard
COPY db /app/db
COPY jobscan /app/jobscan
COPY scripts/daily_refresh.sh /app/scripts/daily_refresh.sh
COPY scripts/azure_scanner_entrypoint.sh /app/scripts/azure_scanner_entrypoint.sh
COPY scripts/refresh_dashboard_snapshots.py /app/scripts/refresh_dashboard_snapshots.py

RUN useradd --create-home --home-dir /tmp/spraytec-scanner --shell /usr/sbin/nologin spraytec \
    && mkdir -p /mnt/spraytec-scanner /app/scripts \
    && chmod 0755 /app/scripts/daily_refresh.sh /app/scripts/azure_scanner_entrypoint.sh \
    && chown -R spraytec:spraytec /tmp/spraytec-scanner /mnt/spraytec-scanner

USER spraytec

CMD ["/app/scripts/azure_scanner_entrypoint.sh"]
