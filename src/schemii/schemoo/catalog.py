"""Owner-bound live schema adapter; no credentials or row data are cached."""

from collections import OrderedDict
from copy import deepcopy
import hashlib
from threading import RLock
from time import monotonic
from .domain import (domain_source, source_identity, relationship_columns, resolve_relationship,
                     qualified_relationship_id, legacy_relationship_id, parameter_domain)


class ModelCatalogs:
    def __init__(self, *, maximum_entries=8, refresh_seconds=30):
        self.maximum_entries = maximum_entries
        self.refresh_seconds = refresh_seconds
        self._cache = OrderedDict()
        self._lock = RLock()

    def get(self, services, owner, connection_id, namespace, *, fresh=False, namespaces=None):
        # One profile/database identity owns every explicitly selected namespace.
        selected = tuple(sorted(set([namespace, *(namespaces or [])])))
        if not 1 <= len(selected) <= 16 or any(not isinstance(name, str) or not 1 <= len(name) <= 63 for name in selected):
            raise ValueError("Choose at most 16 valid source schemas for one model.")
        profile = services.connections.get(owner, connection_id)
        profile_owner = getattr(profile, "owner_id", None) or owner
        managed = profile_owner != owner
        key = (owner, profile_owner, connection_id, profile.revision, namespace, selected)
        with self._lock:
            cached = self._cache.get(key)
            if not managed and not fresh and cached and monotonic() - cached[0] < self.refresh_seconds:
                self._cache.move_to_end(key)
                return deepcopy(cached[1])
        tables, relationships, fingerprints, raw_relationships = [], [], [], []
        readable = {}
        with services.connections.use(owner, connection_id) as connection:
            database = connection.database
            for name in selected:
                live = services.postgres.introspect(connection, name)
                # USAGE and every SELECT column are checked under the exact
                # connection identity; PostgreSQL grants can change independently.
                readable[name] = services.postgres.readable_columns(connection, name)
                fingerprints.append((name, live.fingerprint))
                for relation in [*live.tables, *getattr(live, "views", ()), *getattr(live, "materialized_views", ())]:
                    columns = [c.model_dump(by_alias=True) for c in relation.columns
                               if (relation.name, c.name) in readable[name]]
                    if not columns:
                        continue
                    primary = getattr(relation, "primary_key", None)
                    unique = getattr(relation, "unique_constraints", ())
                    tables.append({"namespace": name, "name": relation.name, "columns": columns,
                                   "primaryKey": list(primary.columns) if primary and primary.validated
                                   and all((relation.name, c) in readable[name] for c in primary.columns) else [],
                                   "uniqueKeys": [list(key.columns) for key in unique if key.validated
                                                  and all((relation.name, c) in readable[name] for c in key.columns)]})
                raw_relationships.extend(live.relationships)
        keys = {source_identity({"namespace": namespace}, table) for table in tables}
        seen, omitted = set(), 0
        for rel in raw_relationships:
            identity = (rel.source_namespace, rel.source_table, rel.name)
            if identity in seen:
                continue
            seen.add(identity)
            pairs = [{"source": left, "target": right} for left, right in zip(rel.source_columns, rel.target_columns)]
            if (not pairs or len(pairs) != len(rel.source_columns) or len(pairs) != len(rel.target_columns)
                    or (rel.source_namespace, rel.source_table) not in keys
                    or (rel.target_namespace, rel.target_table) not in keys
                    or any((rel.source_table, pair["source"]) not in readable[rel.source_namespace]
                           or (rel.target_table, pair["target"]) not in readable[rel.target_namespace] for pair in pairs)):
                omitted += 1
                continue
            relationship_columns({"columns": pairs})
            relationships.append({"id": qualified_relationship_id(*identity), "name": rel.name,
                                  "legacyId": legacy_relationship_id({"sourceTable": rel.source_table, "name": rel.name}) if rel.source_namespace == namespace and rel.target_namespace == namespace else "",
                                  "sourceNamespace": rel.source_namespace, "sourceTable": rel.source_table,
                                  "targetNamespace": rel.target_namespace, "targetTable": rel.target_table,
                                  "columns": pairs,
                                  **({"sourceColumn": pairs[0]["source"], "targetColumn": pairs[0]["target"]} if len(pairs) == 1 else {})})
        fingerprint = fingerprints[0][1] if len(fingerprints) == 1 else hashlib.sha256(repr(fingerprints).encode()).hexdigest()
        result = {"connectionId": connection_id, "connectionRevision": profile.revision,
                  "namespace": namespace, "namespaces": list(selected), "database": database, "fingerprint": fingerprint,
                  "tables": tables, "relationships": relationships,
                  "notice": f"Live schema · {omitted} relationships outside selected schemas or readable columns omitted. Model saves never change the database.",
                  "positions": []}
        if not managed:
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


