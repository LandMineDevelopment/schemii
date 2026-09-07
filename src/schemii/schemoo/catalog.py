"""Owner-bound live schema adapter; no credentials or row data are cached."""

from collections import OrderedDict
from copy import deepcopy
import hashlib
from threading import RLock
from time import monotonic


class ModelCatalogs:
    def __init__(self, *, maximum_entries=8, refresh_seconds=30):
        self.maximum_entries = maximum_entries
        self.refresh_seconds = refresh_seconds
        self._cache = OrderedDict()
        self._lock = RLock()

    def get(self, services, owner, connection_id, namespace, *, fresh=False):
        # Recheck ownership and connection revision even for cached catalogs.
        profile = services.connections.get(owner, connection_id)
        key = (owner, connection_id, profile.revision, namespace)
        with self._lock:
            cached = self._cache.get(key)
            if not fresh and cached and monotonic() - cached[0] < self.refresh_seconds:
                self._cache.move_to_end(key)
                return deepcopy(cached[1])
        with services.connections.use(owner, connection_id) as connection:
            live = services.postgres.introspect(connection, namespace)
            database = connection.database
        tables = [{"name": table.name, "columns": [c.model_dump(by_alias=True) for c in table.columns]}
                  for table in live.tables]
        names = {table["name"] for table in tables}
        relationships, omitted = [], 0
        for rel in live.relationships:
            if (len(rel.source_columns) != 1 or len(rel.target_columns) != 1
                    or rel.source_namespace != namespace or rel.target_namespace != namespace
                    or rel.source_table not in names or rel.target_table not in names):
                omitted += 1
                continue
            relationships.append({
                "id": hashlib.sha256(f"{rel.source_table}:{rel.name}".encode()).hexdigest()[:20],
                "name": rel.name, "sourceTable": rel.source_table,
                "sourceColumn": rel.source_columns[0], "targetTable": rel.target_table,
                "targetColumn": rel.target_columns[0],
            })
        result = {"connectionId": connection_id, "connectionRevision": profile.revision,
                  "namespace": namespace, "database": database, "fingerprint": live.fingerprint,
                  "tables": tables, "relationships": relationships,
                  "notice": f"Live schema · {omitted} composite or cross-schema relationships omitted. Model saves never change the database.",
                  "positions": []}
        with self._lock:
            self._cache[key] = (monotonic(), deepcopy(result))
            self._cache.move_to_end(key)
            while len(self._cache) > self.maximum_entries:
                self._cache.popitem(last=False)
        return result


def initial_positions(services, owner, catalog):
    """Seed presentation only; saved models never depend on a design workspace."""
    matches = [w for w in services.workspaces.list(owner)
               if (w.connection_id, w.database, w.namespace) ==
               (catalog["connectionId"], catalog["database"], catalog["namespace"])]
    if not matches:
        return catalog
    # Stable choice, not an ambiguous target resolver. This only seeds layout.
    workspace = min(matches, key=lambda w: (w.created_at, w.id))
    catalog["workspaceId"] = workspace.id
    design = services.designs.get(owner, workspace.id)
    layout = services.designs.get_layout(owner, workspace.id)
    names = {t.id: t.name for t in design.content.tables}
    live_names = {table["name"] for table in catalog["tables"]}
    catalog["positions"] = [{"name": names[p.object_id], "x": p.x, "y": p.y}
                            for p in layout.content.objects
                            if p.layer == "tables" and names.get(p.object_id) in live_names]
    return catalog


def source_issues(catalog, definition):
    """Report every missing reference rather than discarding the saved model."""
    tables = {t["name"]: {c["name"] for c in t["columns"]} for t in catalog["tables"]}
    nodes = {n["id"]: n["table"] for n in definition.get("nodes", [])}
    relationships = {r["id"] for r in catalog["relationships"]}
    issues = []
    for node, table in nodes.items():
        if table not in tables:
            issues.append({"kind": "missing_table", "nodeId": node, "table": table,
                           "message": f"Source table {table} is no longer available."})
    for edge in definition.get("edges", []):
        if edge["relationshipId"] not in relationships:
            issues.append({"kind": "missing_relationship", "edgeId": edge["id"],
                           "message": f"Relationship {edge['id']} is no longer available; rebind or remove it."})
    def check(reference, location, physical=False):
        table = reference.get("table") if physical else nodes.get(reference.get("table"))
        for key in ("column", "labelColumn"):
            column = reference.get(key)
            if column and column not in tables.get(table, set()):
                issues.append({"kind": "missing_column", "location": location, "table": table,
                               "column": column, "message": f"Column {table}.{column} is no longer available."})
    for field in definition.get("exposedFields") or []:
        check(field, "exposedFields")
    for scope in definition.get("scopes", []):
        for alternative in scope.get("alternatives", []):
            for condition in alternative.get("conditions", []):
                check(condition, scope["id"])
            for parameter in [*alternative.get("inputs", []), *[c for c in alternative.get("conditions", []) if c.get("domain")]]:
                if parameter.get("domain"):
                    domain = parameter["domain"]
                    if domain.get("nodeId") is not None and domain["nodeId"] not in nodes:
                        issues.append({"kind": "missing_domain_source", "location": parameter.get("id", scope["id"]), "nodeId": domain["nodeId"],
                                       "message": "The domain source alias was removed. Choose a new domain value column."})
                    else:
                        check({**domain, "table": nodes[domain["nodeId"]] if domain.get("nodeId") is not None else domain.get("table")}, parameter.get("id", scope["id"]), physical=True)
    return issues
