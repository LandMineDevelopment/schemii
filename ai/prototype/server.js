import { createServer } from 'node:http';
import { timingSafeEqual, createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { once } from 'node:events';
import { createLoginService, LoginError } from './login.js';
import { TurnService, supportedModels } from './turns.js';
import { TurnError } from './runtime.js';

const statuses = { invalid_request: 400, unauthorized: 401, not_found: 404,
  busy: 429, body_too_large: 413, internal_error: 500 };
const digest = value => createHash('sha256').update(value).digest();

export function createHandler({ secret, service = createLoginService(), turns = new TurnService() }) {
  if (typeof secret !== 'string' || !secret.trim()) throw new Error('A sidecar password is required');
  const expected = digest(`Bearer ${secret.trim()}`);
  return async (request, response) => {
    const send = (status, body) => {
      response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify(body));
    };
    try {
      const authorization = request.headers.authorization;
      if (typeof authorization !== 'string' || !timingSafeEqual(digest(authorization), expected)) {
        throw new LoginError('unauthorized');
      }
      if (request.method === 'GET' && request.url === '/health') return send(200, { status: 'ok' });
      if (request.method === 'GET' && request.url === '/models') return send(200, supportedModels());
      const route = { '/logins': 'start', '/logins/status': 'status', '/logins/cancel': 'cancel' };
      const turnRoutes = ['/turns', '/turns/cancel', '/credentials/remove'];
      if (request.method !== 'POST' || (!Object.hasOwn(route, request.url) && !turnRoutes.includes(request.url))) throw new LoginError('not_found');
      if (request.headers['content-type']?.split(';')[0].trim() !== 'application/json') throw new LoginError('invalid_request');
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += Buffer.byteLength(chunk);
        if (size > (request.url === '/turns' ? 10 * 1024 * 1024 : 16 * 1024)) throw new LoginError('body_too_large');
        chunks.push(Buffer.from(chunk));
      }
      let input;
      try { input = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
      catch { throw new LoginError('invalid_request'); }
      if (!input || Array.isArray(input) || typeof input !== 'object') throw new LoginError('invalid_request');
      if (request.url === '/turns/cancel') return send(200, turns.cancel(input));
      if (request.url === '/credentials/remove') return send(200, turns.remove(input));
      if (request.url === '/turns') {
        const controller = new AbortController();
        const disconnected = () => controller.abort();
        response.on('close', disconnected);
        response.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store',
          'X-Accel-Buffering': 'no' });
        let textWireBytes = 0;
        const configuredBytes = input.limits?.responseBytes;
        const wireBudget = 4 * (Number.isSafeInteger(configuredBytes) && configuredBytes > 0
          ? Math.min(configuredBytes, 8 * 1024 * 1024) : 256 * 1024) + 65536;
        const emit = async event => {
          if (response.destroyed) return;
          const line = JSON.stringify(event) + '\n';
          if (event.type === 'text') {
            textWireBytes += Buffer.byteLength(line);
            if (textWireBytes > wireBudget) throw new TurnError('response_too_large');
          }
          if (!response.write(line)) {
            await once(response, 'drain', { signal: AbortSignal.any([
              controller.signal, AbortSignal.timeout(10000),
            ]) });
          }
        };
        try {
          await turns.run(input, { signal: controller.signal, emit });
        } catch (error) {
          const safe = error instanceof TurnError ? error : new TurnError('provider_failed');
          await emit({ type: 'error', code: safe.code, message: safe.message });
        } finally {
          response.off('close', disconnected);
          response.end();
        }
        return;
      }
      return send(200, service[route[request.url]](input));
    } catch (error) {
      const code = (error instanceof LoginError || error instanceof TurnError) && Object.hasOwn(statuses, error.code) ? error.code : 'internal_error';
      if (!response.headersSent && !response.destroyed) send(statuses[code], { error: code });
    }
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const secret = await readFile(process.env.SCHEMII_PI_PASSWORD_FILE || '/run/secrets/opencode_password', 'utf8');
    const server = createServer(createHandler({ secret }));
    server.requestTimeout = 15_000;
    server.headersTimeout = 10_000;
    server.on('error', () => { process.stderr.write('Pi sidecar failed to start.\n'); process.exitCode = 1; });
    server.listen(4097, '0.0.0.0');
  } catch {
    process.stderr.write('Pi sidecar password could not be loaded.\n');
    process.exitCode = 1;
  }
}
