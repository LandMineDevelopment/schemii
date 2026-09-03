import assert from "node:assert/strict";
import test from "node:test";

import {
  createRelationDataSource,
  relationSelectStatement,
} from "../../src/schemii/schemii/web/assets/relation-data-source.js";

test("relation SQL is generated from the selected workspace and safely quoted", () => {
  assert.equal(
    relationSelectStatement('sales"east', 'order"items', 25),
    'SELECT *\nFROM "sales""east"."order""items"\nLIMIT 25;',
  );
});

test("relation rows and inspector lookup share one workspace-bound data source", async () => {
  const calls = [];
  const workspace = { id: "ws_live", connectionId: "pg_main", namespace: "bookstore" };
  const api = {
    async listRelations(workspaceId, options) {
      calls.push(["list", workspaceId, options]);
      return { relations: [{ ref: "rel_books", name: "books", kind: "table" }] };
    },
    async getRelationRows(workspaceId, relationRef, options) {
      calls.push(["rows", workspaceId, relationRef, options]);
      return { columns: [], rows: [], nextCursor: null };
    },
  };
  const source = createRelationDataSource({ api, getWorkspace: () => workspace });
  const relation = await source.resolve("books", "table");
  await source.page(relation, { pageSize: 50 });

  assert.equal(relation.ref, "rel_books");
  assert.equal(source.statement("books"), 'SELECT *\nFROM "bookstore"."books"\nLIMIT 100;');
  assert.deepEqual(calls, [
    ["list", "ws_live", { search: "books", pageSize: 250 }],
    ["rows", "ws_live", "rel_books", { cursor: null, pageSize: 50 }],
  ]);
});

test("relation data source refuses database reads without an attached workspace", async () => {
  const source = createRelationDataSource({ api: {}, getWorkspace: () => ({ id: "ws_local", connectionId: null, namespace: "public" }) });
  assert.equal(await source.resolve("books"), null);
  assert.equal(source.statement("books"), "");
  await assert.rejects(() => source.page({ ref: "rel_books" }), /PostgreSQL-backed workspace/);
});
