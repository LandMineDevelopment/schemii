function quoteIdentifier(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

export function relationSelectStatement(namespace, relationName, limit = 100) {
  return `SELECT *\nFROM ${quoteIdentifier(namespace)}.${quoteIdentifier(relationName)}\nLIMIT ${limit};`;
}

export function createRelationDataSource({ api, getWorkspace }) {
  function workspace() {
    const current = getWorkspace();
    return current?.connectionId && current?.namespace ? current : null;
  }

  async function resolve(name, kind = null) {
    const current = workspace();
    if (!current) return null;
    const response = await api.listRelations(current.id, { search: name, pageSize: 250 });
    return response.relations.find(item => item.name === name && (!kind || item.kind === kind)) || null;
  }

  async function page(relation, { cursor = null, pageSize = 100 } = {}) {
    const current = workspace();
    if (!current) throw new Error("A PostgreSQL-backed workspace is required to browse rows.");
    return api.getRelationRows(current.id, relation.ref, { cursor, pageSize });
  }

  function statement(relationName, limit = 100) {
    const current = workspace();
    if (!current) return "";
    return relationSelectStatement(current.namespace, relationName, limit);
  }

  return Object.freeze({ resolve, page, statement });
}
