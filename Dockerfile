FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS builder

ARG SCHEMII_VERSION=development
ARG SCHEMII_REVISION=development

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY dist ./dist
RUN set -- /build/dist/*.whl; test "$#" -eq 1; python -m pip install --no-cache-dir "$1"

FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS runtime

ARG SCHEMII_VERSION=development
ARG SCHEMII_REVISION=development
ARG SCHEMII_SOURCE=https://github.com/LandMineDevelopment/schemii
LABEL org.opencontainers.image.title="Schemii" \
      org.opencontainers.image.version="$SCHEMII_VERSION" \
      org.opencontainers.image.revision="$SCHEMII_REVISION" \
      org.opencontainers.image.source="$SCHEMII_SOURCE"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCHEMII_HOST=0.0.0.0 \
    SCHEMII_PORT=8080 \
    SCHEMII_CONFIG_DIR=/data/config \
    SCHEMII_SCHEMA_DIR=/data/schemas \
    SCHEMII_BEHIND_LOOPBACK_PROXY=0

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0555 docker/runtime-secret-entrypoint.sh /usr/local/bin/schemii-runtime

RUN useradd --create-home --uid 10001 schemii \
    && mkdir -p /data/config /data/schemas /data/dashboards \
    && chown -R schemii:schemii /data

USER root
ENTRYPOINT ["/usr/local/bin/schemii-runtime"]
EXPOSE 8080

EXPOSE 8081
CMD ["schemii"]
