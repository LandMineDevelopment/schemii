FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml constraints.docker.txt README.md LICENSE ./
COPY src ./src
COPY Dockerfile compose.test.yaml start.sh ./
COPY dev/runtime-contract.json dev/runtime_manifest.py ./dev/
COPY dev/schemii.toml ./dev/
COPY dev/ingress/nginx.conf ./dev/ingress/
COPY dev/postgres/*.sh dev/postgres/*.sql ./dev/postgres/
COPY ai/prototype/*.js ai/prototype/package.json ai/prototype/package-lock.json ai/prototype/Dockerfile ./ai/prototype/
ARG SCHEMII_SOURCE_COMMIT=unknown
RUN python dev/runtime_manifest.py --source-commit "$SCHEMII_SOURCE_COMMIT" > /runtime-build.json
RUN python -m pip wheel --constraint constraints.docker.txt --wheel-dir /wheels .

FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS runtime

LABEL org.opencontainers.image.title="Schemii" \
      org.opencontainers.image.description="Unified Schemii prototype application"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m venv /opt/venv \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin schemii
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels schemii \
    && rm -rf /wheels

ARG SCHEMII_SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=$SCHEMII_SOURCE_COMMIT
COPY --from=builder /runtime-build.json /usr/share/schemii/runtime-build.json
RUN /opt/venv/bin/python -c 'import importlib.metadata,json; print(json.dumps({d.metadata["Name"]:d.version for d in importlib.metadata.distributions()},sort_keys=True))' > /usr/share/schemii/runtime-installed.json

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness', timeout=2).read()"]

CMD ["uvicorn", "schemii.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
