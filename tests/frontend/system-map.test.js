import assert from "node:assert/strict";
import test from "node:test";

import {
  buildApiJourneySteps,
  buildEntryCatalog,
  buildFocusedJourneyOverview,
  buildJourneyOverview,
  buildJourneyStageSteps,
  buildSystemMapModel,
  buildVisibleFlow,
  entriesForLens,
  journeyStageFlow,
  routeFeatureGroups,
} from "../../src/schemii/schemii/web/assets/system-map.js";

function sourceObject(id, name, kind, qualname = name) {
  return {
    id,
    name,
    qualname,
    module: "schemii.example",
    kind,
    dataShape: null,
    docstring: `${name} source intent.`,
    docstringTruncated: false,
    location: {
      path: "schemii/example.py",
      sourceStartLine: 10,
      definitionLine: 10,
      endLine: 12,
    },
    source: {
      available: true,
      sha256: id.padEnd(64, "0").slice(0, 64),
      text: `def ${name}():\n    pass\n`,
      tokens: [["keyword", "def"], ["plain", ` ${name}():\n    `], ["keyword", "pass"], ["plain", "\n"]],
      truncated: false,
    },
  };
}

const handler = sourceObject("handler", "read_widget", "handler");
const serviceClass = sourceObject("service-class", "WidgetService", "service-implementation");
const serviceRead = sourceObject("service-read", "read", "service", "WidgetService.read");
const repositoryClass = sourceObject("repository-class", "WidgetRepository", "repository-implementation");
const repositoryContract = sourceObject("repository-contract", "WidgetRepositoryContract", "repository-contract");
const repositoryRead = sourceObject("repository-read", "read", "repository", "WidgetRepository.read");
const gatewayClass = sourceObject("gateway-class", "ExampleGateway", "gateway-implementation");
const gatewayContract = sourceObject("gateway-contract", "GatewayContract", "gateway-contract");
const gatewayReadContract = sourceObject("gateway-read-contract", "read", "gateway-contract", "GatewayContract.read");
const gatewayRead = sourceObject("gateway-read", "read", "gateway", "ExampleGateway.read");
const outcome = sourceObject("missing", "WidgetMissing", "outcome");

handler.source.text = "def read_widget(widget_id):\n    return service.read(widget_id)\n";
handler.source.tokens = [["plain", handler.source.text]];
handler.location.sourceStartLine = 10;
handler.location.endLine = 11;
serviceRead.dataShape = null;

