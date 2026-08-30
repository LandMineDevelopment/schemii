import assert from "node:assert/strict";
import test from "node:test";

import {
  buildApiMapModel,
  collectSchemaNames,
  schemaReferenceName,
} from "../../src/schemii/schemii/web/assets/api-map.js";

const contract = {
  openapi: "3.1.0",
  info: {
    title: "Example API",
    version: "2.4.0",
    description: "Current example contract",
  },
  tags: [{ name: "widgets", description: "Widget operations" }],
  paths: {
    "/widgets/{widget_id}": {
      parameters: [
        {
          name: "widget_id",
          in: "path",
          required: true,
          schema: { type: "string" },
        },
      ],
      get: {
        tags: ["widgets"],
        operationId: "get_widget",
        summary: "Get widget",
        responses: {
          200: {
            description: "Widget found",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/Widget" },
              },
            },
          },
          404: {
            description: "Widget missing",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/ApiError" },
              },
            },
          },
        },
      },
      patch: {
        tags: ["widgets"],
        operationId: "update_widget",
        summary: "Update widget",
        parameters: [
          {
            name: "widget_id",
            in: "path",
            required: true,
            description: "Operation-level parameter details",
            schema: { type: "string" },
          },
        ],
        requestBody: {
          required: true,
          content: {
            "application/json": {
              schema: { $ref: "#/components/schemas/WidgetUpdate" },
            },
          },
        },
        responses: {
          200: {
            description: "Widget updated",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/Widget" },
              },
            },
          },
        },
      },
    },
    "/health": {
      get: {
        operationId: "health",
        responses: { 204: { description: "Healthy" } },
      },
    },
  },
  components: {
    schemas: {
      Widget: { type: "object" },
      WidgetUpdate: { type: "object" },
      ApiError: { type: "object" },
    },
  },
};

test("buildApiMapModel derives groups, operations, and contracts from OpenAPI", () => {
  const model = buildApiMapModel(contract);

  assert.equal(model.title, "Example API");
  assert.equal(model.version, "2.4.0");
  assert.equal(model.pathCount, 2);
  assert.equal(model.schemaCount, 3);
  assert.equal(model.operations.length, 3);
  assert.deepEqual(model.groups.map(group => group.name), ["ungrouped", "widgets"]);
  assert.equal(model.groups[1].description, "Widget operations");

  const update = model.operations.find(operation => operation.operationId === "update_widget");
  assert.deepEqual(update.request, { required: true, schemas: ["WidgetUpdate"] });
  assert.deepEqual(update.parameters, [{
    name: "widget_id",
    location: "path",
    required: true,
    description: "Operation-level parameter details",
    schema: "string",
  }]);
  assert.deepEqual(update.schemas, ["WidgetUpdate", "Widget"]);
  assert.deepEqual(update.responses, [{
    status: "200",
    description: "Widget updated",
    schemas: ["Widget"],
  }]);
});

test("schema references are decoded and duplicate references are collapsed", () => {
  assert.equal(schemaReferenceName("#/components/schemas/Order~1Line"), "Order/Line");
  assert.equal(schemaReferenceName("https://example.test/schema.json"), "https://example.test/schema.json");
  assert.equal(schemaReferenceName(null), null);

  assert.deepEqual(collectSchemaNames({
    oneOf: [
      { $ref: "#/components/schemas/Widget" },
      { items: { $ref: "#/components/schemas/Widget" } },
      { $ref: "#/components/schemas/ApiError" },
    ],
    example: { $ref: "not-a-contract-reference" },
  }), ["Widget", "ApiError"]);
});

test("composed and collection schema shapes remain visible", () => {
  const model = buildApiMapModel({
    paths: {
      "/widgets": {
        get: {
          parameters: [{
            name: "filter",
            in: "query",
            content: {
              "application/json": {
                schema: {
                  oneOf: [
                    { $ref: "#/components/schemas/Widget" },
                    { type: "string" },
                  ],
                },
              },
            },
          }],
          responses: {
            200: {
              description: "Widget collection",
              content: {
                "application/json": {
                  schema: {
                    type: "array",
                    items: { $ref: "#/components/schemas/Widget" },
                  },
                },
              },
            },
            400: {
              description: "Union response",
              content: {
                "application/json": {
                  schema: {
                    oneOf: [
                      { $ref: "#/components/schemas/ApiError" },
                      { type: "string" },
                    ],
                  },
                },
              },
            },
          },
        },
      },
    },
  });

  assert.deepEqual(model.operations[0].responses, [
    { status: "200", description: "Widget collection", schemas: ["Array<Widget>"] },
    { status: "400", description: "Union response", schemas: ["oneOf<ApiError | string>"] },
  ]);
  assert.equal(model.operations[0].parameters[0].schema, "oneOf<Widget | string>");
});

test("unsupported path-item fields are not represented as operations", () => {
  const model = buildApiMapModel({
    info: {},
    paths: {
      "/items": {
        summary: "Shared path summary",
        servers: [{ url: "https://example.test" }],
        get: { responses: { 204: { description: "Done" } } },
      },
    },
  });

  assert.equal(model.operations.length, 1);
  assert.equal(model.operations[0].id, "get:/items");
  assert.equal(model.groups[0].name, "ungrouped");
});

test("local OpenAPI references, inline schemas, and response ranges are resolved", () => {
  const model = buildApiMapModel({
    info: { title: "Referenced API" },
    paths: {
      "/aliases/{alias}": { $ref: "#/components/pathItems/Alias" },
    },
    components: {
      pathItems: {
        Alias: {
          get: {
            parameters: [{ $ref: "#/components/parameters/Alias" }],
            requestBody: { $ref: "#/components/requestBodies/AliasLookup" },
            responses: {
              "2XX": { $ref: "#/components/responses/AliasFound" },
              default: {
                description: "Unexpected response",
                content: {
                  "application/json": { schema: { type: "integer" } },
                },
              },
            },
          },
        },
      },
      parameters: {
        Alias: {
          name: "alias",
          in: "path",
          required: true,
          schema: { type: "string" },
        },
      },
      requestBodies: {
        AliasLookup: {
          required: true,
          content: {
            "application/json": { schema: { type: "string" } },
          },
        },
      },
      responses: {
        AliasFound: {
          description: "Alias found",
          content: {
            "application/json": {
              schema: { $ref: "#/components/schemas/Alias" },
            },
          },
        },
      },
      schemas: { Alias: { type: "object" } },
    },
  });

  assert.equal(model.operations.length, 1);
  assert.deepEqual(model.operations[0].parameters, [{
    name: "alias",
    location: "path",
    required: true,
    description: "",
    schema: "string",
  }]);
  assert.deepEqual(model.operations[0].request, { required: true, schemas: ["string"] });
  assert.deepEqual(model.operations[0].responses, [
    { status: "2XX", description: "Alias found", schemas: ["Alias"] },
    { status: "default", description: "Unexpected response", schemas: ["integer"] },
  ]);

  const encodedReference = buildApiMapModel({
    paths: { "/encoded": { $ref: "#%2Fcomponents%2FpathItems%2FAlias%252ELookup" } },
    components: {
      pathItems: {
        "Alias.Lookup": {
          get: { responses: { 204: { description: "Resolved" } } },
        },
      },
    },
  });
  assert.equal(encodedReference.operations[0].path, "/encoded");
});
