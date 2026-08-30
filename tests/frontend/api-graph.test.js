import assert from "node:assert/strict";
import test from "node:test";

import {
  buildApiGraphModel,
  reconcileApiGraphPositions,
} from "../../src/schemii/schemii/web/assets/api-graph.js";
import { buildApiMapModel } from "../../src/schemii/schemii/web/assets/api-map.js";

test("API graph connects operations to request, response, and nested schemas", () => {
  const apiModel = buildApiMapModel({
    info: { title: "Graph API", version: "1" },
    paths: {
      "/widgets": {
        post: {
          tags: ["widgets"],
          requestBody: {
            content: {
              "application/json": { schema: { $ref: "#/components/schemas/WidgetCreate" } },
            },
          },
          responses: {
            201: {
              description: "Created",
              content: {
                "application/json": { schema: { $ref: "#/components/schemas/Widget" } },
              },
            },
          },
        },
        get: {
          tags: ["widgets"],
          responses: {
            200: {
              description: "Listed",
              content: {
                "application/json": {
                  schema: { type: "array", items: { $ref: "#/components/schemas/Widget" } },
                },
              },
            },
          },
        },
      },
    },
    components: {
      schemas: {
        WidgetCreate: {
          type: "object",
          properties: { owner: { $ref: "#/components/schemas/Owner" } },
        },
        Widget: {
          type: "object",
          properties: { owner: { $ref: "#/components/schemas/Owner" } },
        },
        Owner: { type: "object" },
        Unused: { type: "string" },
      },
    },
  });

  const graph = buildApiGraphModel(apiModel);

  assert.equal(graph.nodes.filter(node => node.kind === "operation").length, 2);
  assert.equal(graph.nodes.filter(node => node.kind === "schema").length, 4);
  assert.deepEqual(graph.edges.map(edge => [edge.kind, edge.source, edge.target]), [
    ["response", "operation:get:/widgets", "schema:Widget"],
    ["request", "schema:WidgetCreate", "operation:post:/widgets"],
    ["response", "operation:post:/widgets", "schema:Widget"],
    ["schema", "schema:WidgetCreate", "schema:Owner"],
    ["schema", "schema:Widget", "schema:Owner"],
  ]);
});

test("API graph de-duplicates repeated schema relationships", () => {
  const graph = buildApiGraphModel({
    operations: [{
      id: "post:/items",
      method: "post",
      path: "/items",
      summary: "Create item",
      primaryTag: "items",
      schemas: ["Item"],
      graph: {
        requestSchemas: ["Item", "Item"],
        responseSchemas: ["Item", "Item"],
      },
    }],
    schemas: [{ name: "Item", kind: "object", description: "", references: [] }],
  });

  assert.deepEqual(graph.edges.map(edge => edge.kind), ["request", "response"]);
  assert.deepEqual(graph.edges.map(edge => edge.lane), [-0.5, 0.5]);
});

test("new contract nodes do not overlap positions preserved across refreshes", () => {
  const existing = {
    key: "operation:get:/items",
    kind: "operation",
    operationId: "get:/items",
    method: "get",
    path: "/items",
    title: "List items",
    group: "items",
  };
  const added = { ...existing, key: "operation:delete:/items", operationId: "delete:/items", method: "delete" };
  const graph = { nodes: [added, existing], edges: [] };
  const preserved = new Map([[existing.key, { x: 80, y: 90 }]]);

  const positions = reconcileApiGraphPositions(graph, preserved);

  assert.deepEqual(positions.get(existing.key), { x: 80, y: 90 });
  assert.notDeepEqual(positions.get(added.key), positions.get(existing.key));
  assert.ok(Math.abs(positions.get(added.key).y - positions.get(existing.key).y) >= 128);
});
