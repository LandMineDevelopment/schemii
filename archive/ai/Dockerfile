FROM node:22.22.2-alpine3.22@sha256:b77017c37f430e4466ff497058948a2f16e8b59779600d53711eeb7b999b0f4e AS tool-dependencies

WORKDIR /dependencies
COPY workspace/.opencode/package*.json ./
RUN npm ci --omit=dev --omit=optional --ignore-scripts

FROM ghcr.io/anomalyco/opencode:1.18.15@sha256:59b2582fb5a10b7022d8b3347a9d9c60710d526ad6bfd02eb17a2c8582b35809

ARG SCHEMII_VERSION=development
ARG SCHEMII_REVISION=development
ARG SCHEMII_SOURCE=https://github.com/LandMineDevelopment/schemii
LABEL org.opencontainers.image.title="Schemii OpenCode" \
      org.opencontainers.image.version="$SCHEMII_VERSION" \
      org.opencontainers.image.revision="$SCHEMII_REVISION" \
      org.opencontainers.image.source="$SCHEMII_SOURCE"

COPY --from=tool-dependencies /dependencies/node_modules /opt/opencode/node_modules
COPY --chmod=0555 secret-entrypoint.sh /usr/local/bin/schemii-opencode
ENTRYPOINT ["/usr/local/bin/schemii-opencode"]