const systemDocument = {
  schemaVersion: 1,
  analysis: { callDepthLimit: 10 },
  services: [{
    name: "widgets",
    implementationObjectId: serviceClass.id,
    contractObjectIds: [],
    methodObjectIds: [serviceRead.id],
  }, {
    name: "postgres",
    implementationObjectId: gatewayClass.id,
    contractObjectIds: [gatewayContract.id],
    methodObjectIds: [gatewayRead.id],
  }],
  bindings: [{
    ownerObjectId: serviceClass.id,
    attribute: "_repository",
    path: "services.widgets._repository",
    contractObjectIds: [repositoryContract.id],
    implementationObjectIds: [repositoryClass.id],
  }],
  routes: [{
    id: "get:/widgets/{widget_id}",
    method: "get",
    path: "/widgets/{widget_id}",
    operationId: "read_widget",
    endpointObjectId: handler.id,
    dependencies: [],
    rootObjectIds: [handler.id],
    request: { bodyObjectIds: [], parameters: [{ name: "widget_id", location: "path", required: true }] },
    response: { statusCode: 200, objectIds: [] },
    journey: {
      status: "complete",
      nodes: [{
        key: `root:0:${handler.id}`,
        objectId: handler.id,
        parentKey: null,
        depth: 0,
        stage: "api",
        role: "route-handler",
        provenance: "derived",
        evidence: { kind: "registered-fastapi-handler", resolution: "fastapi-registry", line: 10 },
      }, {
        key: `root:0:${handler.id}>0.001:${serviceRead.id}`,
        objectId: serviceRead.id,
        parentKey: `root:0:${handler.id}`,
        depth: 1,
        stage: "internals",
        role: "application-call",
        provenance: "derived",
        evidence: { kind: "installed-runtime-component", resolution: "runtime-service", line: 11 },
      }, {
        key: `root:0:${handler.id}>0.002:${gatewayRead.id}`,
        objectId: gatewayRead.id,
        parentKey: `root:0:${handler.id}`,
        depth: 1,
        stage: "database",
        role: "database-call",
        provenance: "derived",
        evidence: { kind: "installed-postgres-gateway", resolution: "runtime-service", line: 12 },
      }, {
        key: `root:0:${handler.id}>0.001:${serviceRead.id}>0.001.001:${repositoryRead.id}`,
        objectId: repositoryRead.id,
        parentKey: `root:0:${handler.id}>0.001:${serviceRead.id}`,
        depth: 2,
        stage: "internals",
        role: "application-call",
        provenance: "derived",
        evidence: { kind: "installed-runtime-component", resolution: "runtime-field", line: 15 },
      }, {
        key: `root:0:${handler.id}>0.001:${serviceRead.id}>0.001.002:${outcome.id}`,
        objectId: outcome.id,
        parentKey: `root:0:${handler.id}>0.001:${serviceRead.id}`,
        depth: 2,
        stage: "response",
        role: "error-outcome",
        provenance: "derived",
        evidence: { kind: "raised-source-outcome", resolution: "module", line: 16 },
      }],
      transitions: [{
        fromKey: `root:0:${handler.id}`,
        toKey: `root:0:${handler.id}>0.001:${serviceRead.id}`,
        fromStage: "api",
        toStage: "internals",
        provenance: "derived",
      }, {
        fromKey: `root:0:${handler.id}`,
        toKey: `root:0:${handler.id}>0.002:${gatewayRead.id}`,
        fromStage: "api",
        toStage: "database",
        provenance: "derived",
      }, {
        fromKey: `root:0:${handler.id}>0.001:${serviceRead.id}`,
        toKey: `root:0:${handler.id}>0.001:${serviceRead.id}>0.001.002:${outcome.id}`,
        fromStage: "internals",
        toStage: "response",
        provenance: "derived",
      }],
      issues: [],
    },
    implementationDigest: "a".repeat(64),
  }],
  callables: [{
    objectId: handler.id,
    signature: {
      available: true,
      parameters: [{
        name: "widget_id",
        kind: "positional_or_keyword",
        annotation: "str",
        required: true,
        objectIds: [],
      }],
      returnAnnotation: "Widget",
      returnObjectIds: [],
    },
    calls: [{
      sequence: 1,
      expression: "service.read",
      objectId: serviceRead.id,
      resolution: "runtime-service",
      line: 11,
      contexts: [{ kind: "return", label: "returned result", line: 11 }],
      arguments: [{
        parameter: "widget_id",
        annotation: "str",
        expression: "widget_id",
        kind: "positional",
      }],
      targetSignature: {
        available: true,
        parameters: [{
          name: "widget_id",
          kind: "positional_or_keyword",
          annotation: "str",
          required: true,
          objectIds: [],
        }],
        returnAnnotation: "Widget",
        returnObjectIds: [],
      },
    }, {
      sequence: 2,
      expression: "postgres.read",
      objectId: gatewayRead.id,
      resolution: "runtime-service",
      line: 12,
      contexts: [],
    }],
    truncated: { calls: false },
  }, {
    objectId: serviceRead.id,
    signature: {
      available: true,
      parameters: [{
        name: "widget_id",
        kind: "positional_or_keyword",
        annotation: "str",
        required: true,
        objectIds: [],
      }],
      returnAnnotation: "Widget",
      returnObjectIds: [],
    },
    calls: [{
      sequence: 1,
      expression: "self._repository.read",
      objectId: repositoryRead.id,
      resolution: "runtime-field",
      line: 15,
      contexts: [],
    }, {
      sequence: 2,
      expression: "WidgetMissing",
      objectId: outcome.id,
      resolution: "module",
      line: 16,
      contexts: [{ kind: "raise", label: "raised outcome", line: 16 }],
      outcome: true,
      statusCode: 404,
      code: "widget_missing",
    }],
    truncated: { calls: false },
  }, {
    objectId: repositoryRead.id,
    calls: [],
    truncated: { calls: false },
  }, {
    objectId: gatewayRead.id,
    calls: [],
    truncated: { calls: false },
  }],
  objects: [
    handler, serviceClass, serviceRead, repositoryClass, repositoryContract,
    repositoryRead, gatewayClass, gatewayContract, gatewayReadContract,
    gatewayRead, outcome,
  ],
};

