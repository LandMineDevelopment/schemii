"""Exact physical identities and ordered join mappings shared by model consumers."""

import hashlib
import json


def source_identity(catalog, source, side=None):
    """Missing namespace means the model's saved namespace, never name guessing."""
    return (source.get(f"{side}Namespace" if side else "namespace") or catalog.get("namespace", "public"),
            source.get(f"{side}Table" if side else "table", source.get("name", "")))


def physical_table(catalog, source):
    key = source_identity(catalog, source)
    return next((table for table in catalog.get("tables", []) if source_identity(catalog, table) == key), None)


def relationship_columns(relationship):
    """Read the legacy singleton pair; reject partial/ambiguous authored mappings."""
    pairs = relationship.get("columns")
    left, right = relationship.get("sourceColumn"), relationship.get("targetColumn")
    if pairs is None:
        pairs = [{"source": left, "target": right}]
    elif left is not None or right is not None:
        if pairs != [{"source": left, "target": right}]:
            raise ValueError("Relationship columns conflict with the legacy column pair.")
    if (not isinstance(pairs, list) or not 1 <= len(pairs) <= 32
            or any(not isinstance(pair, dict) or set(pair) != {"source", "target"}
                   or any(not isinstance(value, str) or not value or len(value) > 200 for value in pair.values()) for pair in pairs)):
        raise ValueError("Choose one to 32 complete ordered source/target column pairs.")
    if any(len({pair[side] for pair in pairs}) != len(pairs) for side in ("source", "target")):
        raise ValueError("Relationship mappings require distinct source and target columns.")
    return pairs


def legacy_relationship_id(relationship):
    return hashlib.sha256(f"{relationship['sourceTable']}:{relationship.get('name', '')}".encode()).hexdigest()[:20]


def resolve_relationship(catalog, relationship_id, nodes=None, edge=None):
    matches = [r for r in catalog.get("relationships", []) if r["id"] == relationship_id]
    if not matches:
        # Historical FK hashes omitted namespace. They can resolve only in the
        # saved default namespace and only with the exact authored endpoints.
        default = catalog.get("namespace", "public")
        matches = [r for r in catalog.get("relationships", [])
                   if source_identity(catalog, r, "source")[0] == default
                   and source_identity(catalog, r, "target")[0] == default
                   and legacy_relationship_id(r) == relationship_id]
    if nodes is not None and edge is not None:
        matches = [r for r in matches if all(edge.get(side) in nodes
                   and source_identity(catalog, nodes[edge[side]]) == source_identity(catalog, r, side)
                   for side in ("source", "target"))]
    return matches[0] if len(matches) == 1 else None


def qualified_relationship_id(namespace, table, name):
    return hashlib.sha256(json.dumps([namespace, table, name], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20]


def definition_namespaces(namespace, definition):
    names = {namespace}
    for node in definition.get("nodes", []):
        names.add(node.get("namespace") or namespace)
    for scope in definition.get("scopes", []):
        for option in scope.get("alternatives", []):
            for value in [*option.get("inputs", []), *option.get("conditions", [])]:
                domain = value.get("domain") or {}
                if domain.get("nodeId") is None and domain.get("table"):
                    names.add(domain.get("namespace") or namespace)
    if len(names) > 16:
        raise ValueError("Choose at most 16 source schemas for one model.")
    return sorted(names)


def domain_source(domain, nodes):
    node_id = domain.get("nodeId")
    if node_id is None:
        return {key: domain[key] for key in ("namespace", "table") if key in domain}
    node = next((node for node in nodes if node["id"] == node_id), None)
    if node is None:
        raise ValueError("The domain source alias was removed. Choose a new domain value column.")
    return {key: node[key] for key in ("namespace", "table") if key in node}


def domain_table(domain, nodes):
    return domain_source(domain, nodes).get("table", "")
