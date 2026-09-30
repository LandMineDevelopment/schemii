import https from 'node:https';
import { ProtocolFailure } from './protocol.mjs';

export const LOCAL = 'https://localhost:8001';
export const PREVIEW = 'https://omarchy.taile4f57f.ts.net';
export function apiPath(path) {
  if (typeof path !== 'string' || !path.startsWith('/api/v1/') || /[\r\n\\#]/.test(path) ||
      new URL(path, LOCAL).origin !== LOCAL || path.includes('..')) throw new ProtocolFailure('invalid_api_path');
  return path;
}

export class HTTPClient {
  constructor({ origin = LOCAL, cookie = '', accounting = null, timeoutMs = 30000, maxBytes = 32 * 1024 * 1024 } = {}) {
    if (![LOCAL, PREVIEW].includes(origin)) throw new ProtocolFailure('invalid_origin');
    this.origin = origin; this.cookie = cookie; this.accounting = accounting;
    this.timeoutMs = timeoutMs; this.maxBytes = maxBytes;
    this.agent = new https.Agent({ keepAlive: true, maxSockets: 64, maxFreeSockets: 4,
      rejectUnauthorized: origin !== LOCAL }); this.pending = new Set();
  }
  async request({ method = 'GET', path, body = undefined, oracle = null, slowMs = 0,
    disconnectAfterBytes = 0, signal, onHeaders = () => {}, validate = () => {} }) {
    apiPath(path);
    const startedAt = performance.now(), payload = body === undefined ? undefined : JSON.stringify(body);
    let status = 0, complete = false, valid = false, timing = {}, responseCookie, failure = null;
    this.accounting?.sent();
    try {
      const response = await new Promise((resolve, reject) => {
        const req = https.request(new URL(path, this.origin), { method, agent: this.agent,
          headers: { ...(this.cookie ? { Cookie: this.cookie } : {}),
            ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}) } }, resolve);
        this.pending.add(req); req.once('close', () => this.pending.delete(req));
        req.on('error', reject);
        const timer = setTimeout(() => req.destroy(new ProtocolFailure('request_deadline')), this.timeoutMs);
        req.once('close', () => clearTimeout(timer));
        const abort = () => req.destroy(new ProtocolFailure('run_cancelled'));
        signal?.addEventListener('abort', abort, { once: true });
        req.once('close', () => signal?.removeEventListener('abort', abort));
        if (signal?.aborted) abort(); else req.end(payload);
      });
      status = response.statusCode;
      responseCookie = response.headers['set-cookie']?.map(value => value.split(';')[0]).join('; ');
      onHeaders(status);
      let bytes = 0; const chunks = [];
      try {
        for await (const chunk of response) {
          bytes += chunk.length;
          if (bytes > this.maxBytes) throw new ProtocolFailure('body_budget');
          if (disconnectAfterBytes && bytes >= disconnectAfterBytes) {
            response.destroy(); throw new ProtocolFailure('intentional_disconnect');
          }
          if (status >= 200 && status < 300 && oracle) oracle.push(chunk); else chunks.push(chunk);
          if (slowMs) await new Promise(resolve => setTimeout(resolve, slowMs));
        }
      } catch (error) { response.destroy(); throw error; }
      complete = response.complete;
      if (!complete) throw new ProtocolFailure('incomplete_body');
      if (status >= 200 && status < 300 && oracle) timing = oracle.finish();
      let data = null;
      if (!oracle || status < 200 || status >= 300) {
        const content = Buffer.concat(chunks).toString('utf8');
        if (content) { try { data = JSON.parse(content); } catch { throw new ProtocolFailure('invalid_response_json'); } }
      }
      if (status >= 200 && status < 300) validate(data);
      valid = status >= 200 && status < 300;
      return { status, data, cookie: responseCookie, timing: { ...timing, httpMs: performance.now() - startedAt } };
    } catch (error) {
      failure = error instanceof ProtocolFailure ? error.code : 'http_transport_failure';
      if (!(error instanceof ProtocolFailure)) throw new ProtocolFailure(failure);
      throw error;
    } finally {
      this.accounting?.finish({ status, complete, valid, failure, timing: { ...timing, httpMs: performance.now() - startedAt } });
    }
  }
  async json(method, path, body, expected = 200) {
    const response = await this.request({ method, path, body });
    if (response.status !== expected) throw new ProtocolFailure(`unexpected_http_${response.status}`);
    return response.data;
  }
  async login(account, expectedId) {
    const response = await this.request({ method: 'POST', path: '/api/v1/auth/login', body: account });
    this.cookie = response.cookie || '';
    if (response.status !== 200 || !response.cookie || response.data?.user?.username !== account.username ||
        (expectedId && response.data.user.id !== expectedId)) {
      try { await this.logout(); } catch { /* Preserve the identity failure; no session is adopted. */ }
      throw new ProtocolFailure('identity_mismatch');
    }
    return response.data;
  }
  async logout() { if (this.cookie) await this.json('POST', '/api/v1/auth/logout'); this.cookie = ''; }
  close() { for (const request of this.pending) request.destroy(new ProtocolFailure('run_cancelled')); this.agent.destroy(); }
}
