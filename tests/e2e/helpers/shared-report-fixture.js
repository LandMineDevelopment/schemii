import { randomUUID } from 'node:crypto';
import { expect } from '@playwright/test';

export async function checked(response, status = 200) {
  expect(response.status(), `${new URL(response.url()).pathname}: ${await response.text()}`).toBe(status);
  return status === 204 ? null : response.json();
}

// Only metadata created by this fixture is changed. The launcher owns accounts_demo
// and its deliberately public reporting credentials; never reset or alter its rows.
export async function sharedReportFixture(request) {
  const suffix = randomUUID().replaceAll('-', '');
  const users = [], roles = [], profiles = [];
  let source, model, dashboard;
  const cleanup = async () => {
    const errors = [];
    const run = async operation => { try { await operation(); } catch (error) { errors.push(error); } };
    for (const user of users) await run(async () => checked(await request.patch(`/api/v1/admin/accounts/${user.id}`, { data: { disabled: true } })));
    for (const role of roles) await run(async () => {
      const response = await request.delete(`/api/v1/admin/roles/${role.id}`);
      if (response.status() !== 404) await checked(response, 204);
    });
    if (dashboard) await run(async () => {
      const current = await checked(await request.get(`/api/v1/schemer/dashboards/${dashboard.id}`));
      await checked(await request.delete(`/api/v1/schemer/dashboards/${dashboard.id}?expectedRevision=${current.revision}`), 204);
    });
    if (model) await run(async () => checked(await request.delete(`/api/v1/schemoo/models/${model.id}?expected_revision=${model.revision}`), 204));
    for (const profile of profiles) await run(async () => checked(await request.delete(`/api/v1/admin/schemii-connections/${profile.id}?expectedRevision=${profile.revision}`), 204));
    if (source) await run(async () => checked(await request.delete(`/api/v1/connections/${source.id}?expectedRevision=${source.revision}`), 204));
    if (errors.length) throw new AggregateError(errors, 'Shared report fixture cleanup failed');
  };
  try {
    const database = process.env.SCHEMII_E2E_REPORT_DATABASE || 'schemii_test';
    const connection = region => ({ name: `QA report ${region} ${suffix}`, host: 'demo-postgres', port: 5432,
      database, username: `report_${region}`, password: `schemii-demo-${region}-only`, sslMode: 'disable' });
    source = await checked(await request.post('/api/v1/connections', { data: connection('east') }), 201);
    for (const region of ['east', 'west']) profiles.push(await checked(await request.post('/api/v1/admin/schemii-connections', { data: connection(region) }), 201));
    const field = (column, aggregate = 'none') => ({ table: 'sales', column, aggregate });
    model = await checked(await request.post('/api/v1/schemoo/models', { data: {
      name: `QA report model ${suffix}`, connectionId: source.id, database, namespace: 'accounts_demo',
      definition: { root: 'sales', nodes: [{ id: 'sales', table: 'sales', label: 'Sales' }], scopes: [{
        id: 'minimum', label: 'Minimum sale', kind: 'required', alternatives: [{ id: 'at_least', label: 'At least',
          inputs: [{ id: 'amount', label: 'Minimum amount', type: 'integer' }],
          conditions: [{ table: 'sales', column: 'amount', operator: 'gte', parameterId: 'amount' }],
        }],
      }] },
    } }), 201);
    const dashboardBody = { name: `QA shared sales ${suffix}`, modelId: model.id, modelRevision: model.revision,
      selections: { minimum: { alternativeId: 'at_least', values: { amount: 0 } } }, optionalFilters: [], tiles: [{
        id: 'sales', title: 'Sales by region', kind: 'bar', dimensions: [field('region')], measures: [field('amount', 'sum')],
        detailFields: [field('id'), field('region'), field('amount')], limit: 100,
      }] };
    dashboard = await checked(await request.post('/api/v1/schemer/dashboards', { data: dashboardBody }), 201);
    for (const [index, region] of ['east', 'west'].entries()) {
      const credentials = { username: `qa_${region}_${suffix}`, password: `QA-${randomUUID()}` };
      const user = await checked(await request.post('/api/v1/admin/accounts', { data: { ...credentials, display_name: `QA ${region} viewer`, is_admin: false } }), 201);
      users.push({ ...user, ...credentials });
      const profile = profiles[index];
      const body = { name: `QA ${region} report ${suffix}`, capabilities: ['schemer:access'], user_ids: [user.id],
        connections: [{ connection_id: profile.id, owner_id: profile.ownerId, allow_authoring: false }],
        dashboards: [{ dashboard_id: dashboard.id, owner_id: dashboard.ownerId,
          connection_id: profile.id, connection_owner_id: profile.ownerId, can_export: false, can_drill: false }],
      };
      roles.push({ ...await checked(await request.post('/api/v1/admin/roles', { data: body }), 201), body });
    }
    return { users, roles, source, model, dashboard, dashboardBody, cleanup };
  } catch (error) {
    try { await cleanup(); } catch (cleanupError) { throw new AggregateError([error, cleanupError], 'Shared report fixture setup and cleanup failed'); }
    throw error;
  }
}

export async function streamedRows(request, path, data) {
  const response = await request.post(path, { data });
  expect(response.status(), await response.text()).toBe(200);
  const frames = (await response.text()).trim().split('\n').map(line => JSON.parse(line));
  expect(frames.filter(frame => frame.type === 'error' || frame.type === 'tile-error')).toEqual([]);
  expect(frames.some(frame => frame.type === 'complete')).toBeTruthy();
  return frames.filter(frame => frame.type === 'rows').flatMap(frame => frame.rows);
}
