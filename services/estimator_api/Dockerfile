FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ESTIMATOR_API_HOST=0.0.0.0 \
    ESTIMATOR_API_PORT=8000 \
    ESTIMATOR_API_ARTIFACT_DIR=/tmp/spraytec-estimator-artifacts \
    HOME=/tmp

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        fonts-dejavu-core \
        libreoffice-calc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/estimator_api/requirements.txt /tmp/estimator-api-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/estimator-api-requirements.txt

COPY jobscan /app/jobscan
COPY roof_measure /app/roof_measure
COPY services/estimator_api /app/services/estimator_api
COPY templates /app/templates

RUN useradd --create-home --home-dir /tmp/spraytec-api --shell /usr/sbin/nologin spraytec \
    && mkdir -p /tmp/spraytec-estimator-artifacts \
    && chown -R spraytec:spraytec /tmp/spraytec-api /tmp/spraytec-estimator-artifacts

USER spraytec

EXPOSE 8000

CMD ["python", "-m", "services.estimator_api.server"]
