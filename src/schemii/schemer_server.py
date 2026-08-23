from __future__ import annotations

import json
import os
import secrets
import hashlib
import math
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .schemer_ai_actions import normalize_schemer_action
from .ai_operation_maintenance import AiOperationMaintenance, AiOperationMaintenanceConfig, OperationLeaseLost
from .ai_http import AiHttpRouter, ai_conversation_title, authority_call, ensure_ai_conversation_title, issue_ai_proposals
from .dashboard_store import DashboardStore, DashboardStoreError
from .http_common import make_local_app_handler, metadata_profile_dependencies
from .schema_store import SchemaStore
from .opencode_service import OpenCodeService, OpenCodeServiceError
from .metadata import MetadataConfig, MetadataConnectionFactory, MetadataStore, MetadataStoreError
from .secret_file import read_secret_file
from .postgres_http import (
    POSTGRES_CATALOG_CAPABILITY,
    POSTGRES_CONSOLE_CAPABILITY,
    POSTGRES_CONSOLE_WRITE_CAPABILITY,
    POSTGRES_PROFILE_CAPABILITY,
    POSTGRES_READ_SQL_CAPABILITY,
    POSTGRES_RELATION_QUERY_CAPABILITY,
    PostgresRoutePolicy,
    ReadSqlRoutePolicy,
    PostgresHttpMixin,
)
from .postgres_service import PostgresService, PostgresServiceError
from .postgres_console import ConsolePolicy
from .schemer_ai import (
    SCHEMER_AI_SKILLS,
    SCHEMER_AI_SYSTEM_INSTRUCTIONS,
    SCHEMER_AI_TOOL_ACTION_TYPES,
    dashboard_context,
)
from .schemer_metadata_authority import SchemerMetadataAuthority, retire_legacy_schemer_authority
from .schemer_ai_executor import SchemerAiExecutor
from .widget_query import QueryValidationError, normalize_query
from .query_type_capabilities import snapshot_column
from .readiness import readiness_report