const routeDocument = {
  schemaVersion: 1,
  objects: [handler, serviceRead, gatewayRead],
  routes: [{
    id: "get:/widgets/{widget_id}",
    method: "get",
    path: "/widgets/{widget_id}",
    operationId: "read_widget",
    endpointId: handler.id,
    dependencies: [],
    calls: [
      { objectId: serviceRead.id, expression: "service.read", line: 11 },
      { objectId: gatewayRead.id, expression: "postgres.read", line: 12 },
    ],
    requestObjectIds: [],
    responseObjectIds: [],
  }],
};

const databaseDocument = {
  schemaVersion: 1,
  analysis: {},
  gateway: {
    serviceName: "postgres",
    contractObjectId: gatewayContract.id,
    implementationObjectId: gatewayClass.id,
  },
  operations: [{
    id: "read",
    name: "read",
    contractObjectId: gatewayReadContract.id,
    implementationObjectId: gatewayRead.id,
    parameters: [],
    returnAnnotation: "Widget",
    implementationDigest: "b".repeat(64),
  }],
  callables: [{
    objectId: gatewayRead.id,
    depth: 0,
    calls: [],
    queryIds: ["sql:widget"],
    inlineStatements: [],
    truncated: { calls: false },
  }],
  queries: [{
    id: "sql:widget",
    name: "WIDGET_QUERY",
    marker: "schemii_widget",
    statement: "SELECT",
    placeholderCount: 1,
    resultColumns: ["oid", "relname"],
    catalogObjects: ["pg_catalog.pg_class"],
    location: { path: "schemii/queries.py", definitionLine: 4, endLine: 6 },
    sha256: "c".repeat(64),
    sql: "SELECT * FROM pg_catalog.pg_class WHERE oid = %s",
    truncated: false,
  }],
  objects: [gatewayClass, gatewayContract, gatewayReadContract, gatewayRead],
};

const openapiDocument = {
  openapi: "3.1.0",
  info: { title: "Example", version: "1" },
  paths: {
    "/widgets/{widget_id}": {
      get: {
        operationId: "read_widget",
        summary: "Read one widget",
        "x-schemii-status": "planned",
        responses: {
          200: {
            description: "Widget",
            content: { "application/json": { schema: { $ref: "#/components/schemas/Widget" } } },
          },
          404: {
            description: "Widget missing",
            content: { "application/json": { schema: { $ref: "#/components/schemas/ApiErrorResponse" } } },
          },
        },
      },
    },
  },
};

test("system map builds request, API, internal, and database browse catalogs", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);

  assert.deepEqual(entriesForLens(model, "e2e").map(entry => entry.id), ["get:/widgets/{widget_id}"]);
  assert.deepEqual(entriesForLens(model, "database").map(entry => entry.id), ["read"]);
  assert.deepEqual(
    entriesForLens(model, "internals").map(entry => entry.title),
    ["ExampleGateway", "WidgetRepository", "WidgetService"],
  );
  assert.equal(model.routes[0].contract.summary, "Read one widget");
  assert.equal(entriesForLens(model, "api")[0].lifecycle, "planned");
});

test("entry catalog derives groups from source ownership and searches route evidence", () => {
  const groupedSystem = structuredClone(systemDocument);
  groupedSystem.objects.find(object => object.id === handler.id).module = "schemii.common.connections.routes";
  const model = buildSystemMapModel(groupedSystem, routeDocument, databaseDocument, openapiDocument);
  const entries = entriesForLens(model, "e2e");

  assert.equal(entries[0].groupLabel, "Common");
  assert.equal(entries[0].featureLabel, "Connections");
  const catalog = buildEntryCatalog(entries, { includeEmptyRouteOwners: true });
  assert.deepEqual(
    catalog.availableGroups.map(group => [group.label, group.entries.length]),
    [["Common", 1], ["Schemii", 0], ["Schemoo", 0], ["Schemer", 0]],
  );
  assert.equal(catalog.totalCount, 1);
  assert.equal(catalog.visibleCount, 1);
  assert.equal(buildEntryCatalog(entries, { query: "widget_id" }).visibleCount, 1);
  assert.equal(buildEntryCatalog(entries, { query: "connections" }).visibleCount, 1);
  assert.equal(buildEntryCatalog(entries, { query: "unrelated" }).visibleCount, 0);
  assert.equal(buildEntryCatalog(entries, { groupId: "owner:common" }).visibleCount, 1);
  assert.equal(buildEntryCatalog(entries, { groupId: "owner:schemoo" }).visibleCount, 0);
});