def source_dependencies(catalog, definition, *, previous=None):
    """Resolve authored fields and implicit join/grain inputs to physical columns.

    Client contract entries are never an input to dependency discovery. Aliases
    share one physical baseline, and calculated output IDs are not source columns.
    """
    tables = {source_identity(catalog, table): table for table in catalog.get("tables", [])}
    available_columns = {name: {column["name"] for column in table.get("columns", [])} for name, table in tables.items()}
    nodes = {node["id"]: node for node in definition.get("nodes", [])}
    selected, used_relationships = {}, set()

    def implicit_bindings(value):
        source_nodes = {node["id"]: node for node in (value or {}).get("nodes", [])}
        bindings = {}
        for scope in (value or {}).get("scopes", []):
            for option in scope.get("alternatives", []):
                for parameter in option.get("inputs", []):
                    predicate = next((item for item in option.get("conditions", [])
                                      if parameter.get("id") and item.get("parameterId") == parameter["id"]), {})
                    node_id = predicate.get("table")
                    bindings[(scope.get("id"), option.get("id"), parameter.get("id"))] = (
                        parameter.get("type"), parameter.get("domain"), node_id,
                        predicate.get("column"), source_identity(catalog, source_nodes.get(node_id, {})))
        return bindings

    prior_bindings = implicit_bindings(previous)
    current_bindings = implicit_bindings(definition)

    def table_columns(table):
        if table not in tables or not tables[table].get("columns"):
            label = table[1] if table[0] == catalog.get("namespace", "public") else f"{table[0]}.{table[1]}"
            raise ValueError(f"Source {label or '(unbound)'} is unavailable or has no readable columns. Reload sources and choose an accessible source.")
        return selected.setdefault(table, set())

    def physical(table, column):
        columns = table_columns(table)
        if column and column not in available_columns[table]:
            raise ValueError(f"Source column {table[1] if table[0] == catalog.get('namespace', 'public') else '.'.join(table)}.{column} is unavailable. Reload sources and review your selection.")
        if column:
            columns.add(column)

    def reference(node_id, column):
        node = nodes.get(node_id)
        if node is None:
            raise ValueError(f"Model source {node_id or '(unbound)'} is unavailable. Choose a source in this model.")
        if node.get("derivation"):
            available = set(node["derivation"].get("groupBy", [])) | {output["id"] for output in node["derivation"].get("outputs", [])}
            if column not in available:
                raise ValueError(f"Calculated field {node_id}.{column} is unavailable.")
        else:
            physical(source_identity(catalog, node), column)

    def domain(value):
        if not value:
            return
        # Enabling a searchable domain creates a typed but unbound draft. It
        # contributes no physical source until a table or model role is chosen.
        # Keep that authoring state intact; bound references still validate below.
        if value.get("nodeId") is None and not value.get("table"):
            return
        table = source_identity(catalog, domain_source(value, nodes.values()))
        physical(table, value.get("column"))
        if value.get("labelColumn"):
            physical(table, value["labelColumn"])

    def condition(value):
        # Draft saves may retain incomplete bindings for author repair.
        if value.get("table") and value.get("column"):
            reference(value["table"], value["column"])
        if value.get("compareColumn") and value.get("table"):
            reference(value["table"], value["compareColumn"])
        domain(value.get("domain"))

    for node in nodes.values():
        if not node.get("derivation"):
            table = source_identity(catalog, node)
            table_columns(table)
            if definition.get("exposedFields") is None:
                selected[table].update(column["name"] for column in tables[table]["columns"])
    for field in definition.get("exposedFields") or []:
        reference(field["table"], field["column"])
    for edge in definition.get("edges", []):
        if edge.get("kind") == "logical":
            for side in ("source", "target"):
                for pair in relationship_columns(edge):
                    reference(edge[side], pair[side])
        else:
            relationship = resolve_relationship(catalog, edge["relationshipId"], nodes, edge)
            if not relationship:
                raise ValueError(f"Foreign key {edge['relationshipId']} is unavailable. Reload sources and review your selection.")
            for side in ("source", "target"):
                node = nodes.get(edge[side])
                if node is None or node.get("derivation") or source_identity(catalog, node) != source_identity(catalog, relationship, side):
                    raise ValueError("Foreign-key connections must bind matching physical sources in this model.")
                for pair in relationship_columns(relationship):
                    physical(source_identity(catalog, node), pair[side])
            used_relationships.add(relationship["id"])
    for scope in definition.get("scopes", []):
        for alternative in scope.get("alternatives", []):
            for item in alternative.get("conditions", []):
                condition(item)
            for parameter in alternative.get("inputs", []):
                if parameter.get("domain"):
                    domain(parameter["domain"])
                    continue
                key = (scope.get("id"), alternative.get("id"), parameter.get("id"))
                # Only a new or deliberately rebound parameter discovers a new
                # implicit domain. An unchanged saved no-FK binding stays local.
                lookup_definition = definition
                if previous is not None and prior_bindings.get(key) != current_bindings.get(key):
                    lookup_definition = {**definition, "sourceContract": None}
                lookup = parameter_domain(catalog, lookup_definition, alternative, parameter)
                domain(lookup)
                if lookup and lookup.get("relationshipId"):
                    used_relationships.add(lookup["relationshipId"])
    for node in nodes.values():
        derivation = node.get("derivation")
        if not derivation:
            continue
        source = derivation["source"]
        for column in derivation.get("groupBy", []):
            reference(source, column)
        connection = derivation.get("connection") or {}
        for pair in connection.get("columns", []):
            reference(source, pair["source"])
            reference(connection["target"], pair["target"])
        for output in derivation.get("outputs", []):
            contributor = output.get("nodeId", source)
            reference(contributor, output["column"])
            if output.get("operand"):
                reference(output.get("operandNodeId", source), output["operand"])
            for item in output.get("conditions", []):
                condition(item)
    # Grain and identity assumptions need their columns even when hidden.
    for name in list(selected):
        for column in [*tables[name].get("primaryKey", []),
                       *(column for key in tables[name].get("uniqueKeys", []) for column in key)]:
            physical(name, column)
    return selected, used_relationships


