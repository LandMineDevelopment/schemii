FROM node:22.22.2-alpine3.22@sha256:b77017c37f430e4466ff497058948a2f16e8b59779600d53711eeb7b999b0f4e AS deps
WORKDIR /dependencies
COPY archive/ai/workspace/.opencode/package*.json ./
RUN npm ci --omit=dev --omit=optional --ignore-scripts
FROM ghcr.io/anomalyco/opencode:1.18.15@sha256:59b2582fb5a10b7022d8b3347a9d9c60710d526ad6bfd02eb17a2c8582b35809
COPY --from=deps /dependencies/node_modules /opt/opencode/node_modules
COPY --chmod=0555 ai/secret-entrypoint.sh /usr/local/bin/schemii-opencode
ENTRYPOINT ["/usr/local/bin/schemii-opencode"]
