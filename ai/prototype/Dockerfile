FROM node:22.22.2-alpine3.22@sha256:b77017c37f430e4466ff497058948a2f16e8b59779600d53711eeb7b999b0f4e
ARG SCHEMII_SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=$SCHEMII_SOURCE_COMMIT
WORKDIR /prototype
COPY ai/prototype/package*.json ./
ARG SCHEMII_BUILD_MODE=standard
RUN --mount=type=bind,target=/qa-context \
    case "$SCHEMII_BUILD_MODE" in \
      standard) npm ci --omit=dev --ignore-scripts ;; \
      offline) cache="$(mktemp -d)" \
        && cp -R /qa-context/qa-build-inputs/npm-cache/. "$cache" \
        && npm ci --offline --cache "$cache" --omit=dev --ignore-scripts \
           --no-audit --no-fund --logs-max=0 --userconfig=/dev/null \
        && rm -rf "$cache" ;; \
      *) exit 1 ;; \
    esac
COPY ai/prototype/*.js ./
COPY ai/prototype/test/ ./test/
ENV npm_config_cache=/tmp/npm-cache
USER node
CMD ["npm", "test"]