test("route ownership follows each endpoint's source package", () => {
  const cases = [
    ["schemii.common.connections.routes", "Common", "Connections"],
    ["schemii.schemii.workspaces.routes", "Schemii", "Workspaces"],
    ["schemii.schemoo.semantic.routes", "Schemoo", "Semantic"],
    ["schemii.schemer.generation.routes", "Schemer", "Generation"],
  ];

  for (const [module, owner, feature] of cases) {
    const groupedSystem = structuredClone(systemDocument);
    groupedSystem.objects.find(object => object.id === handler.id).module = module;
    const model = buildSystemMapModel(groupedSystem, routeDocument, databaseDocument, openapiDocument);
    const [entry] = entriesForLens(model, "e2e");
    assert.equal(entry.groupLabel, owner);
    assert.equal(entry.featureLabel, feature);
  }
});

test("route features prefer source-owned child tags over the product router tag", () => {
  const cases = [
    ["schemii-assistant-planned", "Assistant"],
    ["schemii-schema-design-planned", "Schema design"],
    ["schemii-database-browser-planned", "Database browser"],
    ["schemii-sql-console-planned", "SQL console"],
  ];

  for (const [tag, feature] of cases) {
    const groupedSystem = structuredClone(systemDocument);
    groupedSystem.objects.find(object => object.id === handler.id).module = "schemii.schemii.routes";
    const taggedOpenapi = structuredClone(openapiDocument);
    taggedOpenapi.paths["/widgets/{widget_id}"].get.tags = ["schemii", tag];
    const model = buildSystemMapModel(groupedSystem, routeDocument, databaseDocument, taggedOpenapi);
    const [entry] = entriesForLens(model, "e2e");

    assert.equal(entry.groupLabel, "Schemii");
    assert.equal(entry.featureLabel, feature);
  }
});

test("route catalogs preserve source registration order for feature subsections", () => {
  const entries = [
    { id: "design", type: "route", featureLabel: "Designs" },
    { id: "connection-create", type: "route", featureLabel: "Connections" },
    { id: "connection-list", type: "route", featureLabel: "Connections" },
  ];

  assert.deepEqual(routeFeatureGroups(entries), [{
    id: "designs",
    label: "Designs",
    entries: [entries[0]],
  }, {
    id: "connections",
    label: "Connections",
    entries: [entries[1], entries[2]],
  }]);
});

test("standalone API, internal, and database entries use focused journey drill-downs", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);

  const apiEntry = entriesForLens(model, "api")[0];
  const api = buildFocusedJourneyOverview(model, apiEntry, "api");
  assert.equal(api.scope, "focused");
  assert.deepEqual(api.stages.map(stage => stage.id), ["api"]);
  assert.match(api.guide.title, /Read one widget/);
  assert.ok(buildJourneyStageSteps(model, apiEntry, api, "api").every(step => !step.nextStage));

  const internalEntry = entriesForLens(model, "internals")
    .find(entry => entry.title === "WidgetService");
  const internals = buildFocusedJourneyOverview(model, internalEntry, "internals");
  assert.deepEqual(internals.stages.map(stage => stage.id), ["internals"]);
  assert.equal(internals.stages[0].summary, "1 public method · read");
  const internalSteps = buildJourneyStageSteps(model, internalEntry, internals, "internals");
  assert.deepEqual(internalSteps.map(step => step.title), [
    "Read in Widget Service",
    "Read in Widget Repository",
    "Return 404 · Widget missing",
  ]);
  assert.ok(internalSteps.every(step => step.dataFlow));
  assert.ok(internalSteps.every(step => !step.nextStage));

  const databaseEntry = entriesForLens(model, "database")[0];
  const database = buildFocusedJourneyOverview(model, databaseEntry, "database");
  assert.deepEqual(database.stages.map(stage => stage.id), ["database"]);
  assert.match(database.stages[0].summary, /GatewayContract\.read → ExampleGateway\.read/);
  const databaseSteps = buildJourneyStageSteps(model, databaseEntry, database, "database");
  assert.deepEqual(databaseSteps.map(step => step.title), [
    "Read through Example Gateway",
    "Run Widget query",
  ]);
  assert.equal(databaseSteps[1].dataFlow.outputObjects[0].dataShape.fields[0].name, "oid");
  assert.ok(databaseSteps.every(step => !step.nextStage));
});

