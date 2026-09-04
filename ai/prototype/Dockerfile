FROM node:22.22.2-alpine3.22@sha256:b77017c37f430e4466ff497058948a2f16e8b59779600d53711eeb7b999b0f4e
WORKDIR /prototype
COPY ai/prototype/package*.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY ai/prototype/runtime.js ai/prototype/login.js ai/prototype/server.js ai/prototype/turns.js ./
COPY ai/prototype/test/ ./test/
ENV npm_config_cache=/tmp/npm-cache
USER node
CMD ["npm", "test"]
