"""Create a separate HR derived-source demo; never modifies warehouse tables.

Usage: python dev/schemoo/derived-demo.py CONNECTION_ID
Requires the existing organization.public HR schema. Each run creates a new model.
"""
import json
import ssl
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ORIGIN = "https://localhost:8001"


def request(path, body=None):
    req = Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json"})
    # Only the canonical localhost development endpoint uses its self-signed cert.
    with urlopen(req, context=ssl._create_unverified_context(), timeout=45) as response:
        return json.load(response)


def main(connection):
    catalog = request("/api/v1/schemoo/catalog?" + urlencode({"connection_id": connection, "namespace": "public"}))
    if catalog["database"] != "organization":
        raise SystemExit("This fixture only targets the organization HR database.")
    tables = ["personnel_dim", "personnel_certification_fact", "certification_dim", "pay_band_class_dim"]
    labels = ["Personnel", "Employee certifications", "Certification definitions", "Pay bands"]
    nodes = [dict(id=table, table=table, label=label) for table, label in zip(tables, labels)]
    pairs = {(tables[1], tables[0]), (tables[1], tables[2]), (tables[0], tables[3])}
    edges = [dict(id=r["id"], relationshipId=r["id"], source=r["sourceTable"], target=r["targetTable"], enabled=True)
             for r in catalog["relationships"] if (r["sourceTable"], r["targetTable"]) in pairs]
    if len(edges) != 3:
        raise SystemExit("Expected the three HR relationships; demo was not created.")
    nodes += [dict(id="pay_calculations", table=tables[3], label="Pay calculations", derivation=dict(
        kind="row", source=tables[3], groupBy=[], outputs=[dict(id="range", label="Minimum pay × level (example)", operation="multiply", column="min_pay_range", operand="level")])),
        dict(id="certification_summary", table=tables[1], label="Certification summary", derivation=dict(
            kind="aggregate", source=tables[1], groupBy=["personnel_id"],
            connection=dict(target=tables[0],columns=[dict(source="personnel_id",target="id")]), outputs=[
                dict(id="names", label="Certification names", operation="list", nodeId=tables[2], column="name", distinct=True, delimiter=", "),
                dict(id="count", label="Certification count", operation="count_distinct", nodeId=tables[1], column="certification_id"),
                dict(id="non_expired", label="Non-expired count", operation="count_distinct", nodeId=tables[1], column="certification_id", conditions=[
                    dict(table=tables[1],column="expiration_date",operator="gte",valueSource="today",allowNull=True)]),
                dict(id="current", label="Currently valid count", operation="count_distinct", nodeId=tables[1], column="certification_id", conditions=[
                    dict(table=tables[1],column="expiration_date",operator="gte",valueSource="today",allowNull=True),
                    dict(table=tables[1],column="effective_date",operator="lte",valueSource="today")])]))]
    fields = [dict(table=t, column=c, aggregate="none") for t, c in [
        (tables[0], "name"), ("certification_summary", "count"), ("certification_summary", "non_expired"), ("certification_summary", "current"), ("certification_summary", "names"), ("pay_calculations", "range")]]
    positions = [(40, 40), (440, 480), (840, 480), (840, 40), (1240, 40), (440, 40)]
    model = request("/api/v1/schemoo/models", dict(name="HR · Source-first certification summary", connectionId=connection,
        database=catalog["database"], namespace="public", catalogFingerprint=catalog["fingerprint"],
        definition=dict(root=tables[0], nodes=nodes, edges=edges, scopes=[], exposedFields=None),
        layout=dict(positions=[dict(id=n["id"], x=p[0], y=p[1]) for n, p in zip(nodes, positions)]),
        explore=dict(root=tables[0], fields=fields, selections={}, reportFilters=[], limit=100)))
    print(f"https://omarchy.taile4f57f.ts.net/schemoo?model={model['id']}")


if __name__ == "__main__":
    main(sys.argv[1])