def catalog_contract(catalog, definition=None, *, previous=None):
    """Schema-only baseline, scoped server-side for new model writes."""
    selected, relationships = source_dependencies(catalog, definition, previous=previous) if definition is not None else (None, None)
    authored = {}
    if definition is not None:
        nodes = {node["id"]: node for node in definition.get("nodes", [])}
        for edge in definition.get("edges", []):
            live = resolve_relationship(catalog, edge.get("relationshipId"), nodes, edge) if edge.get("kind") != "logical" else None
            if live is not None:
                authored[live["id"]] = edge["relationshipId"]

    return {
        "scope": "model" if selected is not None else "catalog",
        "tables": [{"name": table["name"], **({"namespace": table["namespace"]} if table.get("namespace") else {}), "primaryKey": list(table.get("primaryKey", [])),
                    **({"uniqueKeys": deepcopy(table.get("uniqueKeys", []))} if selected is not None else {}),
                    "columns": [{"name": column["name"], "dataType": column.get("dataType", ""),
                                 "nullable": column.get("nullable", True)}
                                for column in table.get("columns", [])
                                if selected is None or column["name"] in selected[source_identity(catalog, table)]]}
                   for table in catalog.get("tables", []) if selected is None or source_identity(catalog, table) in selected],
        "relationships": [{key: (authored.get(relationship["id"], relationship["id"]) if key == "id" else deepcopy(relationship[key])) for key in
                           ("id", "name", "sourceTable", "sourceColumn", "targetTable", "targetColumn", "sourceNamespace", "targetNamespace", "columns") if key in relationship}
                          for relationship in catalog.get("relationships", [])
                          if relationships is None or relationship["id"] in relationships],
    }