test("end-to-end flow follows recursive runtime calls and attaches reachable SQL", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);
  const entry = entriesForLens(model, "e2e")[0];
  const flow = buildVisibleFlow(model, { entry, lens: "e2e", depth: 3 });

  assert.deepEqual(
    new Set(flow.nodes.map(node => node.query?.name || node.object?.qualname)),
    new Set(["read_widget", "WidgetService.read", "WidgetRepository.read", "ExampleGateway.read", "WIDGET_QUERY"]),
  );
  assert.equal(flow.sqlCount, 1);
  assert.ok(flow.edgeCount >= 4);
  assert.ok(!flow.nodes.some(node => node.object?.name === "WidgetMissing"));

  const withOutcomes = buildVisibleFlow(model, { entry, lens: "e2e", depth: 3, showOutcomes: true });
  assert.ok(withOutcomes.nodes.some(node => node.object?.name === "WidgetMissing"));
});

test("journey overview groups source calls into understandable ownership boundaries", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);
  const entry = entriesForLens(model, "e2e")[0];
  const overview = buildJourneyOverview(model, entry);

  assert.deepEqual(overview.stages.map(stage => stage.id), ["api", "internals", "database", "response"]);
  assert.deepEqual(
    overview.stages.find(stage => stage.id === "api").nodes.map(node => node.object?.qualname),
    ["read_widget"],
  );
  assert.deepEqual(
    new Set(overview.stages.find(stage => stage.id === "internals").nodes.map(node => node.object?.qualname)),
    new Set(["WidgetService.read", "WidgetRepository.read"]),
  );
  assert.ok(overview.stages.find(stage => stage.id === "database").nodes.some(node => node.query?.name === "WIDGET_QUERY"));
  assert.deepEqual(overview.responses.success.map(response => response.status), ["200"]);
  assert.deepEqual(overview.responses.sourceErrors.map(outcome => outcome.label), ["404 · widget_missing"]);

  const internalFlow = journeyStageFlow(overview, "internals");
  assert.equal(internalFlow.nodes[0].depth, 0);
  assert.ok(internalFlow.nodes.every(node => !node.query));
  assert.ok(!internalFlow.nodes.some(node => node.object?.qualname === "ExampleGateway.read"));
});

test("every non-API journey area uses narrative data-flow drill-downs", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);
  const entry = entriesForLens(model, "e2e")[0];
  const overview = buildJourneyOverview(model, entry);

  const internals = buildJourneyStageSteps(model, entry, overview, "internals");
  assert.deepEqual(internals.map(step => step.title), [
    "Read in Widget Service",
    "Read in Widget Repository",
  ]);
  assert.deepEqual(internals.map(step => step.kind), ["application", "repository"]);
  assert.equal(internals[0].dataFlow.argumentsPassed[0].expression, "widget_id");
  assert.match(internals[0].explanation, /read_widget calls WidgetService\.read/);
  assert.match(internals[0].explanation, /widget_id ← widget_id/);
  assert.match(internals[0].explanation, /schemii\/example\.py:11/);
  assert.equal(internals.at(-1).nextStage, "database");

  const database = buildJourneyStageSteps(model, entry, overview, "database");
  assert.deepEqual(database.map(step => step.title), [
    "Read through Example Gateway",
    "Run Widget query",
  ]);
  assert.equal(database[1].kind, "query");
  assert.deepEqual(
    database[1].dataFlow.outputObjects[0].dataShape.fields.map(field => field.name),
    ["oid", "relname"],
  );
  assert.match(database[1].explanation, /WIDGET_QUERY at schemii\/queries\.py:4/);
  assert.match(database[1].explanation, /pg_catalog\.pg_class/);
  assert.match(database[1].explanation, /1 bound parameter → oid · relname/);
  assert.equal(database.at(-1).nextStage, "response");

  const response = buildJourneyStageSteps(model, entry, overview, "response");
  assert.deepEqual(response.map(step => step.title), [
    "Return HTTP 200",
    "Return HTTP 404 · Widget missing",
  ]);
  assert.deepEqual(response.map(step => step.kind), ["success", "outcome"]);
  assert.ok(response.every(step => step.dataFlow));
  assert.ok(response[0].dataFlow.outputContract);
  assert.ok(response[1].dataFlow.outputContract);
  assert.match(response[0].explanation, /read_widget returns Widget/);
  assert.match(response[0].explanation, /GET \/widgets\/\{widget_id\} declares HTTP 200/);
  assert.match(response[1].explanation, /WidgetService\.read raises WidgetMissing/);
});

