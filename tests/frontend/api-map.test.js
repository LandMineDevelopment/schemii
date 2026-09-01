import assert from "node:assert/strict";
import test from "node:test";

import {
  buildApiMapModel,
  buildSchemaContract,
  collaboratorStageId,
  collectSchemaNames,
  contractJsonShape,
  pythonSourceExcerpt,
  routeStageInitiallyOpen,
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
        "x-schemii-status": "planned",
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
  assert.equal(update.lifecycle, "planned");
  assert.equal(model.operations.find(operation => operation.operationId === "get_widget").lifecycle, "implemented");
  assert.equal(update.request.required, true);
  assert.deepEqual(update.request.schemas, ["WidgetUpdate"]);
  assert.equal(update.request.content[0].mediaType, "application/json");
  assert.equal(update.request.content[0].contract.reference, "WidgetUpdate");
  assert.deepEqual(update.parameters.map(({ contract: parameterContract, ...parameter }) => parameter), [{
    name: "widget_id",
    location: "path",
    required: true,
    description: "Operation-level parameter details",
    schema: "string",
  }]);
  assert.deepEqual(update.schemas, ["WidgetUpdate", "Widget"]);
  assert.deepEqual(update.responses.map(({ content: responseContent, ...response }) => response), [{
    status: "200",
    description: "Widget updated",
    schemas: ["Widget"],
  }]);
  assert.equal(update.responses[0].content[0].contract.reference, "Widget");
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

  assert.deepEqual(model.operations[0].responses.map(({ content, ...response }) => response), [
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
  assert.deepEqual(model.operations[0].parameters.map(({ contract: parameterContract, ...parameter }) => parameter), [{
    name: "alias",
    location: "path",
    required: true,
    description: "",
    schema: "string",
  }]);
  assert.equal(model.operations[0].request.required, true);
  assert.deepEqual(model.operations[0].request.schemas, ["string"]);
  assert.equal(model.operations[0].request.content[0].contract.type, "string");
  assert.deepEqual(model.operations[0].responses.map(({ content, ...response }) => response), [
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

test("schema contracts expose nested required fields and stop recursive references", () => {
  const specification = {
    components: {
      schemas: {
        Widget: {
          type: "object",
          description: "A recursively linked widget",
          required: ["id", "children"],
          properties: {
            id: { type: "string", minLength: 1 },
            children: {
              type: "array",
              items: { $ref: "#/components/schemas/Widget" },
            },
            state: { type: "string", enum: ["ready", "paused"], default: "ready" },
          },
        },
      },
    },
  };

  const contractShape = buildSchemaContract(specification, { $ref: "#/components/schemas/Widget" });

  assert.equal(contractShape.reference, "Widget");
  assert.equal(contractShape.description, "A recursively linked widget");
  assert.deepEqual(contractShape.properties.map(property => [property.name, property.required]), [
    ["id", true],
    ["children", true],
    ["state", false],
  ]);
  assert.deepEqual(contractShape.properties[0].contract.constraints, ["min length: 1"]);
  assert.equal(contractShape.properties[1].contract.items.recursive, true);
  assert.deepEqual(contractShape.properties[2].contract.enum, ["ready", "paused"]);
  assert.equal(contractShape.properties[2].contract.default, '"ready"');

  const jsonShape = JSON.parse(contractJsonShape(contractShape));
  assert.match(jsonShape.id, /string/);
  assert.deepEqual(jsonShape.children, ["<Widget; recursive>"]);
  assert.match(jsonShape.state, /ready \| paused/);
});

test("schema contracts model dictionaries, boolean schemas, and truncated enums", () => {
  const dictionary = buildSchemaContract({}, {
    type: "object",
    additionalProperties: { type: "integer", minimum: 0 },
  });
  const unrestricted = buildSchemaContract({}, true);
  const prohibited = buildSchemaContract({}, false);
  const enumerated = buildSchemaContract({}, {
    type: "string",
    enum: Array.from({ length: 14 }, (_, index) => `value-${index + 1}`),
  });

  assert.equal(dictionary.additionalProperties.type, "integer");
  assert.deepEqual(dictionary.additionalProperties.constraints, ["minimum: 0"]);
  assert.equal(unrestricted.type, "any");
  assert.equal(prohibited.type, "never");
  assert.equal(enumerated.enum.length, 12);
  assert.equal(enumerated.enumTruncated, true);
});

test("JSON contract shapes preserve unions and merge composed objects", () => {
  const union = buildSchemaContract({}, {
    anyOf: [{ type: "string" }, { type: "null" }],
  });
  const composition = buildSchemaContract({}, {
    allOf: [
      { type: "object", required: ["id"], properties: { id: { type: "string" } } },
      { type: "object", properties: { note: { type: "string" } } },
    ],
  });

  assert.equal(JSON.parse(contractJsonShape(union)), "<anyOf: string | null>");
  assert.deepEqual(JSON.parse(contractJsonShape(composition)), {
    id: "<string>",
    note: "<string; optional>",
  });
});

test("schema contract expansion has a global node budget", () => {
  const schemas = { Leaf: { type: "string" } };
  for (let level = 4; level >= 1; level -= 1) {
    schemas[`Level${level}`] = {
      type: "object",
      properties: Object.fromEntries(
        Array.from({ length: 20 }, (_, index) => [
          `field_${index}`,
          { $ref: `#/components/schemas/${level === 4 ? "Leaf" : `Level${level + 1}`}` },
        ]),
      ),
    };
  }
  const shape = buildSchemaContract(
    { components: { schemas } },
    { $ref: "#/components/schemas/Level1" },
  );
  const descendants = contractShape => [
    ...contractShape.properties.map(property => property.contract),
    ...(contractShape.items ? [contractShape.items] : []),
    ...(contractShape.additionalProperties ? [contractShape.additionalProperties] : []),
    ...contractShape.branches.map(branch => branch.contract),
  ];
  let count = 0;
  let foundTruncation = false;
  const queued = [shape];
  while (queued.length) {
    const current = queued.pop();
    count += 1;
    foundTruncation ||= current.truncated;
    queued.push(...descendants(current));
  }

  assert.ok(count <= 400);
  assert.equal(foundTruncation, true);
});

test("route inspection is joined by method and path without entering the OpenAPI graph", () => {
  const endpointId = "python:example.routes:get_widget";
  const model = buildApiMapModel(contract, {
    schemaVersion: 1,
    routes: [{
      id: "get:/widgets/{widget_id}",
      endpointId,
      dependencies: [],
      calls: [],
      requestObjectIds: [],
      responseObjectIds: [],
      implementationDigest: "abc123",
    }],
    objects: [{
      id: endpointId,
      name: "get_widget",
      qualname: "get_widget",
      module: "example.routes",
      kind: "handler",
      docstring: "Return one widget.",
      location: { path: "example/routes.py", sourceStartLine: 10, definitionLine: 11, endLine: 14 },
      source: { available: true, sha256: "abc123", text: "def get_widget():\n    pass\n", truncated: false },
    }],
  });

  const operation = model.operations.find(item => item.operationId === "get_widget");
  assert.equal(model.implementationInspectionAvailable, true);
  assert.equal(operation.inspectionAvailable, true);
  assert.equal(operation.story.endpoint.docstring, "Return one widget.");
  assert.equal(operation.story.implementationDigest, "abc123");
  assert.deepEqual(operation.graph.responseSchemas, ["Widget", "ApiError"]);
});

test("Python call-site excerpts preserve syntax token boundaries", () => {
  const sourceText = [
    "def route():\n",
    "    value = service.get(\n",
    "        item_id,\n",
    "    )\n",
    "    return value\n",
  ].join("");
  const object = {
    location: { sourceStartLine: 20 },
    source: {
      text: sourceText,
      tokens: [
        ["keyword", "def"],
        ["plain", " route():\n    value = service.get(\n        item_id,\n    )\n    "],
        ["keyword", "return"],
        ["plain", " value\n"],
      ],
    },
  };

  const excerpt = pythonSourceExcerpt(object, 21, 1);

  assert.equal(excerpt.startLine, 20);
  assert.equal(excerpt.endLine, 22);
  assert.equal(excerpt.tokens.map(([, value]) => value).join(""), excerpt.text);
  assert.match(excerpt.text, /service\.get/);
  assert.doesNotMatch(excerpt.text, /return value/);

  const fullCall = pythonSourceExcerpt(object, 21, 0, 23);
  assert.equal(fullCall.startLine, 21);
  assert.equal(fullCall.endLine, 23);
  assert.match(fullCall.text, /item_id/);
  assert.match(fullCall.text, /\)\n$/);
  assert.doesNotMatch(fullCall.text, /def route|return value/);
});

test("route drill-downs start closed unless a stage is explicitly deep-linked", () => {
  assert.equal(routeStageInitiallyOpen("operation-stage-handler"), false);
  assert.equal(routeStageInitiallyOpen("operation-stage-handler", "#operation-stage-request"), false);
  assert.equal(routeStageInitiallyOpen("operation-stage-handler", "#operation-stage-handler"), true);
});

test("collaborator stage links use stable call identities instead of list positions", () => {
  const call = {
    object: { id: "python:example.services:WidgetService.get", name: "get" },
    expression: "service.get(widget_id)",
    line: 42,
  };

  assert.equal(collaboratorStageId({ ...call }), collaboratorStageId(call));
  assert.match(collaboratorStageId(call), /^operation-stage-collaborator-get-[a-z0-9]+$/);
  assert.notEqual(collaboratorStageId({ ...call, line: 43 }), collaboratorStageId(call));
});