def model_source_contract(catalog, definition, previous=None):
    """Derive membership from the model; preserve unacknowledged saved assumptions.

    An acknowledgement is accepted only when it exactly matches live metadata.
    Omitting or fabricating client entries cannot omit a required dependency or
    replace the server's prior baseline. New dependencies use live metadata.
    """
    # Only the server's prior contract may pin an implicit lookup identity.
    # Client omissions cannot suppress dependencies during initial creation.
    dependency_definition = {**definition, "sourceContract": (previous or {}).get("sourceContract")}
    current = catalog_contract(catalog, dependency_definition, previous=previous)
    prior = (previous or {}).get("sourceContract") or {}
    proposed = definition.get("sourceContract") or {}
    prior_tables = {source_identity(catalog, table): table for table in prior.get("tables", [])}
    proposed_tables = {source_identity(catalog, table): table for table in proposed.get("tables", [])}
    for table in current["tables"]:
        saved, submitted = prior_tables.get(source_identity(catalog, table), {}), proposed_tables.get(source_identity(catalog, table), {})
        for key in ("primaryKey", "uniqueKeys"):
            if saved.get(key) is not None and submitted.get(key) != table[key]:
                table[key] = deepcopy(saved[key])
        old_columns = {column["name"]: column for column in saved.get("columns", [])}
        submitted_columns = {column["name"]: column for column in submitted.get("columns", [])}
        table["columns"] = [deepcopy(old_columns[column["name"]])
                            if column["name"] in old_columns and submitted_columns.get(column["name"]) != column
                            else column for column in table["columns"]]
    old_relationships = {relationship["id"]: relationship for relationship in prior.get("relationships", [])}
    submitted_relationships = {relationship["id"]: relationship for relationship in proposed.get("relationships", [])}
    current["relationships"] = [deepcopy(old_relationships[relationship["id"]])
                                if relationship["id"] in old_relationships and submitted_relationships.get(relationship["id"]) != relationship
                                else relationship for relationship in current["relationships"]]
    return current