test("API journey tells the request story instead of presenting a misleading raw call tree", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);
  const entry = entriesForLens(model, "e2e")[0];
  const principalDependency = sourceObject("principal-dependency", "get_current_principal", "dependency");
  const principal = sourceObject("principal-model", "Principal", "model");
  const request = sourceObject("widget-request", "WidgetRequest", "model");
  model.objects.set(principalDependency.id, principalDependency);
  model.objects.set(principal.id, principal);
  model.objects.set(request.id, request);
  entry.route.dependencies = [{ parameterName: "principal", object: principalDependency, useCache: true, resultObjects: [principal] }];
  entry.route.request.bodyObjects = [request];
  entry.route.contract.request.schemas = ["WidgetRequest"];
  entry.route.contract.story.requestObjects = [request];
  const overview = buildJourneyOverview(model, entry);
  const steps = buildApiJourneySteps(model, entry, overview);

  assert.deepEqual(steps.map(step => step.title), [
    "Resolve Principal",
    "Validate the request body",
    "Run the route handler",
    "Hand off to application logic",
  ]);
  assert.equal(steps[1].summary, "application/json → WidgetRequest");
  assert.match(steps[1].explanation, /GET \/widgets\/\{widget_id\}: application\/json → WidgetRequest/);
  assert.match(steps[2].explanation, /GET \/widgets\/\{widget_id\} → read_widget at schemii\/example\.py:10/);
  assert.match(steps[3].explanation, /read_widget calls WidgetService\.read/);
  assert.equal(steps[3].nextStage, "internals");
  assert.deepEqual(steps[3].dataFlow.argumentsPassed, [{
    parameter: "widget_id",
    annotation: "str",
    expression: "widget_id",
    kind: "positional",
  }]);
  assert.equal(steps[3].dataFlow.signature.returnAnnotation, "Widget");
  assert.ok(!steps.some(step => step.title === "Principal" || step.title === "Service"));
});

test("journey integrity gaps are explicit instead of being reclassified by frontend names", () => {
  const missingClassification = structuredClone(systemDocument);
  missingClassification.routes[0].journey.nodes = missingClassification.routes[0].journey.nodes.filter(
    node => node.objectId !== repositoryRead.id,
  );
  const model = buildSystemMapModel(missingClassification, routeDocument, databaseDocument, openapiDocument);
  const overview = buildJourneyOverview(model, entriesForLens(model, "e2e")[0]);

  assert.ok(overview.issues.some(issue => issue.kind === "missing-journey-classification"));
  assert.ok(overview.stages.find(stage => stage.id === "internals").nodes.some(
    node => node.object?.qualname === "WidgetRepository.read",
  ));
});

test("API lens stays shallow and search retains the path to a matching descendant", () => {
  const model = buildSystemMapModel(systemDocument, routeDocument, databaseDocument, openapiDocument);
  const entry = entriesForLens(model, "api")[0];
  const apiFlow = buildVisibleFlow(model, { entry, lens: "api", depth: "all" });

  assert.ok(apiFlow.nodes.some(node => node.object?.qualname === "WidgetService.read"));
  assert.ok(!apiFlow.nodes.some(node => node.object?.qualname === "WidgetRepository.read"));
  assert.equal(apiFlow.sqlCount, 0);

  const filtered = buildVisibleFlow(model, { entry, lens: "e2e", depth: 3, query: "pg_catalog.pg_class" });
  assert.ok(filtered.nodes.some(node => node.object?.name === "read_widget"));
  assert.ok(filtered.nodes.some(node => node.query?.name === "WIDGET_QUERY"));
});
