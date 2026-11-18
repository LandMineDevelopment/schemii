"""Portable CI semantic model and dashboard using the existing Schemii APIs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import UUID

from schemii.schemer.dashboard_models import DashboardCreate, DashboardTile
from schemii.schemoo.models import (
    Condition,
    ExploreState,
    ModelCreate,
    ModelDefinition,
    ModelNode,
    ModelScope,
    ReportFilter,
    ScopeAlternative,
    SelectedField,
)

from .analysis import instant


MODEL_API = "/api/v1/schemoo/models"
DASHBOARD_API = "/api/v1/schemer/dashboards"
SLOT_FIELDS = (
    "run_started_at",
    "run_outcome",
    "run_completeness",
    "producer_acceptance",
    "mode",
    "command_key",
    "outcome",
    "disposition",
    "verification_state",
    "case_coverage_state",
    "expected_cases",
    "observed_cases",
    "missing_case_observations",
    "eligible_first_attempt_cases",
    "excluded_first_attempt_cases",
    "unknown_first_attempt_cases",
    "first_passes",
    "first_failures",
    "cases_with_retries",
    "recovered_first_failures",
    "duration_state",
    "duration_ms",
)
RUN_FIELDS = (
    "started_at",
    "outcome",
    "mode",
    "completeness",
    "acceptance",
    "observed_slots",
    "executed_slots",
    "pending_slots",
    "reused_slots",
    "excluded_slots",
    "slot_coverage_state",
    "duration_state",
    "duration_ms",
)
ATTEMPT_TOTALS = (
    "eligible_first_attempt_cases",
    "first_passes",
    "first_failures",
    "excluded_first_attempt_cases",
    "unknown_first_attempt_cases",
    "cases_with_retries",
    "recovered_first_failures",
)


@dataclass(frozen=True)
class Scope:
    repository: UUID
    installation: UUID
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("Supply explicit UTC offsets for the scope.")
        if self.start >= self.end:
            raise ValueError("The UTC scope must have an increasing window.")

    @property
    def bounds(self) -> tuple[str, str]:
        return (
            self.start.astimezone(timezone.utc).isoformat(),
            self.end.astimezone(timezone.utc).isoformat(),
        )

    def name_suffix(self, connection_id: str) -> str:
        identity = "\n".join(
            (connection_id, str(self.repository), str(self.installation), *self.bounds)
        )
        return sha256(identity.encode()).hexdigest()[:12]


def slot_overview() -> ExploreState:
    return ExploreState(
        fields=[
            SelectedField(table="slots", column="disposition"),
            SelectedField(table="slots", column="verification_state"),
            SelectedField(table="slots", column="run_outcome"),
            SelectedField(table="slots", column="disposition", aggregate="count"),
        ]
    )


def run_overview() -> ExploreState:
    """Open this alternative root for run totals, including runs with no slots."""
    return ExploreState(
        root="runs",
        fields=[
            SelectedField(table="runs", column="outcome"),
            SelectedField(table="runs", column="completeness"),
            SelectedField(table="runs", column="acceptance"),
            SelectedField(table="runs", column="observed_slots", aggregate="count"),
            SelectedField(table="runs", column="observed_slots", aggregate="sum"),
            SelectedField(table="runs", column="pending_slots", aggregate="sum"),
        ],
    )


def model_template(scope: Scope, connection_id: str, database: str) -> ModelCreate:
    start, end = scope.bounds
    conditions: list[Condition] = []
    for node, clock in (("slots", "run_started_at"), ("runs", "started_at")):
        conditions.extend(
            (
                Condition(
                    table=node, column="repository_id", value=str(scope.repository)
                ),
                Condition(
                    table=node, column="installation_id", value=str(scope.installation)
                ),
                Condition(table=node, column=clock, operator="gte", value=start),
                Condition(table=node, column=clock, operator="lt", value=end),
            )
        )
    return ModelCreate(
        name=f"CI Verification · {scope.name_suffix(connection_id)}",
        connection_id=connection_id,
        database=database,
        namespace="devops",
        definition=ModelDefinition(
            root="slots",
            nodes=[
                ModelNode(
                    id="slots", table="v1_ci_verification", label="Command slots"
                ),
                ModelNode(id="runs", table="v1_ci_run_coverage", label="CI runs"),
            ],
            scopes=[
                ModelScope(
                    id="ci-window",
                    label=f"Fixed CI repository and installation · {start} to {end}",
                    kind="conditional",
                    requirement="required",
                    rowBehavior="require_matching",
                    alternatives=[
                        ScopeAlternative(
                            id="fixed", label="Fixed UTC window", conditions=conditions
                        )
                    ],
                )
            ],
            exposedFields=[
                SelectedField(table=node, column=column)
                for node, fields in (("slots", SLOT_FIELDS), ("runs", RUN_FIELDS))
                for column in fields
            ],
        ),
        explore=slot_overview(),
    )


def dashboard_template(model: dict[str, Any]) -> DashboardCreate:
    """Bind a dashboard to the read-back model ID and exact rules revision."""
    suffix = model["name"].removeprefix("CI Verification · ")
    overview = slot_overview()
    return DashboardCreate(
        name=f"CI Health and Performance · {suffix}",
        model_id=model["id"],
        model_revision=model["revision"],
        tiles=[
            DashboardTile(
                id="verification",
                title="Command slots by disposition, verification state and run outcome",
                kind="aggregate",
                dimensions=overview.fields[:-1],
                measures=overview.fields[-1:],
            ),
            DashboardTile(
                id="case-coverage",
                title="Command slots by case evidence state",
                kind="aggregate",
                dimensions=[SelectedField(table="slots", column="case_coverage_state")],
                measures=[
                    SelectedField(
                        table="slots", column="disposition", aggregate="count"
                    )
                ],
            ),
            DashboardTile(
                id="first-attempts",
                title="Observed first attempts and retries · counts",
                kind="aggregate",
                measures=[
                    SelectedField(table="slots", column=column, aggregate="sum")
                    for column in ATTEMPT_TOTALS
                ],
            ),
            DashboardTile(
                id="command-duration",
                title="Recorded command duration · samples and maximum (ms)",
                kind="aggregate",
                dimensions=[
                    SelectedField(table="slots", column="command_key"),
                    SelectedField(table="slots", column="outcome"),
                ],
                measures=[
                    SelectedField(
                        table="slots", column="duration_ms", aggregate="count"
                    ),
                    SelectedField(table="slots", column="duration_ms", aggregate="max"),
                ],
                report_filters=[
                    ReportFilter(
                        id="measured",
                        label="Measured original executions",
                        conditions=[
                            Condition(
                                table="slots", column="duration_state", value="measured"
                            )
                        ],
                    )
                ],
            ),
        ],
    )


class Response(Protocol):
    def raise_for_status(self) -> Any: ...
    def json(self) -> Any: ...


class Client(Protocol):
    def get(self, url: str) -> Response: ...
    def post(self, url: str, *, json: Any) -> Response: ...


def _json(response: Response) -> dict[str, Any]:
    response.raise_for_status()
    return response.json()


def _saved(
    client: Client, path: str, collection: str, template: ModelCreate | DashboardCreate
) -> dict[str, Any]:
    wanted = template.model_dump(mode="json", by_alias=True)
    matches = [
        item["id"]
        for item in _json(client.get(path))[collection]
        if item["name"] == wanted["name"]
    ]
    if len(matches) > 1:
        raise ValueError(
            "More than one saved item has the pack name. Reconcile the IDs before installing."
        )
    item_id = matches[0] if matches else _json(client.post(path, json=wanted))["id"]
    saved = _json(client.get(f"{path}/{item_id}"))
    observed = {key: saved[key] for key in wanted}
    if isinstance(template, ModelCreate):
        # These two fields are populated by the model API from the live catalog.
        observed["catalogFingerprint"] = wanted["catalogFingerprint"]
        observed["definition"] = {**observed["definition"], "sourceContract": None}
    if observed != wanted:
        raise ValueError(
            "The saved definition differs from the pack. Review the saved revision before proceeding."
        )
    return saved


def install_pack(
    client: Client,
    scope: Scope,
    *,
    connection_id: str,
    database: str,
    connection_revision: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create or reconcile the pair using a caller-owned authenticated API client.

    Serialize installs for a given owner/scope. A failed POST is not replayed
    here. A later call reads the saved objects before deciding whether to create.
    """
    template = model_template(scope, connection_id, database)
    catalog = _json(
        client.get(
            f"/api/v1/schemoo/catalog?connection_id={connection_id}&namespace=devops"
        )
    )
    if (catalog["database"], catalog["connectionRevision"]) != (
        database,
        connection_revision,
    ):
        raise ValueError(
            "The live catalog is not the selected connection revision and database."
        )
    columns = {
        table["name"]: {col["name"] for col in table["columns"]}
        for table in catalog["tables"]
    }
    for node, fields, clock in (
        ("v1_ci_verification", SLOT_FIELDS, "run_started_at"),
        ("v1_ci_run_coverage", RUN_FIELDS, "started_at"),
    ):
        if not {*fields, clock, "repository_id", "installation_id"} <= columns.get(
            node, set()
        ):
            raise ValueError(
                f"The live catalog does not contain the required fields of {node}."
            )
    saved = _saved(client, MODEL_API, "models", template)
    dashboard = _saved(client, DASHBOARD_API, "dashboards", dashboard_template(saved))
    return saved, dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    model = commands.add_parser(
        "model", help="Emit a scoped Schemoo model creation body"
    )
    model.add_argument("--connection-id", required=True)
    model.add_argument("--database", required=True)
    model.add_argument("--repository-id", type=UUID, required=True)
    model.add_argument("--installation-id", type=UUID, required=True)
    model.add_argument("--from", dest="start", type=instant, required=True)
    model.add_argument("--until", dest="end", type=instant, required=True)
    dashboard = commands.add_parser(
        "dashboard", help="Emit a dashboard body from a saved model GET response"
    )
    dashboard.add_argument("model_receipt", type=argparse.FileType("r"))
    commands.add_parser(
        "run-explore", help="Emit the separate run-grain Explore payload"
    )
    args = parser.parse_args()
    body: ModelCreate | DashboardCreate | ExploreState
    if args.command == "model":
        body = model_template(
            Scope(args.repository_id, args.installation_id, args.start, args.end),
            args.connection_id,
            args.database,
        )
    elif args.command == "dashboard":
        with args.model_receipt as stream:
            body = dashboard_template(json.load(stream))
    else:
        body = run_overview()
    print(body.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