def source_drift(catalog, definition):
    """Compare semantic assumptions with today's live schema without treating additions as drift."""
    contract = definition.get("sourceContract")
    if not contract:
        return []
    scoped = contract.get("scope") == "model"
    live_tables = {source_identity(catalog, table): table for table in catalog.get("tables", [])}
    nodes_by_table = {}
    for node in definition.get("nodes", []):
        if not node.get("derivation"):
            nodes_by_table.setdefault(source_identity(catalog, node), []).append(node["id"])
    unrestricted = definition.get("exposedFields") is None
    referenced = {(field.get("table"), field.get("column")) for field in definition.get("exposedFields") or []}
    for edge in definition.get("edges", []):
        if edge.get("kind") == "logical":
            referenced.update((edge.get(side), pair[side]) for side in ("source", "target") for pair in relationship_columns(edge))
    for scope in definition.get("scopes", []):
        for option in scope.get("alternatives", []):
            referenced.update((condition.get("table"), condition[key]) for condition in option.get("conditions", [])
                              for key in ("column", "compareColumn") if condition.get(key))
            for parameter in option.get("inputs", []):
                domain = parameter.get("domain") or {}
                if domain.get("nodeId"):
                    referenced.update((domain["nodeId"], domain.get(key)) for key in ("column", "labelColumn") if domain.get(key))
    for node in definition.get("nodes", []):
        derivation = node.get("derivation") or {}
        source = derivation.get("source")
        referenced.update((source, column) for column in derivation.get("groupBy", []))
        connection = derivation.get("connection") or {}
        for pair in connection.get("columns", []):
            referenced.add((source, pair.get("source")))
            referenced.add((connection.get("target"), pair.get("target")))
        for output in derivation.get("outputs", []):
            contributor = output.get("nodeId", source)
            referenced.add((contributor, output.get("column")))
            if output.get("operand"):
                referenced.add((output.get("operandNodeId", source), output["operand"]))
            referenced.update((condition.get("table"), condition[key]) for condition in output.get("conditions", [])
                              for key in ("column", "compareColumn") if condition.get(key))
    issues = []
    for expected in contract.get("tables", []):
        live = live_tables.get(source_identity(catalog, expected))
        if not live:
            if scoped and source_identity(catalog, expected) not in nodes_by_table:
                issues.append({"kind": "missing_table", "severity": "breaking", "table": expected["name"], "namespace": source_identity(catalog, expected)[0],
                               "acknowledge": False,
                               "message": f"Parameter lookup source {expected['name']} is no longer available. Repair its domain before saving."})
            continue  # source_issues reports modeled missing tables with repair context
        node_ids = nodes_by_table.get(source_identity(catalog, expected), [])
        if list(expected.get("primaryKey", [])) != list(live.get("primaryKey", [])) and (node_ids or scoped):
            issues.append({"kind": "changed_primary_key", "severity": "breaking", "table": expected["name"], "namespace": source_identity(catalog, expected)[0],
                           "nodeIds": node_ids, "expected": expected.get("primaryKey", []), "current": live.get("primaryKey", []),
                           "acknowledge": True,
                           "message": f"Primary key changed on {expected['name']}: {expected.get('primaryKey', []) or 'none'} → {live.get('primaryKey', []) or 'none'}. Review joins and summary grain before accepting it."})
        if scoped and expected.get("uniqueKeys") is not None and any(key not in live.get("uniqueKeys", []) for key in expected["uniqueKeys"]):
            issues.append({"kind": "changed_unique_key", "severity": "breaking", "table": expected["name"], "namespace": source_identity(catalog, expected)[0],
                           "nodeIds": node_ids, "acknowledge": True,
                           "message": f"Unique keys changed on {expected['name']}. Review summary grain before accepting them."})
        live_columns = {column["name"]: column for column in live.get("columns", [])}
        for column in expected.get("columns", []):
            current = live_columns.get(column["name"])
            if not current:
                # Unrestricted exposure follows the live catalog, so a removed
                # column only blocks when an explicit rule/output still uses it.
                affected = [node_id for node_id in node_ids if scoped or (node_id, column["name"]) in referenced]
                issues.append({"kind": "removed_column", "severity": "breaking" if scoped or affected else "warning",
                               "table": expected["name"], "namespace": source_identity(catalog, expected)[0], "column": column["name"], "nodeIds": affected or node_ids,
                               "acknowledge": not (scoped or affected),
                               "message": f"Source column {expected['name']}.{column['name']} was removed." +
                               (" Repair model fields, keys, filters, or calculations that use it." if scoped or affected else " It was not exposed by this model.")})
                continue
            affected = [node_id for node_id in node_ids if scoped or unrestricted or (node_id, column["name"]) in referenced]
            changes = []
            if column.get("dataType", "") != current.get("dataType", ""):
                changes.append(f"type {column.get('dataType') or 'unknown'} → {current.get('dataType') or 'unknown'}")
            if column.get("nullable", True) != current.get("nullable", True):
                changes.append(f"nullable {column.get('nullable', True)} → {current.get('nullable', True)}")
            if changes and (scoped or affected):
                issues.append({"kind": "changed_column", "severity": "breaking", "table": expected["name"], "namespace": source_identity(catalog, expected)[0],
                               "column": column["name"], "nodeIds": affected, "acknowledge": True,
                               "message": f"Referenced column {expected['name']}.{column['name']} changed ({'; '.join(changes)}). Review filters and calculations before accepting it."})
    live_relationships = {relationship["id"]: relationship for relationship in catalog.get("relationships", [])}
    used_edges = {}
    for edge in definition.get("edges", []):
        if edge.get("kind") != "logical":
            used_edges.setdefault(edge["relationshipId"], []).append(edge)
    signature = lambda relationship: (relationship.get("name", ""), source_identity(catalog, relationship, "source"),
                                      source_identity(catalog, relationship, "target"),
                                      tuple((pair["source"], pair["target"]) for pair in relationship_columns(relationship)))
    for expected in contract.get("relationships", []):
        live = resolve_relationship(catalog, expected["id"], {node["id"]: node for node in definition.get("nodes", [])}, used_edges[expected["id"]][0]) if expected["id"] in used_edges else resolve_relationship(catalog, expected["id"])
        if not live and expected["id"] not in used_edges:
            issues.append({"kind": "removed_relationship", "severity": "breaking" if scoped else "warning", "relationshipId": expected["id"],
                           "acknowledge": not scoped,
                           "message": f"Foreign key {expected.get('name') or expected['id']} was removed from the source. " +
                           ("Repair the parameter lookup before saving." if scoped else "No model connection used it.")})
        if live and signature(expected) != signature(live) and (scoped or expected["id"] in used_edges):
            edges = used_edges.get(expected["id"], [])
            issues.append({"kind": "changed_relationship", "severity": "breaking", "relationshipId": expected["id"],
                           "edgeIds": [edge["id"] for edge in edges], "acknowledge": True,
                           "message": f"Foreign key {expected.get('name') or expected['id']} changed from {source_identity(catalog, expected, 'source')} {relationship_columns(expected)} to {source_identity(catalog, live, 'source')} {relationship_columns(live)}. Review every affected connection before accepting it."})
    return issues