def _configured_ai_widget(service, action, operation_id, widget_count):
    source = action["source"]
    descriptor = service.inspect_relation(source["profileId"], source["database"], source["namespace"], source["relation"], source["kind"], source["fingerprint"])
    columns = [snapshot_column(column) for column in descriptor["columns"]]
    verified_source = {key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")} | {"snapshotVersion": 2, "columns": columns}
    try:
        query = normalize_query(action["query"], columns)
    except QueryValidationError as error:
        raise PostgresServiceError(400, "invalid_widget_query", str(error)) from error
    dimensions, measures = query["dimensions"], query["measures"]
    table = {"version": 1, "columns": [
        {"targetId": item["id"], "width": 180 if kind == "dimension" else 120, "hidden": False, "pinned": kind == "dimension", "label": item["label"]}
        for kind, values in (("dimension", dimensions), ("measure", measures)) for item in values
    ], "pageSize": 25}
    dimension_id = dimensions[0]["id"] if dimensions else None
    measure_ids = [item["id"] for item in measures]
    visualization = {"version": 1, "mode": action["visualizationMode"], "selections": {
        "kpi": {"measureIds": measure_ids}, "bar": {"dimensionId": dimension_id, "measureIds": measure_ids},
        "line": {"dimensionId": dimension_id, "measureIds": measure_ids}, "donut": {"dimensionId": dimension_id, "measureId": measure_ids[0]},
    }}
    detail = {"version": 1, "columns": [{"sourceColumn": item["name"], "label": item["name"], "width": 160, "hidden": False, "searchable": True, "numberFormat": {"style": "auto"}} for item in columns[:64]], "defaultSort": None, "rowIdentifier": None, "pageSize": 25}
    widget_id = f"widget_{hashlib.sha256(operation_id.encode()).hexdigest()[:20]}"
    if action["visualizationMode"] in {"bar", "line", "donut"} and not dimensions:
        raise PostgresServiceError(400, "invalid_visualization", "Chart widgets require at least one dimension")
    if action["visualizationMode"] == "kpi" and dimensions:
        raise PostgresServiceError(400, "invalid_visualization", "KPI widgets cannot persist grouped dimensions")
    selected_query = json.loads(json.dumps(query))
    if action["visualizationMode"] == "kpi": selected_query["dimensions"] = []
    elif action["visualizationMode"] in {"bar", "line", "donut"}:
        selected_query["dimensions"] = [dimensions[0]]
        selected_query["measures"] = [measures[0]] if action["visualizationMode"] == "donut" else measures
    selected_ids = {item["id"] for item in selected_query["dimensions"]} | {item["id"] for item in selected_query["measures"]}
    selected_query["sort"] = [item for item in selected_query["sort"] if item["targetId"] in selected_ids]
    result = service.execute_widget_query(source["profileId"], verified_source, selected_query)
    if action["visualizationMode"] in {"bar", "line", "donut"}:
        values = [row[index] for row in result.get("rows", []) for index in range(1, len(row))]
        try:
            numeric = [float(value) for value in values if value is not None and not isinstance(value, bool)]
        except (TypeError, ValueError, OverflowError):
            raise PostgresServiceError(409, "invalid_visualization", "Query result is not numeric enough for the selected chart")
        if len(numeric) != len([value for value in values if value is not None]) or any(not math.isfinite(value) for value in numeric):
            raise PostgresServiceError(409, "invalid_visualization", "Query result is not finite numeric data")
        if action["visualizationMode"] == "donut" and any(value is None for value in values):
            raise PostgresServiceError(409, "invalid_visualization", "Donut chart results cannot contain null values")
        if action["visualizationMode"] in {"bar", "donut"} and any(value < 0 for value in numeric) or action["visualizationMode"] == "donut" and not any(value > 0 for value in numeric):
            raise PostgresServiceError(409, "invalid_visualization", "Query result cannot render the selected non-negative chart")
        if action["visualizationMode"] == "line" and not numeric:
            raise PostgresServiceError(409, "invalid_visualization", "Line chart results require at least one finite non-null point")
    return {"id": widget_id, "kind": "aggregate_report", "title": action["title"], "layout": {"desktop": {"x": 0, "y": 0, "w": 4, "h": 3}, "mobile": {"order": widget_count, "h": 3}}, "configuration": {"source": verified_source, "query": query, "table": table, "visualization": visualization, **({"detail": detail} if columns else {})}}


def _saved_widget_projection(configuration):
    query = configuration["query"]
    visualization = configuration.get("visualization")
    if not isinstance(visualization, dict) or visualization.get("mode") == "table":
        return query
    mode = visualization["mode"]
    selection = visualization["selections"][mode]
    dimension_ids = [] if mode == "kpi" else [selection.get("dimensionId")] if selection.get("dimensionId") else []
    measure_ids = [selection["measureId"]] if mode == "donut" else selection["measureIds"]
    target_ids = set(dimension_ids + measure_ids)
    return {
        **query,
        "dimensions": [item for item in query["dimensions"] if item["id"] in dimension_ids],
        "measures": [item for item in query["measures"] if item["id"] in measure_ids],
        "sort": [item for item in query["sort"] if item["targetId"] in target_ids],
    }
from .schemer_examples import mercury_dashboard_from_service
from .server_runtime import begin_http_shutdown, parse_port, parse_proxy_setting, postgres_runtime_config, run_server, validate_static_directory
from .postgres_concurrency import PostgresExecutionController


def _ai_catalog_sources(service: PostgresService, record: dict, target: dict[str, str] | None) -> list[dict]:
    candidates = []
    if target is not None:
        catalog = service.list_relations(target["profileId"], target["database"], target["namespace"])
        candidates.extend({
            "profileId": target["profileId"], "database": target["database"], "namespace": target["namespace"],
            "relation": relation["name"], "kind": relation["kind"],
        } for relation in catalog.get("relations", [])[:8])
    else:
        for widget in record["dashboard"]["widgets"]:
            source = widget.get("configuration", {}).get("source")
            if isinstance(source, dict):
                candidates.append({key: source.get(key) for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")})
    resolved = []
    seen = set()
    used_bytes = 0
    for candidate in candidates:
        key = tuple(candidate.get(field) for field in ("profileId", "database", "namespace", "relation", "kind", "fingerprint"))
        if key in seen or len(resolved) >= 8:
            continue
        seen.add(key)
        try:
            descriptor = service.inspect_relation(
                candidate["profileId"], candidate["database"], candidate["namespace"], candidate["relation"],
                candidate.get("kind"), candidate.get("fingerprint"),
            )
        except (KeyError, PostgresServiceError, ValueError):
            continue
        safe = {
            "profileId": descriptor["profileId"], "database": descriptor["database"], "namespace": descriptor["namespace"],
            "relation": descriptor["relation"], "kind": descriptor["kind"], "fingerprint": descriptor["fingerprint"],
            "columns": [
                {
                    **{key: column[key] for key in ("name", "type", "nullable", "ordinal", "suggestions") if key in column},
                    **({"capabilities": {
                        **{key: column["capabilities"][key] for key in ("groupable", "distinct", "sortable", "numeric", "temporal", "capabilityFingerprint")},
                        "filterOperators": [item["name"] for item in column["capabilities"]["filterOperators"]],
                        "aggregates": [item["name"] for item in column["capabilities"]["aggregates"]],
                    }} if "capabilities" in column else {}),
                }
                for column in descriptor.get("columns", [])
            ],
        }
        if descriptor.get("snapshotVersion") == 2:
            safe["snapshotVersion"] = 2
        size = len(json.dumps(safe, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        if size > 12 * 1024 or used_bytes + size > 28 * 1024:
            continue
        resolved.append(safe)
        used_bytes += size
    return resolved


def _paths() -> tuple[Path, Path, Path]:
    web_dir = Path(__file__).resolve().parent / "schemer_web"
    configured = os.environ.get("SCHEMER_CONFIG_DIR") or os.environ.get("SCHEMII_CONFIG_DIR", "~/.config/schemii")
    dashboard_dir = os.environ.get("SCHEMER_DASHBOARD_DIR", "~/.local/share/schemer/dashboards")
    return web_dir, Path(configured).expanduser().resolve(), Path(dashboard_dir).expanduser().resolve()


def make_handler(
    web_dir: Path,
    service: PostgresService,
    dashboard_store: DashboardStore,
    session_token: str,
    *,
    server_id: str,
    ai_authority: SchemerMetadataAuthority,
    ai_service: OpenCodeService | None = None,
    dependency_schema_store: SchemaStore | None = None,
    behind_loopback_proxy: bool = False,
    ai_maintenance: AiOperationMaintenance | None = None,
):
    base_handler = make_local_app_handler(
        web_dir, service, session_token, server_id=server_id, behind_loopback_proxy=behind_loopback_proxy,
    )
    ai_router = AiHttpRouter(
        ai_service,
        lambda handler, current_service, session_id, body: handler._ai_message(current_service, session_id, body),
        lambda handler, current_service, session_id, proposal_id, operation, body: handler._ai_proposal(
            current_service, session_id, proposal_id, operation, body,
        ),
        lambda handler, current_service, session_id: handler._ai_history(current_service, session_id),
        lambda handler, current_service, session_id, operation_id: handler._ai_operation_status(
            current_service, session_id, operation_id,
        ),
        lambda handler, current_service, body: handler._ai_create_session(current_service, body),
        lambda handler, current_service, session_id: handler._ai_activity(current_service, session_id),
        lambda handler, current_service, session_id: handler._ai_delete_session(current_service, session_id),
        proposal_operations=frozenset({"execute", "reconcile"}),
        settings_handler=lambda handler, body: authority_call(
            handler, ai_authority.get_settings if body is None else lambda: ai_authority.update_settings(body),
        ),
        cancellation_handler=lambda handler, current_service, session_id, proposal_id: handler._ai_cancel_proposal(
            current_service, session_id, proposal_id,
        ),
        title_handler=lambda handler, current_service, session_id, body: handler._ai_title(
            current_service, session_id, body,
        ),
    )
    ai_executor = SchemerAiExecutor(
        service, dashboard_store, ai_authority,
        catalog_sources=_ai_catalog_sources, configured_widget=_configured_ai_widget,
    )

    class SchemerHandler(PostgresHttpMixin, base_handler):
        postgres_console_policy = ConsolePolicy(allow_write=True, human_write_intent=True)
        postgres_route_policy = PostgresRoutePolicy(
            "schemer",
            frozenset({
                POSTGRES_PROFILE_CAPABILITY, POSTGRES_CATALOG_CAPABILITY, POSTGRES_RELATION_QUERY_CAPABILITY,
                POSTGRES_READ_SQL_CAPABILITY, POSTGRES_CONSOLE_CAPABILITY, POSTGRES_CONSOLE_WRITE_CAPABILITY,
            }),
            read_sql=ReadSqlRoutePolicy(
                require_database=True, require_profile_fingerprint=True,
                context_fields=frozenset({"dashboardId", "expectedRevision"}), allow_explain=False,
                max_rows=100, max_columns=50, max_result_bytes=256 * 1024,
            ),
            relation_query_context_fields=frozenset({"dashboardId", "expectedRevision"}),
            temporal_series_context_fields=frozenset({"dashboardId", "expectedRevision", "widgetId"}),
            relation_detail_context_fields=frozenset({"dashboardId", "expectedRevision"}),
            read_sql_guard=lambda handler, body: handler._postgres_dashboard_revision_guard(body),
            relation_query_guard=lambda handler, body: handler._postgres_dashboard_revision_guard(body),
            relation_detail_guard=lambda handler, body: handler._postgres_dashboard_revision_guard(body),
            temporal_series_guard=lambda handler, body: handler._postgres_temporal_series_guard(body),
            saved_widget_query=lambda handler, profile_id, body: handler._postgres_saved_widget_query(profile_id, body),
            saved_widget_detail=lambda handler, profile_id, body: handler._postgres_saved_widget_detail(profile_id, body),
        )

        def _ai_create_session(self, current_ai_service, body):
            def create():
                base_fields = {"model", "dashboardId", "accessLevel"}
                data_fields = base_fields | {"profileId", "database", "namespace"}
                access = body.get("accessLevel") if isinstance(body, dict) else None
                if access not in {"metadata", "dashboard", "data"} or set(body) != (data_fields if access == "data" else base_fields):
                    raise OpenCodeServiceError(400, "validation_error", "AI session context is invalid")
                dashboard_id = body.get("dashboardId")
                record = dashboard_store.get(dashboard_id)
                target = {}
                if access == "data":
                    profile_id = body.get("profileId")
                    database = PostgresService._validate_database(body.get("database"))
                    namespace = PostgresService._validate_namespace(body.get("namespace"))
                    selected = next((item for item in service.list_profiles() if item.get("id") == profile_id), None)
                    if selected is None:
                        raise OpenCodeServiceError(404, "not_found", "Profile was not found")
                    if selected.get("dbname") != database:
                        raise OpenCodeServiceError(409, "database_changed", "The saved profile database does not match the requested database")
                    target = {
                        "profileId": profile_id, "database": database, "namespace": namespace,
                        "profileFingerprint": service.profile_context_fingerprint(profile_id),
                    }
                title = str(record["dashboard"].get("title") or "Dashboard chat")[:80]
                provisioned = ai_authority.provision_chat(dashboard_id)
                chat_id = provisioned["chatId"]
                try:
                    created = current_ai_service.create_session(title, body.get("model"))
                    ai_authority.bind_external_session(chat_id, created["id"], created.get("title") or title)
                    chat = ai_authority.activate_chat(chat_id, target, access)
                except Exception:
                    try:
                        ai_authority.fail_chat(chat_id, "provider_or_activation_failed")
                    except Exception:
                        pass
                    if "created" in locals():
                        try:
                            current_ai_service.delete_session(created["id"])
                        except Exception:
                            pass
                    raise
                return self._public_ai_chat(chat)
            return self._ai_call(create, 201)

        @staticmethod
        def _public_ai_chat(chat):
            target = {key: chat["target"][key] for key in ("profileId", "database", "namespace") if key in chat["target"]}
            return {
                "id": chat["id"], "title": chat["title"], "dashboardId": chat["dashboardId"],
                "accessLevel": chat["accessLevel"], "target": target,
                "capabilities": chat["capabilities"], "policyRevision": chat["policyRevision"],
            }

        def _ai_chat(self, session_id):
            chat = ai_authority.get_chat(session_id)
            dashboard_store.get(chat["dashboardId"])
            if chat["target"]:
                profile = next((item for item in service.list_profiles() if item.get("id") == chat["target"]["profileId"]), None)
                if profile is None or profile.get("dbname") != chat["target"]["database"] or service.profile_context_fingerprint(profile["id"]) != chat["target"]["profileFingerprint"]:
                    raise OpenCodeServiceError(409, "session_context_changed", "The saved PostgreSQL target changed; create a new chat")
            return chat

        def _ai_activity(self, current_ai_service, session_id):
            try:
                chat = self._ai_chat(session_id)
            except (MetadataStoreError, OpenCodeServiceError, DashboardStoreError) as error:
                payload = error.payload if hasattr(error, "payload") else error.to_dict()
                return self.send_json(error.status, payload)
            return AiHttpRouter._activity_stream(self, current_ai_service, chat["externalSessionId"])

        def _ai_delete_session(self, current_ai_service, session_id):
            def delete():
                chat = ai_authority.begin_delete(session_id)
                result = current_ai_service.delete_session(chat["externalSessionId"])
                ai_authority.finish_delete(session_id)
                return result
            return self._ai_call(delete)

        def _ai_title(self, current_ai_service, session_id: str, body: dict):
            if set(body) != {"title"}:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "AI chat title fields are invalid"}})
            try:
                title = ai_conversation_title(body.get("title"), truncate=False)
            except ValueError as error:
                return self.send_json(400, {"error": {"code": "validation_error", "message": str(error)}})

            def rename():
                self._ai_chat(session_id)
                return self._public_ai_chat(ai_authority.rename_conversation(session_id, title))

            return self._ai_call(rename)

        def _authorize_dashboard(self) -> bool:
            return self._authorize_local_api("Dashboard API", "Dashboard session token is missing or invalid")

        def _postgres_profile_dependency_impact(self, profile_id):
            impact = {"schemas": [], "dashboards": [], "activeChats": [], "plans": [], "operations": []}
            for record in dashboard_store.list():
                widgets = [item["id"] for item in record["dashboard"]["widgets"] if item.get("configuration", {}).get("source", {}).get("profileId") == profile_id]
                if widgets:
                    impact["dashboards"].append({"id": record["id"], "revision": record["revision"], "name": record["dashboard"]["title"], "widgetIds": widgets})
            if dependency_schema_store is not None:
                for record in dependency_schema_store.list():
                    postgres = record.get("schema", {}).get("postgres", {})
                    if postgres.get("sourceProfileId") == profile_id:
                        impact["schemas"].append({"id": record["id"], "revision": record.get("revision", 0), "name": record["schema"].get("projectName", "")})
            impact.update(metadata_profile_dependencies(ai_authority, profile_id))
            return impact

        @contextmanager
        def _postgres_dashboard_revision_guard(self, body):
            dashboard_id = body.get("dashboardId")
            expected_revision = body.get("expectedRevision")
            if not isinstance(dashboard_id, str) or isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                raise PostgresServiceError(400, "validation_error", "SQL dashboard context is invalid")
            try:
                with dashboard_store.guard_revision(dashboard_id, expected_revision):
                    yield
            except DashboardStoreError as error:
                detail = error.payload["error"]
                raise PostgresServiceError(error.status, detail["code"], detail["message"]) from error

        def _saved_widget(self, profile_id, body):
            dashboard_id = body.get("dashboardId")
            expected_revision = body.get("expectedRevision")
            widget_id = body.get("widgetId")
            if not isinstance(dashboard_id, str) or isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or not isinstance(widget_id, str):
                raise PostgresServiceError(400, "validation_error", "Saved widget binding is invalid")
            try:
                guard = dashboard_store.guard_revision(dashboard_id, expected_revision)
                record = guard.__enter__()
            except DashboardStoreError as error:
                detail = error.payload["error"]
                raise PostgresServiceError(error.status, detail["code"], detail["message"]) from error
            widget = next((item for item in record["dashboard"]["widgets"] if item["id"] == widget_id), None)
            configuration = widget.get("configuration", {}) if widget else {}
            if not widget or configuration.get("source", {}).get("profileId") != profile_id or not isinstance(configuration.get("query"), dict):
                guard.__exit__(None, None, None)
                raise PostgresServiceError(409, "saved_widget_changed", "The saved widget source or query is no longer available")
            return guard, configuration

        def _postgres_saved_widget_query(self, profile_id, body):
            guard, configuration = self._saved_widget(profile_id, body)
            try:
                return service.execute_widget_query(profile_id, configuration["source"], _saved_widget_projection(configuration))
            finally:
                guard.__exit__(None, None, None)

        def _postgres_saved_widget_detail(self, profile_id, body):
            guard, configuration = self._saved_widget(profile_id, body)
            try:
                saved_detail = configuration.get("detail")
                if not isinstance(saved_detail, dict):
                    raise PostgresServiceError(409, "saved_widget_changed", "The saved widget detail projection is no longer available")
                detail = {
                    "version": 1,
                    "columns": [
                        {"id": f"detail_column_{index + 1}", "label": item["label"], "column": item["sourceColumn"], "numberFormat": item["numberFormat"], "searchable": item["searchable"]}
                        for index, item in enumerate(saved_detail["columns"])
                    ],
                    "rowIdentifier": saved_detail["rowIdentifier"],
                }
                return service.execute_relation_detail(
                    profile_id, configuration["source"], _saved_widget_projection(configuration), body["selection"],
                    detail, body["offset"], body["limit"], body["sort"], body["searches"],
                )
            finally:
                guard.__exit__(None, None, None)

        @contextmanager
        def _postgres_temporal_series_guard(self, body):
            dashboard_id = body.get("dashboardId")
            expected_revision = body.get("expectedRevision")
            widget_id = body.get("widgetId")
            if not isinstance(dashboard_id, str) or isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or not isinstance(widget_id, str):
                raise PostgresServiceError(400, "validation_error", "Temporal series dashboard context is invalid")
            try:
                with dashboard_store.guard_revision(dashboard_id, expected_revision) as record:
                    widget = next((item for item in record["dashboard"]["widgets"] if item["id"] == widget_id), None)
                    configuration = widget.get("configuration", {}) if widget else {}
                    visualization = configuration.get("visualization", {})
                    selection = visualization.get("selections", {}).get("line", {})
                    saved_query = configuration.get("query", {})
                    selected_dimension = selection.get("dimensionId")
                    selected_measures = selection.get("measureIds", [])
                    target_ids = {selected_dimension, *selected_measures}
                    expected_query = {
                        **saved_query,
                        "dimensions": [item for item in saved_query.get("dimensions", []) if item.get("id") == selected_dimension],
                        "measures": [item for item in saved_query.get("measures", []) if item.get("id") in selected_measures],
                        "sort": [item for item in saved_query.get("sort", []) if item.get("targetId") in target_ids],
                    }
                    if not widget or visualization.get("mode") != "line" or configuration.get("source") != body.get("source") or expected_query != body.get("query"):
                        raise PostgresServiceError(409, "temporal_series_changed", "The temporal series no longer matches the saved line widget")
                    yield
            except DashboardStoreError as error:
                detail = error.payload["error"]
                raise PostgresServiceError(error.status, detail["code"], detail["message"]) from error

        def _dashboard_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)
            except MetadataStoreError as error:
                self.send_json(error.status, error.to_dict())
            except PostgresServiceError as error:
                self.send_json(error.status, error.to_dict())

        def _ai_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except OpenCodeServiceError as error:
                self.send_json(error.status, error.payload)
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)
            except MetadataStoreError as error:
                self.send_json(error.status, error.to_dict())
            except PostgresServiceError as error:
                self.send_json(error.status, error.to_dict())

        def _service_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except PostgresServiceError as error:
                self.send_json(error.status, error.to_dict())
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)
            except MetadataStoreError as error:
                self.send_json(error.status, error.to_dict())

        @staticmethod
        def _dashboard_id(path: str) -> str | None:
            prefix = "/api/dashboards/"
            return path[len(prefix):] if path.startswith(prefix) else None

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/readiness":
                status, report = readiness_report(ai_authority, ai_service, service, ai_maintenance)
                return self.send_json(status, report)
            if self._handle_common_get(path):
                return
            if path == "/api/dashboards":
                if self._authorize_dashboard():
                    self._dashboard_call(lambda: {"dashboards": dashboard_store.list()})
                return
            if path == "/api/dashboards/summary":
                if self._authorize_dashboard():
                    self._dashboard_call(lambda: {"summaries": dashboard_store.list_summaries()})
                return
            if ai_router.handle_get(self, path):
                return
            dashboard_id = self._dashboard_id(path)
            if dashboard_id is not None:
                if self._authorize_dashboard():
                    self._dashboard_call(lambda: dashboard_store.get(dashboard_id))
                return
            if self._handle_postgres_get(parsed):
                return
            if path == "/":
                self.path = "/index.html"
            return super().do_GET()

        def do_HEAD(self):
            if urlparse(self.path).path == "/":
                self.path = "/index.html"
            return super().do_HEAD()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                if not self._authorize_shutdown():
                    return
                begin_http_shutdown(self, "schemer-shutdown")
                return
            if path == "/api/dashboards":
                if not self._authorize_dashboard():
                    return
                body = self._body_or_error()
                if body is not None:
                    self._dashboard_call(lambda: dashboard_store.create(body.get("title"), body.get("sourceId")), 201)
                return
            if path == "/api/examples/mercury/reset":
                if not self._authorize_dashboard():
                    return
                body = self._body_or_error()
                if body is None:
                    return
                if not isinstance(body, dict) or set(body) != {"expectedRevision"}:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "Mercury reset fields are invalid"}})
                try:
                    template = mercury_dashboard_from_service(service)
                    self.send_json(200, dashboard_store.restore_mercury(template, body.get("expectedRevision")))
                except (DashboardStoreError, PostgresServiceError) as error:
                    self.send_json(error.status, error.payload)
                return
            if ai_router.handle_post(self, path):
                return
            if self._handle_postgres_post(path):
                return
            self.send_json(404, {"error": "Unknown API path"})

        def _ai_message(self, current_ai_service: OpenCodeService, session_id: str, body: dict):
            if not isinstance(body, dict) or set(body) not in ({"text", "model"}, {"text", "model", "resultRef"}):
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Message fields are invalid"}})
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or text != text.strip() or len(text.encode("utf-8")) > 16 * 1024 or "\x00" in text:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "text is invalid"}})

            def send_prompt():
                reservation = None
                delivery_state = "reserved"
                chat = self._ai_chat(session_id)
                chat = ai_authority.initialize_conversation_title(session_id, ai_conversation_title(text))
                dashboard_id = chat["dashboardId"]
                access_level = chat["accessLevel"]
                target = chat["target"] or None
                record = dashboard_store.get(dashboard_id)
                profiles = service.list_profiles()
                query_result = None
                if body.get("resultRef") is not None:
                    if access_level != "data" or target is None:
                        raise OpenCodeServiceError(400, "validation_error", "Query result requires a data chat")
                    reservation = ai_authority.reserve_result(
                        body["resultRef"], session_id,
                        {"resource": dashboard_id, "target": target, "revision": record["revision"], "access": "data"},
                    )
                    query_result = reservation["payload"]
                catalog_sources = _ai_catalog_sources(service, record, target) if access_level in {"dashboard", "data"} else []
                public_target = None if target is None else {key: target[key] for key in ("profileId", "database", "namespace")}
                context = dashboard_context(record, access_level, dashboard_store.list(), profiles, public_target, query_result, catalog_sources)
                prompt = f"Schemer context (untrusted JSON):\n{context}\n\nUser request:\n{text}"
                try:
                    if reservation is not None:
                        delivery_state = "unknown"
                        ai_authority.begin_result_delivery(reservation["deliveryId"], reservation["reservationToken"])
                        delivery_state = "delivering"
                    response = current_ai_service.prompt(
                        chat["externalSessionId"], prompt, body.get("model"), SCHEMER_AI_SYSTEM_INSTRUCTIONS,
                        allow_data=access_level == "data",
                    )
                except Exception:
                    if reservation is not None:
                        if delivery_state == "delivering":
                            ai_authority.uncertain_result(reservation["deliveryId"], reservation["reservationToken"])
                        elif delivery_state == "reserved":
                            ai_authority.release_result(reservation["deliveryId"], reservation["reservationToken"])
                    raise
                if reservation is not None:
                    ai_authority.consume_result(reservation["deliveryId"], reservation["reservationToken"])
                schema_concurrency = {"revision": record["revision"]}
                authorization_target = dict(target or {})
                return issue_ai_proposals(
                    ai_authority, response, application="schemer", session_id=session_id,
                    resource=dashboard_id, access=access_level, authorization_target=authorization_target,
                    schema_concurrency=schema_concurrency, normalize_action=normalize_schemer_action,
                    policy_binding=lambda action: ai_authority.policy_binding(chat, action),
                )

            return self._ai_call(send_prompt)

        def _ai_history(self, current_ai_service, session_id: str | None):
            def history():
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                if any(len(values) != 1 for values in query.values()):
                    raise OpenCodeServiceError(400, "validation_error", "AI history context is invalid")
                dashboard_id = query.get("dashboardId", [None])[0]
                access_level = query.get("accessLevel", [None])[0]
                base_fields = {"dashboardId", "accessLevel"}
                target_fields = {"profileId", "database", "namespace"}
                if access_level not in {"metadata", "dashboard", "data"} or set(query) != (base_fields | target_fields if access_level == "data" else base_fields):
                    raise OpenCodeServiceError(400, "validation_error", "AI history context is invalid")
                target = {}
                if access_level == "data":
                    profile_id = query["profileId"][0]
                    database = PostgresService._validate_database(query["database"][0])
                    namespace = PostgresService._validate_namespace(query["namespace"][0])
                    selected = next((item for item in service.list_profiles() if item.get("id") == profile_id), None)
                    if selected is None or selected.get("dbname") != database:
                        raise OpenCodeServiceError(404, "not_found", "AI chat target was not found")
                    target = {
                        "profileId": profile_id, "database": database, "namespace": namespace,
                        "profileFingerprint": service.profile_context_fingerprint(profile_id),
                    }
                if session_id is None:
                    identities = {item.get("id"): item for item in current_ai_service.list_sessions().get("sessions", [])}
                    sessions = []
                    for chat in ai_authority.list_chats(dashboard_id if isinstance(dashboard_id, str) else None):
                        if chat["accessLevel"] != access_level or chat["target"] != target:
                            continue
                        identity = identities.get(chat["externalSessionId"])
                        if identity is not None:
                            chat = ensure_ai_conversation_title(current_ai_service, ai_authority, chat)
                            sessions.append({
                                **self._public_ai_chat(chat),
                                "contextTitle": chat["contextTitle"],
                                "createdAt": identity.get("createdAt"),
                                "updatedAt": identity.get("updatedAt"),
                            })
                    return {"sessions": sessions}
                chat = self._ai_chat(session_id)
                if chat["dashboardId"] != dashboard_id or chat["accessLevel"] != access_level or chat["target"] != target:
                    raise OpenCodeServiceError(409, "session_context_changed", "The AI conversation belongs to a different dashboard, disclosure level, or data target")
                result = current_ai_service.session_messages(chat["externalSessionId"])
                pending = []
                for proposal in ai_authority.pending_proposals(session_id):
                    operation_record = ai_authority.operation_for_proposal(proposal["id"], session_id)
                    if operation_record is None or operation_record["state"] in {"running", "uncertain"} or (
                        operation_record["state"] == "cancelled" and operation_record.get("cancellationRequested")
                    ):
                        pending.append({"proposalId": proposal["id"], "sessionId": session_id, "action": proposal["action"], "policyBinding": proposal["policyBinding"], "operation": operation_record, "cancellationRequested": proposal.get("cancellationRequested", False)})
                return {**result, "pendingProposals": pending}

            return self._ai_call(history)

        def _ai_proposal(self, current_ai_service, session_id: str, proposal_id: str, operation: str, body: dict):
            if operation == "reconcile":
                def reconcile():
                    chat = self._ai_chat(session_id)
                    current = ai_authority.operation_for_proposal(proposal_id, session_id)
                    if current is None:
                        raise MetadataStoreError("operation_not_started", "Proposal operation has not started", status=404)
                    if current["state"] != "uncertain": return {"operation": current}
                    proposal = ai_authority.proposal(proposal_id, session_id)
                    return ai_executor.reconcile(chat, current, proposal)
                return authority_call(self, reconcile)
            if operation == "execute":
                return self._ai_execute_proposal(current_ai_service, session_id, proposal_id, body)
            return self.send_json(404, {"error": "Unknown API path"})

        def _ai_operation_status(self, current_ai_service, session_id: str, operation_id: str):
            return authority_call(self, lambda: {"operation": ai_authority.operation(operation_id, session_id)})

        def _ai_cancel_proposal(self, current_ai_service, session_id: str, proposal_id: str):
            def cancel():
                cancellation = ai_authority.request_query_cancellation(proposal_id, session_id)
                operation_id = cancellation.get("operationId")
                if cancellation.get("requested") and operation_id and cancellation.get("operationState") == "running":
                    service.cancel_read_only_sql(operation_id)
                operation = None if operation_id is None else ai_authority.operation(operation_id, session_id)
                if operation is not None and operation["state"] != "running":
                    service.release_read_only_sql(operation_id)
                return {"cancellation": cancellation, "operation": operation}
            return authority_call(self, cancel)

        def _ai_execute_proposal(self, current_ai_service, session_id: str, proposal_id: str, body: dict):
            if set(body) != {"confirmation"}:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Proposal execution fields are invalid"}})
            try:
                chat = self._ai_chat(session_id)
                proposal_record = ai_authority.proposal(proposal_id, session_id)
                if chat.get("policySnapshot") is None and proposal_record["policyBinding"] != ai_authority.policy_binding(chat, proposal_record["action"]):
                    raise MetadataStoreError("chat_policy_changed", "Proposal policy no longer matches this chat", status=409)
                operation, approval = ai_authority.authorize_and_claim(
                    proposal_id, session_id, proposal_record["policyBinding"]["policyRevision"], body.get("confirmation"),
                )
            except (MetadataStoreError, OpenCodeServiceError, DashboardStoreError) as error:
                payload = error.payload if hasattr(error, "payload") else error.to_dict()
                return self.send_json(error.status, payload)
            dashboard_id = chat["dashboardId"]
            access = chat["accessLevel"]
            record = dashboard_store.get(dashboard_id)
            schema_concurrency = {"revision": record["revision"]}
            authorization_target = dict(chat["target"])
            profile = None
            if access == "data":
                profile = next((item for item in service.list_profiles() if item.get("id") == authorization_target.get("profileId")), None)
                if profile is None or profile.get("dbname") != authorization_target.get("database"):
                    return self.send_json(409, {"error": {"code": "database_changed", "message": "The PostgreSQL target changed"}})
            execution_owner = operation.pop("executionOwner", False)
            if not execution_owner:
                return self.send_json(200, {"operation": operation, "approval": approval})
            action = proposal_record["action"]
            attempt_id = operation.pop("attemptId")
            claim_token = operation.pop("claimToken")
            if ai_maintenance is not None:
                ai_maintenance.track(operation["id"], attempt_id, claim_token)

            def finish_claim(state, *, result=None, error=None):
                try:
                    if ai_maintenance is not None:
                        ai_maintenance.assert_owned(attempt_id)
                    return ai_authority.finish_operation(attempt_id, claim_token, state, result=result, error=error), False
                except OperationLeaseLost:
                    return ai_authority.operation(operation["id"], session_id), True
                except MetadataStoreError as failure:
                    if failure.code not in {"invalid_claim", "operation_not_running", "operation_lease_expired"}:
                        raise
                    return ai_authority.operation(operation["id"], session_id), True
                finally:
                    if ai_maintenance is not None:
                        ai_maintenance.release(attempt_id)
                    if action.get("type") == "read_query":
                        service.release_read_only_sql(operation["id"])
            try:
                if proposal_record["schemaConcurrency"] != schema_concurrency or proposal_record["authorizationTarget"] != authorization_target:
                    raise DashboardStoreError(409, "dashboard_changed", "Proposal authority binding changed; request a fresh proposal")
                result = ai_executor.execute(
                    action, operation["id"], chat=chat, record=record, profile=profile,
                    schema_concurrency=schema_concurrency, authorization_target=authorization_target,
                    policy_binding=proposal_record["policyBinding"],
                )
            except (OpenCodeServiceError, DashboardStoreError, PostgresServiceError, MetadataStoreError) as error:
                payload = error.payload if hasattr(error, "payload") else error.to_dict()
                uncertain = isinstance(error, DashboardStoreError) and error.status >= 500
                finished, lost = finish_claim("uncertain" if uncertain else "failed", error=payload["error"])
                return self.send_json(409 if lost else getattr(error, "status", 400), {"operation": finished, "approval": approval})
            except Exception:
                finished, lost = finish_claim("uncertain",
                    error={"code": "execution_outcome_unknown", "message": "Operation outcome is uncertain; reload authoritative state"},
                )
                return self.send_json(409 if lost else 500, {"operation": finished, "approval": approval})
            finished, lost = finish_claim("succeeded", result=result)
            return self.send_json(409 if lost else 200, {"operation": finished, "approval": approval})

        def do_PUT(self):
            path = urlparse(self.path).path
            if ai_router.handle_put(self, path):
                return
            dashboard_id = self._dashboard_id(path)
            if dashboard_id is not None:
                if not self._authorize_dashboard():
                    return
                body = self._body_or_error()
                if body is not None:
                    self._dashboard_call(lambda: dashboard_store.save(dashboard_id, body))
                return
            if not self._handle_postgres_put(path):
                self.send_json(404, {"error": {"code": "not_found", "message": "Unknown API path"}})

        def do_DELETE(self):
            path = urlparse(self.path).path
            if ai_router.handle_delete(self, path):
                return
            dashboard_id = self._dashboard_id(path)
            if dashboard_id is not None:
                if self._authorize_dashboard():
                    body = self._body_or_error()
                    if body is not None:
                        if set(body) != {"expectedRevision"}:
                            return self.send_json(400, {"error": {"code": "invalid_dashboard_binding", "message": "Dashboard deletion fields are invalid"}})
                        self._dashboard_call(lambda: dashboard_store.delete(dashboard_id, body["expectedRevision"]))
                return
            if not self._handle_postgres_delete(path):
                self.send_json(404, {"error": {"code": "not_found", "message": "Unknown API path"}})

    return SchemerHandler


