"""Resolve saved domain roles without trusting a stale physical-table hint."""


def domain_table(domain, nodes):
    node_id = domain.get("nodeId")
    if node_id is None:
        return domain.get("table", "")
    node = next((node for node in nodes if node["id"] == node_id), None)
    if node is None:
        raise ValueError("The domain source alias was removed. Choose a new domain value column.")
    return node["table"]