def source_issues(catalog, definition):
    """Report every missing reference rather than discarding the saved model."""
    tables = {source_identity(catalog, t): {c["name"] for c in t["columns"]} for t in catalog["tables"]}
    nodes = {n["id"]: source_identity(catalog, n) for n in definition.get("nodes", [])}
    derived_columns = {n["id"]: set(n["derivation"].get("groupBy", [])) | {out["id"] for out in n["derivation"].get("outputs", [])}
                       for n in definition.get("nodes", []) if n.get("derivation")}
    issues = []
    for node, table in nodes.items():
        if table not in tables:
            issues.append({"kind": "missing_table", "nodeId": node, "table": table[1], "namespace": table[0],
                           "message": f"Source table {table} is no longer available."})
    for edge in definition.get("edges", []):
        if edge.get("kind") == "logical":
            for side in ("source", "target"):
                table = nodes.get(edge.get(side))
                for pair in relationship_columns(edge):
                    column = pair[side]
                    if column not in tables.get(table, set()):
                        issues.append({"kind": "missing_column", "edgeId": edge["id"], "table": (table or ("", ""))[1], "namespace": (table or ("", ""))[0],
                                       "column": column, "message": f"Logical relationship column {table}.{column} is no longer available."})
            continue
        if resolve_relationship(catalog, edge["relationshipId"], {node["id"]: node for node in definition.get("nodes", [])}, edge) is None:
            issues.append({"kind": "missing_relationship", "edgeId": edge["id"],
                           "message": f"Relationship {edge['id']} is no longer available; rebind or remove it."})
    def check(reference, location, physical=False):
        table = source_identity(catalog, reference) if physical else nodes.get(reference.get("table"))
        columns = derived_columns.get(reference.get("table"), tables.get(table, set())) if not physical else tables.get(table, set())
        for key in ("column", "compareColumn", "labelColumn"):
            column = reference.get(key)
            if column and column not in columns:
                issues.append({"kind": "missing_column", "location": location, "table": (table or ("", ""))[1], "namespace": (table or ("", ""))[0],
                               "column": column, "message": f"Column {table}.{column} is no longer available."})
    for node in definition.get("nodes", []):
        derivation = node.get("derivation")
        if not derivation:
            continue
        source = derivation["source"]
        if source not in nodes:
            issues.append({"kind": "missing_calculation_source", "nodeId": node["id"], "message": "The calculation's source was removed."})
        connection = derivation.get("connection")
        if connection:
            if connection["target"] not in nodes:
                issues.append({"kind": "missing_calculation_target", "nodeId": node["id"], "message": "The summary's connection target was removed. Choose a new model connection."})
            for pair in connection["columns"]:
                check({"table": source, "column": pair["source"]}, node["id"])
                check({"table": connection["target"], "column": pair["target"]}, node["id"])
        for column in derivation.get("groupBy", []):
            check({"table": source, "column": column}, node["id"])
        for output in derivation.get("outputs", []):
            contributor = output.get("nodeId", source)
            check({"table": contributor, "column": output["column"]}, node["id"])
            if output.get("operand"):
                check({"table": output.get("operandNodeId", source), "column": output["operand"]}, node["id"])
            for condition in output.get("conditions", []):
                check(condition, node["id"])
                if condition.get("domain"):
                    domain = condition["domain"]
                    domain_node = domain.get("nodeId", condition["table"])
                    check({"table": domain_node, "column": domain.get("column"), "labelColumn": domain.get("labelColumn")}, node["id"])
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
                        check({**domain, **domain_source(domain, definition.get("nodes", []))}, parameter.get("id", scope["id"]), physical=True)
    for issue in issues:
        issue.setdefault("severity", "breaking")
    drift = source_drift(catalog, definition)
    removed_columns = {(issue.get("namespace"), issue.get("table"), issue.get("column")) for issue in drift
                       if issue.get("kind") == "removed_column" and issue.get("severity") == "breaking"}
    # The contract-backed removal issue carries impact and acknowledgement context;
    # do not repeat the older generic missing-reference report for the same column.
    issues = [issue for issue in issues if not (
        issue.get("kind") == "missing_column"
        and (issue.get("namespace"), issue.get("table"), issue.get("column")) in removed_columns)]
    return issues + drift
