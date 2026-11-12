"""Resolve saved domain roles without trusting a stale physical-table hint."""


def domain_table(domain, nodes):
    node_id = domain.get("nodeId")
    if node_id is None:
        return domain.get("table", "")
    node = next((node for node in nodes if node["id"] == node_id), None)
    if node is None:
        raise ValueError("The domain source alias was removed. Choose a new domain value column.")
    return node["table"]


def parameter_domain(catalog, definition, alternative, parameter):
    """Resolve the same saved lookup binding for contracts and execution."""
    explicit = parameter.get("domain")
    if explicit:
        table = domain_table(explicit, definition.get("nodes", []))
        column = explicit.get("column")
        return {"table": table, "column": column,
                "labelColumn": explicit.get("labelColumn") or column} if table and column else None
    condition = next((item for item in alternative.get("conditions", [])
                      if parameter.get("id") and item.get("parameterId") == parameter["id"]), None)
    node = next((item for item in definition.get("nodes", [])
                 if item["id"] == (condition or {}).get("table")), None)
    if not node or not condition.get("column"):
        return None
    table, column = node["table"], condition["column"]
    contract = definition.get("sourceContract") or {}
    relationship = None
    if parameter.get("type") != "source":
        saved = next((item for item in (contract.get("relationships", []) if contract.get("scope") == "model" else [])
                      if (item["sourceTable"], item["sourceColumn"]) == (table, column)), None)
        if saved:
            relationship = next((item for item in catalog.get("relationships", [])
                                 if item["id"] == saved["id"]), None)
            if relationship is None:
                raise ValueError("The parameter lookup foreign key was removed. Choose an explicit domain before saving.")
        elif contract.get("scope") != "model":
            relationship = next((item for item in catalog.get("relationships", [])
                                 if (item["sourceTable"], item["sourceColumn"]) == (table, column)), None)
        if relationship:
            table, column = relationship["targetTable"], relationship["targetColumn"]
    columns = next((item["columns"] for item in catalog.get("tables", []) if item["name"] == table), [])
    saved_table = next((item for item in contract.get("tables", []) if item["name"] == table), None)
    label_columns = saved_table["columns"] if saved_table and contract.get("scope") == "model" else columns
    label = "name" if parameter.get("type") != "source" and any(item["name"] == "name" for item in label_columns) else column
    return {"table": table, "column": column, "labelColumn": label,
            **({"relationshipId": relationship["id"]} if relationship else {})}