def main() -> None:
    web_dir, config_dir, dashboard_dir = _paths()
    host = os.environ.get("SCHEMER_HOST", "127.0.0.1")
    behind_loopback_proxy = parse_proxy_setting(
        os.environ.get("SCHEMER_BEHIND_LOOPBACK_PROXY", "0"), "SCHEMER_BEHIND_LOOPBACK_PROXY",
    )
    port = parse_port(os.environ.get("SCHEMER_PORT", "8081"), "SCHEMER_PORT")
    try:
        ai_timeout = float(os.environ.get("SCHEMER_OPENCODE_TIMEOUT", "120"))
    except ValueError as exc:
        raise SystemExit("SCHEMER_OPENCODE_TIMEOUT must be a number") from exc
    if not 1 <= ai_timeout <= 300:
        raise SystemExit("SCHEMER_OPENCODE_TIMEOUT must be from 1 to 300 seconds")
    validate_static_directory(web_dir)
    postgres_config = postgres_runtime_config(os.environ)
    service = PostgresService(
        config_dir, application_name="schemer",
        plan_ttl_seconds=postgres_config.migration_plan_ttl_seconds,
        temporal_manifest_ttl_seconds=postgres_config.temporal_manifest_ttl_seconds,
        console_transaction_maximum=postgres_config.console_transaction_maximum,
        console_transaction_idle_seconds=postgres_config.console_transaction_idle_seconds,
        console_transaction_lifetime_seconds=postgres_config.console_transaction_lifetime_seconds,
        execution_controller=PostgresExecutionController(
            postgres_config.class_capacities, global_capacity=postgres_config.global_capacity,
            target_capacity=postgres_config.target_capacity,
        ),
    )
    dashboard_store = DashboardStore(dashboard_dir)
    try:
        metadata_config = MetadataConfig.from_env()
        metadata_store = MetadataStore(
            MetadataConnectionFactory(metadata_config), max_json_bytes=metadata_config.max_json_bytes,
        )
        metadata_store.health()
    except (ValueError, MetadataStoreError) as error:
        raise SystemExit(f"Schemer metadata readiness failed: {error}") from error
    try:
        maintenance_config = AiOperationMaintenanceConfig.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from error
    ai_maintenance = AiOperationMaintenance(metadata_store, maintenance_config)
    service.set_metadata_store(metadata_store)
    retire_legacy_schemer_authority(config_dir)
    try:
        mercury_template = mercury_dashboard_from_service(service)
    except PostgresServiceError:
        mercury_template = None
    dashboard_store.initialize_once(mercury_template)
    if mercury_template is not None:
        dashboard_store.upgrade_mercury_example(mercury_template)
    try:
        opencode_password = read_secret_file(
            os.environ.get("SCHEMER_OPENCODE_PASSWORD_FILE", ""), "SCHEMER_OPENCODE_PASSWORD_FILE",
        ) or os.environ.get("SCHEMER_OPENCODE_PASSWORD", "")
    except ValueError as error:
        raise SystemExit(str(error)) from error
    ai_service = OpenCodeService(
        os.environ.get("SCHEMER_OPENCODE_URL", ""),
        os.environ.get("SCHEMER_OPENCODE_USERNAME", "opencode"),
        opencode_password,
        ai_timeout,
        workspace="/workspace-schemer",
        custom_tools=set(SCHEMER_AI_TOOL_ACTION_TYPES),
        tool_action_types=SCHEMER_AI_TOOL_ACTION_TYPES,
        safe_skills=SCHEMER_AI_SKILLS,
        data_tools={"schemer_read_query"},
    )
    server_id = secrets.token_urlsafe(18)
    handler = make_handler(
        web_dir,
        service,
        dashboard_store,
        secrets.token_urlsafe(32),
        server_id=server_id,
        ai_authority=SchemerMetadataAuthority(metadata_store, worker_id=f"schemer-{server_id}", lease_seconds=maintenance_config.lease_seconds),
        ai_maintenance=ai_maintenance,
        dependency_schema_store=SchemaStore(
            Path(os.environ.get("SCHEMII_SCHEMA_DIR", "~/.local/share/schemii/schemas")).expanduser().resolve(),
            read_only=True,
        ),
        ai_service=ai_service,
        behind_loopback_proxy=behind_loopback_proxy,
    )
    run_server(host, port, handler, "Schemer", server_factory=ThreadingHTTPServer, shutdown_callback=service.close, lifecycle_services=(ai_maintenance,))


if __name__ == "__main__":
    main()
