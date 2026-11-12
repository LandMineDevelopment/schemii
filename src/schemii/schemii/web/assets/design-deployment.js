import { element, errorPanel, replace } from '#common/dom.js';

export function detachedDesignEligible(workspace, design) {
  return Boolean(workspace && design && !workspace.connectionId && workspace.id === design.workspaceId);
}

/** A metadata-copy review. Migration authority stays in the existing controller. */
export function createDesignDeploymentController({ api, elements, getContext, openTarget, notify }) {
  let version = 0, controller, sourceId, target, preview, resolutions = {}, busy = false;
  const current = token => token === version && elements.dialog.open && getContext()?.workspace?.id === sourceId;
  function invalidate() { version++; controller?.abort(); controller = null; }
  function actions() {
    elements.review.disabled = busy || !elements.namespace.value || !elements.connection.value;
    elements.copy.disabled = busy || !preview?.ready || preview.summary.changeCount === 0;
    elements.connection.disabled = busy; elements.namespace.disabled = busy;
  }
  function status(message) { replace(elements.status, element('p', { text: message, attrs: { role: 'status' } })); }
  function retryStatus(error, label, callback) {
    const retry = element('button', { type: 'button', className: 'ui-button compact', text: label });
    retry.onclick = () => { void callback(); };
    replace(elements.status, errorPanel(error), retry);
  }
  function resetReview() { preview = null; target = null; resolutions = {}; replace(elements.body); actions(); }
  function close() { invalidate(); preview = null; busy = false; }
  async function namespaces() {
    invalidate(); resetReview(); const token = version; controller = new AbortController();
    replace(elements.namespace); busy = true; actions(); status('Loading visible namespaces…');
    try {
      const result = await api.listConnectionNamespaces(elements.connection.value, { signal: controller.signal });
      if (!current(token)) return;
      elements.namespace.append(element('option', { text: 'Choose namespace', attrs: { value: '' } }));
      for (const entry of result.namespaces) {
        const name = typeof entry === 'string' ? entry : entry.name;
        elements.namespace.append(element('option', { text: name, attrs: { value: name } }));
      }
      status(result.namespaces.length ? 'Choose a namespace, then review the copy.' : 'This connection has no visible namespaces. Check its PostgreSQL permissions or choose another connection.');
    } catch (error) { if (current(token)) retryStatus(error, 'Retry namespaces', namespaces); }
    finally { if (current(token)) { busy = false; actions(); } }
  }
  function render() {
    replace(elements.body);
    if (!preview) return;
    elements.body.append(element('h3', { text: preview.targetLabel }),
      element('p', { text: `Source revision ${preview.sourceRevision} → target design revision ${preview.targetRevision}. Target-only objects and edits are retained.` }),
      element('p', { text: preview.summary.title }));
    if (preview.targetChanges) {
      const existing = element('section', {}, [element('h4', { text: 'Existing target edits' }), element('p', { text: preview.targetChanges.title })]);
      const list = element('ul');
      for (const change of preview.targetChanges.changes) list.append(element('li', { text: `${change.operation} ${change.kind}: ${change.name}` }));
      existing.append(list); elements.body.append(existing);
    }
    if (preview.liveDrift) elements.body.append(element('p', { className: 'hint', text: 'PostgreSQL has changed since this target’s synchronization baseline. The fresh migration review will reconcile live drift before Apply is available.' }));
    const changes = element('ul');
    for (const change of preview.summary.changes) changes.append(element('li', { text: `${change.operation} ${change.kind}: ${change.name}` }));
    elements.body.append(changes);
    for (const warning of preview.warnings) elements.body.append(element('p', { className: 'hint', text: warning }));
    for (const collision of preview.collisions) {
      const choice = element('select', { attrs: { 'aria-label': `Resolve ${collision.label}` } });
      for (const [value, text] of [['', 'Choose a definition'], ['keep', 'Keep target'], ['incoming', 'Use detached design']]) choice.append(element('option', { text, attrs: { value } }));
      choice.value = resolutions[collision.key] || '';
      choice.onchange = () => {
        if (choice.value) resolutions[collision.key] = choice.value; else delete resolutions[collision.key];
        preview = null; elements.copy.disabled = true;
        status('Choices changed. Review the copy again before saving.');
      };
      const comparison = element('div', { className: 'deployment-comparison' }, [
        element('div', {}, [element('h4', { text: 'Target definition' }), element('pre', { text: collision.target.definition || JSON.stringify(collision.target, null, 2) })]),
        element('div', {}, [element('h4', { text: 'Detached definition' }), element('pre', { text: collision.incoming.definition || JSON.stringify(collision.incoming, null, 2) })]),
      ]);
      elements.body.append(element('section', { className: 'deployment-collision' }, [element('h4', { text: collision.label }), choice, comparison]));
    }
    status(preview.ready ? (preview.summary.changeCount ? 'Ready to save one undoable target design edit. PostgreSQL is unchanged until you review and apply a migration.' : 'The target already contains these definitions. Nothing needs copying.') : 'Choose how to resolve each collision, then review the copy again.');
    actions();
  }
  async function review() {
    if (busy || !elements.connection.value || !elements.namespace.value) return;
    invalidate(); const token = version; controller = new AbortController(); busy = true; preview = null; actions();
    status('Opening the exact target and reviewing saved definitions…');
    try {
      target = (await api.openPostgresWorkspace({ connectionId: elements.connection.value, namespace: elements.namespace.value }, { signal: controller.signal })).workspace;
      if (!current(token)) return;
      preview = await api.reviewDesignDeployment(sourceId, { targetWorkspaceId: target.id, resolutions }, { signal: controller.signal });
      if (!current(token)) return;
      render();
    } catch (error) { if (current(token)) { preview = null; replace(elements.status, errorPanel(error)); } }
    finally { if (current(token)) { busy = false; actions(); } }
  }
  async function copy() {
    if (busy || !preview?.ready || !preview.summary.changeCount) return;
    const reviewed = preview, destination = target;
    invalidate(); const token = version; controller = new AbortController(); busy = true; actions();
    status('Saving the reviewed target design…');
    try {
      await api.copyDesignDeployment(sourceId, { targetWorkspaceId: destination.id, resolutions, reviewDigest: reviewed.reviewDigest }, { signal: controller.signal });
      if (!current(token)) return;
      elements.dialog.close(); close();
      notify('Copied into the target design. Review its fresh migration before applying PostgreSQL changes.');
      await openTarget(destination);
    } catch (error) {
      if (!current(token)) return;
      preview = null; replace(elements.status, errorPanel(error));
      elements.status.append(element('p', { text: 'Both workspaces remain available. Review again to check whether the copy saved before retrying.' }));
    } finally { if (current(token)) { busy = false; actions(); } }
  }
  async function open() {
    const value = getContext(); if (!detachedDesignEligible(value?.workspace, value?.design)) return false;
    invalidate(); sourceId = value.workspace.id; resetReview();
    replace(elements.connection); replace(elements.namespace); elements.dialog.showModal();
    const token = version; controller = new AbortController(); busy = true; actions(); status('Loading available PostgreSQL connections…');
    try {
      const connections = await api.listConnections({ signal: controller.signal });
      if (!current(token)) return false;
      replace(elements.connection, element('option', { text: 'Choose connection', attrs: { value: '' } })); replace(elements.namespace);
      for (const connection of connections) elements.connection.append(element('option', { text: `${connection.name} · ${connection.database} · ${connection.username}`, attrs: { value: connection.id } }));
      status(connections.length ? 'Choose a connection and namespace. The source design stays unchanged; existing target edits are reviewed before copying.' : 'No PostgreSQL connections are available. Add a connection, then reopen this review. SQL export remains available.');
    } catch (error) { if (current(token)) retryStatus(error, 'Retry connections', open); }
    finally { if (current(token)) { busy = false; actions(); } }
    return true;
  }
  elements.connection.addEventListener('change', () => { if (elements.connection.value) void namespaces(); else { invalidate(); resetReview(); } });
  elements.namespace.addEventListener('change', () => { invalidate(); resetReview(); });
  elements.review.addEventListener('click', review); elements.copy.addEventListener('click', copy);
  elements.dialog.addEventListener('close', close);
  return { open, close: () => { close(); if (elements.dialog.open) elements.dialog.close(); }, review, copy };
}
