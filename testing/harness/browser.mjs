import { chromium } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { assertProviderAvailable } from './prerequisites.mjs';
import { assertProductNavigationAllowed } from './readiness.mjs';

const COOKIE = 'schemii_session';
const STORAGE_KEY = '__schemii_qa_isolation__';
export const dialogDetails = dialog => ({ type:dialog.type(), message:dialog.message(), defaultValue:dialog.defaultValue() });
const safeId = value => /^[a-zA-Z0-9_-]+$/.test(value);

// Self-contained so Playwright can run this same inspection in either a
// snapshot's evaluateAll or one target's evaluate without injecting helpers.
function inspectControls(target) {
  return (Array.isArray(target) ? target : [target]).map(el => {
    const inertAncestor = el.closest('[inert]');
    const nativeDisabled = el.matches(':disabled');
    const ariaDisabled = el.closest('[aria-disabled]')?.getAttribute('aria-disabled') === 'true';
    const style = getComputedStyle(el);
    const visible = [...el.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)
      && !['hidden', 'collapse'].includes(style.visibility);
    const reasons = [];
    if (inertAncestor) reasons.push(`inert ancestor ${inertAncestor.id ? `#${inertAncestor.id}` : inertAncestor.tagName.toLowerCase()}`);
    if (nativeDisabled) reasons.push('disabled');
    if (ariaDisabled) reasons.push('aria-disabled');
    if (!visible) reasons.push('not visible');
    return {
      tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), ariaLabel: el.getAttribute('aria-label'), text: el.innerText?.trim().slice(0, 200),
      id: el.id, type: el.getAttribute('type'), placeholder: el.getAttribute('placeholder'),
      disabled: nativeDisabled || ariaDisabled, inert: Boolean(inertAncestor), visible, unavailable: reasons.length > 0, reasons,
      readOnly: Boolean(el.readOnly),
    };
  });
}

/** Live browser handles and credentials never leave this process. */
export class BrowserFleet {
  #credentials = new Map();
  #isolationProven = false;
  #pagesParkedForIsolation = false;

  constructor({ baseURL, runDir, headless = false, timeoutMs = 15000, onEvent = () => {}, onLaunch = async () => {} }) {
    this.baseURL = new URL(baseURL).origin;
    this.runDir = path.resolve(runDir);
    this.headless = headless;
    this.timeoutMs = timeoutMs;
    this.onEvent = onEvent;
    this.onLaunch = onLaunch;
    this.lanes = new Map();
  }

