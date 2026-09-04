import { createServer } from 'node:http';
import { timingSafeEqual, createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { createLoginService, LoginError } from './login.js';

const statuses = { invalid_request: 400, unauthorized: 401, not_found: 404,
  busy: 429, body_too_large: 413, internal_error: 500 };
const digest = value => createHash('sha256').update(value).digest();

export function createHandler({ secret, service = createLoginService() }) {
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
      const route = { '/logins': 'start', '/logins/status': 'status', '/logins/cancel': 'cancel' };
      if (request.method !== 'POST' || !Object.hasOwn(route, request.url)) throw new LoginError('not_found');
      if (request.headers['content-type']?.split(';')[0].trim() !== 'application/json') throw new LoginError('invalid_request');
      const chunks = [];
      let size = 0;
      for await (const chunk of request) {
        size += Buffer.byteLength(chunk);
        if (size > 16 * 1024) throw new LoginError('body_too_large');
        chunks.push(Buffer.from(chunk));
      }
      let input;
      try { input = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
      catch { throw new LoginError('invalid_request'); }
      if (!input || Array.isArray(input) || typeof input !== 'object') throw new LoginError('invalid_request');
      return send(200, service[route[request.url]](input));
    } catch (error) {
      const code = error instanceof LoginError && Object.hasOwn(statuses, error.code) ? error.code : 'internal_error';
      send(statuses[code], { error: code });
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
