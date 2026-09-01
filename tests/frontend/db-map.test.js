import assert from "node:assert/strict";
import test from "node:test";

import { buildDbGraphModel } from "../../src/schemii/schemii/web/assets/db-graph.js";
import { buildDbMapModel } from "../../src/schemii/schemii/web/assets/db-map.js";

function sourceObject(id, name, kind = "helper") {
  return {
    id,
    name,
    qualname: `ExampleGateway.${name}`,
    module: "schemii.example",
    kind,
    docstring: "Derived intent.",
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

const contractClass = sourceObject("contract-class", "ExampleGateway", "gateway-contract");
const implementationClass = sourceObject("implementation-class", "LiveGateway", "gateway-implementation");
const contractMethod = sourceObject("contract-read", "read", "gateway-contract");
const implementationMethod = sourceObject("implementation-read", "read", "gateway-operation");
const execute = sourceObject("helper-execute", "_execute", "helper");

const databaseDocument = {
  schemaVersion: 1,
  analysis: { kind: "bounded-python-source" },
  gateway: {
    serviceName: "postgres",
    contractObjectId: contractClass.id,
    implementationObjectId: implementationClass.id,
  },
  operations: [{
    id: "read",
    name: "read",
    contractObjectId: contractMethod.id,
    implementationObjectId: implementationMethod.id,
    parameters: [{ name: "namespace", kind: "positional_or_keyword", annotation: "str", required: true }],
    returnAnnotation: "Catalog",
    implementationDigest: "a".repeat(64),
  }],
  callables: [{
    objectId: implementationMethod.id,
    depth: 0,
    calls: [{
      sequence: 1,
      expression: "self._execute",
      objectId: execute.id,
      resolution: "runtime-binding",
      line: 11,
      queryIds: ["sql:catalog"],
    }],
    queryIds: ["sql:catalog"],
    inlineStatements: [],
    truncated: { calls: false },
  }, {
    objectId: execute.id,
    depth: 1,
    calls: [],
    queryIds: [],
    inlineStatements: [],
    truncated: { calls: false },
  }],
  queries: [{
    id: "sql:catalog",
    name: "CATALOG_QUERY",
    marker: "schemii_catalog",
    statement: "SELECT",
    placeholderCount: 1,
    resultColumns: ["oid", "relname"],
    catalogObjects: ["pg_catalog.pg_class"],
    location: { path: "schemii/queries.py", definitionLine: 4, endLine: 8 },
    sha256: "b".repeat(64),
    sql: "SELECT * FROM pg_catalog.pg_class WHERE relnamespace = %s",
    truncated: false,
  }],
  objects: [contractClass, implementationClass, contractMethod, implementationMethod, execute],
};

const routeDocument = {
  schemaVersion: 1,
  objects: [sourceObject("route-handler", "get_catalog", "handler"), implementationMethod],
  routes: [{
    id: "get:/catalog",
    method: "get",
    path: "/catalog",
    operationId: "get_catalog",
    endpointId: "route-handler",
    calls: [{ objectId: implementationMethod.id, expression: "services.postgres.read", line: 20 }],
  }],
};

test("DB map joins runtime gateway operations to API callers and reachable SQL", () => {
  const model = buildDbMapModel(databaseDocument, routeDocument);

  assert.equal(model.serviceName, "postgres");
  assert.equal(model.contract.name, "ExampleGateway");
  assert.equal(model.implementation.name, "LiveGateway");
  assert.equal(model.operations.length, 1);
  const operation = model.operations[0];
  assert.equal(operation.name, "read");
  assert.equal(operation.returnAnnotation, "Catalog");
  assert.deepEqual(operation.parameters.map(parameter => parameter.name), ["namespace"]);
  assert.deepEqual(operation.queries.map(query => query.name), ["CATALOG_QUERY"]);
  assert.deepEqual(operation.queries[0].resultColumns, ["oid", "relname"]);
  assert.deepEqual(operation.callers.map(route => route.id), ["get:/catalog"]);
  assert.match(operation.searchText, /pg_catalog\.pg_class/);
});

test("DB graph derives callable, SQL, and catalog relationships from the inspection model", () => {
  const model = buildDbMapModel(databaseDocument, routeDocument);
  const graph = buildDbGraphModel(model);

  assert.deepEqual(
    new Set(graph.nodes.map(node => node.kind)),
    new Set(["operation", "callable", "query", "catalog"]),
  );
  assert.deepEqual(
    new Set(graph.edges.map(edge => edge.kind)),
    new Set(["call", "query", "catalog"]),
  );
  const reachable = graph.operationReachable.get("read");
  assert.equal(reachable.size, 4);
});

test("DB map rejects non-versioned inspection data instead of guessing", () => {
  assert.throws(
    () => buildDbMapModel({ schemaVersion: 2 }),
    /unsupported schema version/,
  );
});