  #url(value = '/') {
    const url = new URL(value, this.baseURL);
    if (url.origin !== this.baseURL || url.username || url.password) throw new Error('Navigation must stay on the application origin');
    return url.href;
  }

  #handle(id) {
    const handle = this.lanes.get(id);
    if (!handle || handle.closing) throw new Error(`Browser lane ${id} is unavailable`);
    return handle;
  }

  #event(handle, kind, detail = {}) {
    this.onEvent({ lane: handle.lane.id, kind, time: new Date().toISOString(), ...detail });
  }

  async #login(handle) {
    const credentials = this.#credentials.get(handle.lane.id);
    const response = await handle.context.request.post(`${this.baseURL}/api/v1/auth/login`, {
      data: credentials, headers: { Origin: this.baseURL }, timeout: this.timeoutMs,
    });
    if (!response.ok()) throw new Error(`Login failed for lane ${handle.lane.id} (HTTP ${response.status()})`);
    return this.#identity(handle);
  }

  async #identity(handle) {
    const response = await handle.context.request.get(`${this.baseURL}/api/v1/auth/me`, { timeout: this.timeoutMs });
    if (!response.ok()) throw new Error(`Identity check failed for lane ${handle.lane.id} (HTTP ${response.status()})`);
    const identity = await response.json();
    if (identity.user?.username !== handle.lane.username.toLowerCase()) throw new Error(`Identity mismatch for lane ${handle.lane.id}`);
    const capabilities = identity.capabilities ?? [];
    if (handle.lane.expectedCapabilities) {
      const expected=[...handle.lane.expectedCapabilities].sort();
      if(JSON.stringify([...capabilities].sort())!==JSON.stringify(expected))throw new Error(`Lane ${handle.lane.id} effective permissions differ from its persona.`);
    }
    for (const product of handle.lane.products ?? []) {
      const allowed=capabilities.includes(`${product}:access`);
      if ((handle.lane.deniedProducts || []).includes(product)) {
        if(allowed)throw new Error(`Lane ${handle.lane.id} unexpectedly has ${product}:access`);
      }else if(!allowed)throw new Error(`Lane ${handle.lane.id} lacks ${product}:access`);
    }
    return { username: identity.user.username, capabilities };
  }

  async openLane(lane, credentials) {
    if (!safeId(lane.id)) throw new Error('Lane ID must contain only letters, digits, underscore or hyphen');
    if (this.lanes.has(lane.id)) throw new Error(`Lane ${lane.id} already exists`);
    if (lane.username?.toLowerCase() !== credentials.username?.toLowerCase()) throw new Error('Lane credentials do not match its assigned account');
    if ([...this.lanes.values()].some(h => h.lane.username.toLowerCase() === lane.username.toLowerCase())) throw new Error('Active lanes require distinct accounts');
    const directory = path.join(this.runDir, lane.id);
    await mkdir(directory, { recursive: true, mode: 0o700 });
    // The endpoint is deliberately confined to this process. The BrowserServer
    // gives us an actual Chromium PID for orphan detection and launch evidence.
    const server = await chromium.launchServer({ host: '127.0.0.1', headless: this.headless, timeout: this.timeoutMs });
    const handle = { lane, server, directory, launchId: randomUUID(), queue: Promise.resolve(), pendingDialog: null };
    this.lanes.set(lane.id, handle);
    this.#isolationProven = false;
    this.#pagesParkedForIsolation = false;
    this.#credentials.set(lane.id, { username: credentials.username, password: credentials.password });
    try {
      handle.pid = server.process().pid;
      const processStat = await readFile(`/proc/${handle.pid}/stat`, 'utf8');
      handle.birthTick = processStat.slice(processStat.lastIndexOf(')') + 2).split(' ')[19];
      if (!handle.birthTick) throw new Error('Cannot identify Chromium process birth time');
      // Persist process ownership before connecting, authenticating, or probing.
      // Callback failures take the same server-closing path as every open error.
      await this.onLaunch({ laneId: lane.id, pid: handle.pid, birthTick: handle.birthTick, launchId: handle.launchId });
      let browser;
      try { browser = await chromium.connect(server.wsEndpoint(), { timeout: this.timeoutMs }); }
      catch { throw new Error('Cannot connect to the isolated Chromium process'); }
      handle.browser = browser;
      browser.on('disconnected', () => { if (!handle.closing) this.#event(handle, 'disconnected'); });
      handle.context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: lane.viewport ?? { width: 1440, height: 1000 } });
      handle.context.setDefaultTimeout(this.timeoutMs);
      handle.context.setDefaultNavigationTimeout(this.timeoutMs);
      handle.page = await handle.context.newPage();
      handle.page.on('dialog', dialog => {
        handle.pendingDialog = dialog;
        this.#event(handle, 'dialog', { type: dialog.type() });
        handle.dialogSignal?.({ dialog: dialogDetails(dialog), requires:'explicit dialog accept or dismiss' });
      });
      // Error text can include credentials or query data; only persist the category.
      handle.page.on('pageerror', () => this.#event(handle, 'page-error'));
      handle.page.on('console', message => { if (message.type() === 'error') this.#event(handle, 'console-error'); });
      const identity = await this.#login(handle);
      // Keep readiness on a page that cannot start a product data stream. The
      // isolation proof intentionally logs each account out before restoring it.
      await handle.page.goto(this.#url('/account'), { waitUntil: 'domcontentloaded' });
      this.#event(handle, 'opened', { launchId: handle.launchId });
      return { lane: lane.id, launchId: handle.launchId, pid: handle.pid, birthTick: handle.birthTick, identity, url: handle.page.url() };
    } catch (error) {
      await this.closeLane(lane.id);
      throw error;
    }
  }

  // Coordinator must hold the wave barrier: no actions or lane lifecycle changes
  // may start until this fleet-wide authentication exercise finishes.
  async parkLanesForIsolation() {
    this.#isolationProven = false;
    this.#pagesParkedForIsolation = false;
    const handles = [...this.lanes.values()];
    if (!handles.length) throw new Error('Isolation needs at least one active lane');
    // Recovery can happen while other lanes are ready but still unclaimed. Move
    // every such page off its product before revoking the session cookies.
    for (const handle of handles) {
      await handle.queue;
      if (new URL(handle.page.url()).pathname !== '/account') {
        await handle.page.goto(this.#url('/account'), { waitUntil: 'domcontentloaded' });
      }
    }
    this.#pagesParkedForIsolation = true;
  }

  async proveIsolation() {
    this.#isolationProven = false;
    if (!this.#pagesParkedForIsolation) throw new Error('Lane pages must be parked before the isolation proof');
    this.#pagesParkedForIsolation = false;
    const handles = [...this.lanes.values()];
    if (!handles.length) throw new Error('Isolation needs at least one active lane');
    if (new Set(handles.map(handle => handle.pid)).size !== handles.length) throw new Error('Browser lanes share a Chromium process');
    const cookies = [];
    const markers = new Map();
    for (const handle of handles) {
      await handle.queue;
      await this.#identity(handle);
      const cookie = (await handle.context.cookies(this.baseURL)).find(item => item.name === COOKIE);
      if (!cookie?.value) throw new Error(`Lane ${handle.lane.id} has no session cookie`);
      cookies.push(cookie.value);
      const marker = randomUUID();
      markers.set(handle.lane.id, marker);
      await handle.page.evaluate(({ key, marker }) => localStorage.setItem(key, marker), { key: STORAGE_KEY, marker });
    }
    if (new Set(cookies).size !== handles.length) throw new Error('Browser lanes share a session cookie');
    for (const handle of handles) {
      const actual = await handle.page.evaluate(key => localStorage.getItem(key), STORAGE_KEY);
      if (actual !== markers.get(handle.lane.id)) throw new Error('Browser lanes share local storage');
    }
    for (const handle of handles) {
      try {
        const response = await handle.context.request.post(`${this.baseURL}/api/v1/auth/logout`, { headers: { Origin: this.baseURL }, timeout: this.timeoutMs });
        if (!response.ok()) throw new Error('Isolation logout failed');
        const loggedOut = await handle.context.request.get(`${this.baseURL}/api/v1/auth/me`, { timeout: this.timeoutMs });
        if (loggedOut.status() !== 401) throw new Error('Isolation logout did not end the selected session');
        for (const other of handles) if (other !== handle) await this.#identity(other);
      } finally {
        await this.#login(handle);
      }
    }
    const identities = await Promise.all(handles.map(async handle => ({ lane: handle.lane.id, ...await this.#identity(handle) })));
    this.#isolationProven = true;
    return { passed: true, lanes: handles.length, distinctAccounts: true, distinctLaunches: new Set(handles.map(h => h.launchId)).size === handles.length, distinctProcesses: true, processes: handles.map(({ lane, pid, birthTick }) => ({ lane: lane.id, pid, birthTick })), distinctCookies: true, distinctStorage: true, logoutIsolation: true, identities };
  }

  async navigateLane(id) {
    const handle = this.#handle(id);
    const task = handle.queue.then(async () => {
      await this.#identity(handle);
      const product = handle.lane.products?.[0];
      const target = handle.lane.url ?? (product && product !== 'schemii' ? `/${product}` : '/');
      const destination = this.#url(target);
      assertProductNavigationAllowed(destination, this.#isolationProven, this.baseURL);
      await handle.page.goto(destination, { waitUntil: 'domcontentloaded' });
      this.#event(handle, 'product-opened', { product: product ?? 'schemii' });
      return { lane: id, url: handle.page.url() };
    });
    handle.queue = task.catch(() => {});
    return task;
  }

  #locator(page, args) {
    if (args.role) return page.getByRole(args.role, { name: args.name, exact: args.exact ?? true });
    if (args.label) return page.getByLabel(args.label, { exact: args.exact ?? true });
    if (args.selector) return page.locator(args.selector);
    throw new Error('Supply role/name, label, or selector');
  }

  async #availableLocator(page, args, { typing = false } = {}) {
    const locator = this.#locator(page, args);
    let diagnostic = locator;
    if (await locator.count() === 0 && args.role) {
      diagnostic = page.getByRole(args.role, { name: args.name, exact: args.exact ?? true, includeHidden: true });
    }
    if (await diagnostic.count() === 1) {
      const [state] = await diagnostic.evaluate(inspectControls);
      if (state.unavailable || (typing && state.readOnly)) {
        const reasons = [...state.reasons, ...(typing && state.readOnly ? ['read-only'] : [])];
        throw new Error(`Control unavailable: ${reasons.join(', ')}. Choose an available control or establish the scenario prerequisites.`);
      }
    }
    return locator;
  }

  async #screenshot(handle, page = handle.page, prefix = 'screen') {
    const filename = path.join(handle.directory, `${prefix}-${Date.now()}-${randomUUID().slice(0, 8)}.png`);
    await page.screenshot({ path: filename, fullPage: true, mask: [page.locator('input[type="password"], [data-secret]')] });
    return filename;
  }

  async #withDialog(handle, operation) {
    const dialog = new Promise(resolve => { handle.dialogSignal = resolve; });
    try { return await Promise.race([operation(), dialog]); }
    finally { handle.dialogSignal = null; }
  }

  async action(id, action, args = {}) {
    const handle = this.#handle(id);
    const task = handle.queue.then(async () => {
      await this.#identity(handle);
      const page = handle.page;
      if (new URL(page.url()).origin !== this.baseURL) throw new Error('Browser left the application origin; recover the lane before continuing');
      if (handle.pendingDialog && action !== 'dialog' && action !== 'identity') return { dialog: dialogDetails(handle.pendingDialog), requires: 'dialog accept or dismiss' };
      switch (action) {
        case 'identity': return this.#identity(handle);
        case 'navigate': return await this.#withDialog(handle, async () => {
          const destination = this.#url(args.url);
          assertProductNavigationAllowed(destination, this.#isolationProven, this.baseURL);
          await page.goto(destination, { waitUntil: 'domcontentloaded' });
          return { url: page.url() };
        });
        case 'snapshot': return {
          url: page.url(), title: await page.title(), screenshot: await this.#screenshot(handle),
          accessibility: await page.locator('body').ariaSnapshot(),
          elements: {
            text: (await page.locator('body').innerText()).slice(0, 25000),
            controls: (await page.locator('button,a,input,select,textarea,[role]').evaluateAll(inspectControls)).slice(0, 300),
          },
        };
        case 'click': return await this.#withDialog(handle, async () => { await (await this.#availableLocator(page, args)).click({ noWaitAfter: true }); return { clicked: true }; });
        case 'type': {
          const locator = await this.#availableLocator(page, args, { typing: true });
          if (args.clear === false) await locator.pressSequentially(String(args.value ?? ''));
          else await locator.fill(String(args.value ?? ''));
          return { typed: true };
        }
        case 'press': return await this.#withDialog(handle, async () => {
          if (args.role || args.label || args.selector) await (await this.#availableLocator(page, args)).press(args.key, { noWaitAfter: true });
          else await page.keyboard.press(args.key);
          return { pressed: true };
        });
        case 'scroll': await page.mouse.wheel(Number(args.x ?? 0), Number(args.y ?? 600)); return { scrolled: true };
        case 'resize': await page.setViewportSize({ width: Number(args.width), height: Number(args.height) }); return { viewport: page.viewportSize() };
        case 'screenshot': return { screenshot: await this.#screenshot(handle) };
        case 'dialog': {
          const dialog = handle.pendingDialog;
          if (!dialog) throw new Error('No pending dialog');
          if (!['accept', 'dismiss'].includes(args.action)) throw new Error('Dialog action must be accept or dismiss');
          const details = dialogDetails(dialog);
          await dialog[args.action](args.action === 'accept' ? args.promptText : undefined); handle.pendingDialog = null;
          return { handled: args.action, dialog:details };
        }
        case 'drag': {
          await page.mouse.move(Number(args.from.x), Number(args.from.y)); await page.mouse.down();
          try { await page.mouse.move(Number(args.to.x), Number(args.to.y), { steps: 12 }); }
          finally { await page.mouse.up(); }
          return { dragged: true };
        }
        case 'download': {
          const locator = await this.#availableLocator(page, args);
          if (args.browserFallback === true) {
            const available = await page.evaluate(() => {
              // File System Access pickers cannot be driven through a browser
              // download event. Exercise the app's supported browser fallback.
              globalThis.showSaveFilePicker = undefined;
              return typeof globalThis.showSaveFilePicker === 'function';
            });
            if (available) throw new Error('Browser download fallback could not disable the native save picker.');
          }
          const [download] = await Promise.all([page.waitForEvent('download'), locator.click({ noWaitAfter: true })]);
          const filename = path.join(handle.directory, `${randomUUID()}-${path.basename(download.suggestedFilename()).replace(/[^a-zA-Z0-9._-]/g, '_')}`);
          await download.saveAs(filename);
          return { download: filename };
        }
        case 'upload': {
          const filename = String(args.fileName || '');
          const content = args.content;
          if (!/^[a-zA-Z0-9_.-]{1,80}\.(csv|json)$/i.test(filename) || typeof content !== 'string')
            throw new Error('Upload needs a CSV or JSON fileName and text content.');
          const buffer = Buffer.from(content, 'utf8');
          if (!buffer.length || buffer.length > 1024 * 1024)
            throw new Error('Upload content must be 1 through 1048576 bytes.');
          const locator = page.locator(args.selector || 'input[type="file"]');
          if (await locator.count() !== 1 || await locator.getAttribute('type') !== 'file')
            throw new Error('Upload requires one exact file input selector.');
          await locator.setInputFiles({ name: filename, mimeType: filename.toLowerCase().endsWith('.csv') ? 'text/csv' : 'application/json', buffer });
          return { uploaded: filename, bytes: buffer.length };
        }
        default: throw new Error(`Unsupported browser action: ${action}`);
      }
    });
    handle.queue = task.catch(() => {});
    return task;
  }

  async probeCapabilities(id) {
    const handle = this.#handle(id);
    const task = handle.queue.then(async () => {
      await this.#identity(handle);
      const page = await handle.context.newPage();
      try {
        await page.setContent(`<!doctype html><title>QA browser capability check</title>
          <label>Probe input <input id="input"></label><input id="upload" type="file"><button id="click">Click probe</button>
          <button id="confirm">Confirm probe</button><a id="download" download="probe.txt">Download probe</a>
          <div id="drag" style="margin:30px;width:160px;height:100px;background:#acf">Drag probe</div>
          <script>document.querySelector('#click').onclick=()=>document.body.dataset.clicked='yes';
          document.querySelector('#confirm').onclick=()=>document.body.dataset.confirmed=String(confirm('Probe'));
          document.querySelector('#download').href=URL.createObjectURL(new Blob(['qa-download-ok']));
          document.querySelector('#drag').onpointerdown=()=>document.body.dataset.down='yes';
          document.onpointermove=event=>{if(event.buttons===1)document.body.dataset.moved='yes'};
          document.onpointerup=()=>document.body.dataset.up='yes';</script>`);
        await page.getByLabel('Probe input').fill('qa-input-ok');
        await page.locator('#click').click();
        if (await page.locator('#input').inputValue() !== 'qa-input-ok' || await page.locator('body').getAttribute('data-clicked') !== 'yes') throw new Error('Click/type probe failed');
        await page.locator('#upload').setInputFiles({name:'probe.csv',mimeType:'text/csv',buffer:Buffer.from('a,b\n1,2\n')});
        if (await page.locator('#upload').evaluate(input=>input.files?.[0]?.name) !== 'probe.csv') throw new Error('Upload probe failed');
        for (const accept of [false, true]) {
          page.once('dialog', dialog => (accept ? dialog.accept() : dialog.dismiss()));
          await page.locator('#confirm').click();
          if (await page.locator('body').getAttribute('data-confirmed') !== String(accept)) throw new Error('Dialog probe failed');
        }
        const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#download').click()]);
        const filename = path.join(handle.directory, `probe-${randomUUID()}.txt`);
        await download.saveAs(filename);
        if (await readFile(filename, 'utf8') !== 'qa-download-ok') throw new Error('Download probe failed');
        const box = await page.locator('#drag').boundingBox();
        await page.mouse.move(box.x + 10, box.y + 10); await page.mouse.down();
        await page.mouse.move(box.x + 100, box.y + 50, { steps: 8 }); await page.mouse.up();
        if (await page.locator('body').getAttribute('data-down') !== 'yes' || await page.locator('body').getAttribute('data-moved') !== 'yes' || await page.locator('body').getAttribute('data-up') !== 'yes') throw new Error('Pointer drag probe failed');
        return { passed: true, click: true, type: true, upload: true, dialogs: true, download: filename, drag: true, screenshot: await this.#screenshot(handle, page, 'probe') };
      } finally { await page.close(); }
    });
    handle.queue = task.catch(() => {});
    return task;
  }

  /** Read-only fixture/prerequisite checks; response bodies stay in memory. */
  async verifyChecks(id, checks = []) {
    const handle = this.#handle(id);
    const task = handle.queue.then(async () => {
      await this.#identity(handle);
      const evidence = [];
      const at = (document, dotPath) => dotPath === '' ? document : String(dotPath).split('.').reduce((value, key) => value?.[key], document);
      for (const check of checks) {
        const url = new URL(this.#url(check.path));
        if (!url.pathname.startsWith('/api/v1/')) throw new Error('Prerequisite checks must use /api/v1/ paths');
        const response = await handle.context.request.get(url.href, { timeout: this.timeoutMs, maxRedirects: 0 });
        const expectedStatus = check.status ?? 200;
        if (response.status() !== expectedStatus) throw new Error(`Prerequisite ${url.pathname} expected HTTP ${expectedStatus}, received ${response.status()}`);
        const assertions = [];
        if (Object.keys(check.equals ?? {}).length || Object.keys(check.minLength ?? {}).length || check.providerAvailable) {
          let document;
          try { document = await response.json(); } catch { throw new Error(`Prerequisite ${url.pathname} did not return JSON`); }
          for (const [field, expected] of Object.entries(check.equals ?? {})) {
            if (expected !== null && typeof expected === 'object') throw new Error('Prerequisite equals values must be scalar');
            if (at(document, field) !== expected) throw new Error(`Prerequisite ${url.pathname} equality assertion failed at ${field}`);
            assertions.push({ field, kind: 'equals', passed: true });
          }
          for (const [field, minimum] of Object.entries(check.minLength ?? {})) {
            if (!Number.isInteger(minimum) || minimum < 0) throw new Error('Prerequisite minLength must be a nonnegative integer');
            const value = at(document, field);
            if (!(Array.isArray(value) || typeof value === 'string') || value.length < minimum) throw new Error(`Prerequisite ${url.pathname} length assertion failed at ${field}`);
            assertions.push({ field, kind: 'minLength', passed: true });
          }
          if (check.providerAvailable) {
            assertProviderAvailable(document, check.providerAvailable);
            assertions.push({ field: check.providerAvailable.providerId, kind: 'providerAvailable', passed: true });
          }
        }
        evidence.push({ path: url.pathname, status: response.status(), assertions, passed: true });
      }
      return evidence;
    });
    handle.queue = task.catch(() => {});
    return task;
  }

  async closeLane(id) {
    const handle = this.lanes.get(id);
    if (!handle) return;
    handle.closing = true;
    try {
      if (handle.context && handle.browser?.isConnected()) {
        try {
          const response = await handle.context.request.post(`${this.baseURL}/api/v1/auth/logout`, { headers: { Origin: this.baseURL }, timeout: this.timeoutMs });
          if (!response.ok()) this.#event(handle, 'logout-failed', { status: response.status() });
        } catch { this.#event(handle, 'logout-failed'); }
      }
      // Remote Browser.close disconnects this client; server.close owns and
      // terminates the real Chromium process, including all its contexts.
      try { await handle.context?.close(); }
      finally {
        try { await handle.browser?.close(); }
        finally { await handle.server.close(); }
      }
    }
    finally { this.lanes.delete(id); this.#credentials.delete(id); }
  }

  async recoverLane(lane, credential) {
    await this.closeLane(lane.id);
    return this.openLane(lane, credential);
  }

  async close() {
    const results = await Promise.allSettled([...this.lanes.keys()].map(id => this.closeLane(id)));
    const failure = results.find(result => result.status === 'rejected');
    if (failure) throw failure.reason;
  }
}
