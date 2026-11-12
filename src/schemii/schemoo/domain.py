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
    names.update(table.get("namespace") or namespace for table in (definition.get("sourceContract") or {}).get("tables", []))
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


def parameter_domain(catalog, definition, alternative, parameter, *, scope_id=None):
    """Resolve the same qualified saved lookup for contracts and execution."""
    explicit = parameter.get("domain")
    if explicit:
        source = domain_source(explicit, definition.get("nodes", []))
        column = explicit.get("column")
        return {**source, "column": column, "labelColumn": explicit.get("labelColumn") or column} if source.get("table") and column else None
    condition = next((item for item in alternative.get("conditions", [])
                      if parameter.get("id") and item.get("parameterId") == parameter["id"]), None)
    node = next((item for item in definition.get("nodes", []) if item["id"] == (condition or {}).get("table")), None)
    if not node or not condition.get("column"):
        return None
    source = {key: node[key] for key in ("namespace", "table") if key in node}
    column = condition["column"]
    contract, relationship = definition.get("sourceContract") or {}, None
    bindings = [item for item in contract.get("parameterDomains", [])
                if item["alternativeId"] == alternative.get("id") and item["inputId"] == parameter.get("id")
                and (scope_id is None or item["scopeId"] == scope_id)]
    if len(bindings) > 1:
        raise ValueError("The saved parameter lookup is ambiguous. Save this input with an explicit domain.")
    if bindings:
        binding = bindings[0]
        result = {key: binding[key] for key in ("namespace", "table", "column", "labelColumn") if key in binding}
        relationship_id = binding.get("relationshipId")
        if relationship_id:
            relationship = resolve_relationship(catalog, relationship_id)
            if relationship is None:
                raise ValueError("The parameter lookup foreign key was removed. Choose an explicit domain before saving.")
            pairs = relationship_columns(relationship)
            if len(pairs) != 1:
                raise ValueError("Choose an explicit domain for a composite or ambiguous relationship.")
            if (source_identity(catalog, relationship, "source") != source_identity(catalog, source)
                    or pairs[0]["source"] != column):
                raise ValueError("The parameter lookup foreign key changed its source. Choose an explicit domain before saving.")
            result.update(namespace=source_identity(catalog, relationship, "target")[0],
                          table=relationship["targetTable"], column=pairs[0]["target"], relationshipId=relationship["id"])
            if (source_identity(catalog, result), result["column"]) != (source_identity(catalog, binding), binding["column"]):
                columns = (physical_table(catalog, result) or {}).get("columns", [])
                result["labelColumn"] = "name" if any(item["name"] == "name" for item in columns) else result["column"]
        return result
    if parameter.get("type") != "source":
        def candidates(values):
            return [item for item in values if source_identity(catalog, item, "source") == source_identity(catalog, source)
                    and any(pair["source"] == column for pair in relationship_columns(item))]
        saved = candidates(contract.get("relationships", [])) if contract.get("scope") == "model" else []
        matches = ([resolve_relationship(catalog, item["id"]) for item in saved] if saved else
                   candidates(catalog.get("relationships", [])) if contract.get("scope") != "model" else [])
        if saved and any(item is None for item in matches):
            raise ValueError("The parameter lookup foreign key was removed. Choose an explicit domain before saving.")
        if matches:
            if len(matches) != 1 or len(relationship_columns(matches[0])) != 1:
                raise ValueError("Choose an explicit domain for a composite or ambiguous relationship.")
            relationship = matches[0]
            source = {"table": relationship["targetTable"], **({"namespace": relationship["targetNamespace"]} if relationship.get("targetNamespace") else {})}
            column = relationship_columns(relationship)[0]["target"]
    columns = (physical_table(catalog, source) or {}).get("columns", [])
    saved_table = next((item for item in contract.get("tables", []) if source_identity(catalog, item) == source_identity(catalog, source)), None)

    label_columns = saved_table["columns"] if saved_table and contract.get("scope") == "model" else columns
    label = "name" if parameter.get("type") != "source" and any(item["name"] == "name" for item in label_columns) else column
    return {**source, "column": column, "labelColumn": label,
            **({"relationshipId": relationship["id"]} if relationship else {})}
