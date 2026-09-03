"""Source-derived live relation browsing for an owner-bound workspace."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from schemii.common.connections.service import ConnectionService
from schemii.common.postgres import PostgresGateway
from schemii.common.postgres.models import (
    PostgresCatalog,
    PostgresMaterializedView,
    PostgresTable,
    PostgresView,
)
from schemii.common.postgres.query_analysis import analyze_query_definition
from schemii.common.postgres.query_models import QueryAnalysis
from schemii.schemii.workspaces.models import SchemiiWorkspace
from schemii.schemii.workspaces.store import WorkspaceRepository

from .models import (
    RelationDetailResponse,
    RelationLineageEdge,
    RelationLineageNode,
    RelationLineageResponse,
    RelationListResponse,
    RelationRowPage,
    RelationSummary,
)


LiveRelation = PostgresTable | PostgresView | PostgresMaterializedView


class RelationBrowserError(ValueError):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _ResolvedRelation:
    ref: str
    fingerprint: str
    kind: str
    value: LiveRelation


class RelationBrowserService:
    """Resolve browser state from fresh PostgreSQL catalog evidence."""

    def __init__(
        self,
        *,
        workspaces: WorkspaceRepository,
        connections: ConnectionService,
        postgres: PostgresGateway,
    ) -> None:
        self._workspaces = workspaces
        self._connections = connections
        self._postgres = postgres

    @staticmethod
    def _digest(value: Any) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _relation_fingerprint(cls, relation: LiveRelation) -> str:
        return cls._digest(relation.model_dump(mode="json"))

    @classmethod
    def _ref(cls, workspace_id: str, catalog: PostgresCatalog, kind: str, name: str) -> str:
        digest = cls._digest([workspace_id, catalog.fingerprint, kind, name])
        return f"rel_{digest[:32]}"

    @staticmethod
    def _kind(relation: LiveRelation) -> str:
        if isinstance(relation, PostgresMaterializedView):
            return "materialized_view"
        if isinstance(relation, PostgresView):
            return "view"
        return relation.kind

    @classmethod
    def _relations(cls, workspace_id: str, catalog: PostgresCatalog) -> list[_ResolvedRelation]:
        values: list[LiveRelation] = [
            *catalog.tables,
            *catalog.views,
            *catalog.materialized_views,
        ]
        resolved = []
        for value in values:
            kind = cls._kind(value)
            resolved.append(
                _ResolvedRelation(
                    ref=cls._ref(workspace_id, catalog, kind, value.name),
                    fingerprint=cls._relation_fingerprint(value),
                    kind=kind,
                    value=value,
                )
            )
        order = {"table": 0, "partitioned_table": 0, "view": 1, "materialized_view": 2}
        return sorted(resolved, key=lambda item: (order.get(item.kind, 9), item.value.name.casefold()))

    def _catalog(self, owner_id: str, workspace_id: str) -> tuple[SchemiiWorkspace, PostgresCatalog]:
        workspace = self._workspaces.get(owner_id, workspace_id)
        if workspace.connection_id is None or workspace.database is None or workspace.namespace is None:
            raise RelationBrowserError(409, "workspace_target_required", "Live relation browsing is available only in database-derived or live workspaces")
        with self._connections.use(owner_id, workspace.connection_id) as connection:
            if connection.database != workspace.database:
                raise RelationBrowserError(409, "workspace_target_changed", "The workspace connection no longer targets its saved database")
            return workspace, self._postgres.introspect(connection, workspace.namespace)

    @staticmethod
    def _cursor(offset: int, fingerprint: str, search: str) -> str:
        raw = json.dumps({"o": offset, "f": fingerprint, "q": search}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _read_cursor(cursor: str | None, fingerprint: str, search: str) -> int:
        if cursor is None:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode())
            if value.get("f") != fingerprint or value.get("q") != search:
                raise ValueError
            offset = value["o"]
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 100_000:
                raise ValueError
            return offset
        except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise RelationBrowserError(409, "relation_cursor_stale", "The relation page changed; restart browsing from the first page") from None

    def list(self, owner_id: str, workspace_id: str, *, cursor: str | None, page_size: int, search: str | None) -> RelationListResponse:
        _, catalog = self._catalog(owner_id, workspace_id)
        normalized = (search or "").strip().casefold()
        items = self._relations(workspace_id, catalog)
        if normalized:
            items = [item for item in items if normalized in item.value.name.casefold() or normalized in item.kind.replace("_", " ")]
        offset = self._read_cursor(cursor, catalog.fingerprint, normalized)
        page = items[offset:offset + page_size]
        next_offset = offset + len(page)
        return RelationListResponse(
            workspace_id=workspace_id,
            catalog_fingerprint=catalog.fingerprint,
            relations=[RelationSummary(ref=item.ref, name=item.value.name, kind=item.kind, column_count=len(item.value.columns), fingerprint=item.fingerprint) for item in page],
            next_cursor=self._cursor(next_offset, catalog.fingerprint, normalized) if next_offset < len(items) else None,
        )

    def _resolve(self, owner_id: str, workspace_id: str, relation_ref: str) -> tuple[SchemiiWorkspace, PostgresCatalog, _ResolvedRelation]:
        workspace, catalog = self._catalog(owner_id, workspace_id)
        relation = next((item for item in self._relations(workspace_id, catalog) if item.ref == relation_ref), None)
        if relation is None:
            raise RelationBrowserError(404, "relation_not_found", "The relation was not found in the current PostgreSQL catalog")
        return workspace, catalog, relation

    def detail(self, owner_id: str, workspace_id: str, relation_ref: str) -> RelationDetailResponse:
        _, catalog, relation = self._resolve(owner_id, workspace_id, relation_ref)
        return RelationDetailResponse(workspace_id=workspace_id, catalog_fingerprint=catalog.fingerprint, relation_ref=relation.ref, relation=relation.value)

    @staticmethod
    def _analysis_relations(catalog: PostgresCatalog) -> list[dict[str, Any]]:
        values: list[LiveRelation] = [*catalog.tables, *catalog.views, *catalog.materialized_views]
        return [{"namespace": item.namespace, "name": item.name, "kind": RelationBrowserService._kind(item), "columns": [{"name": column.name, "data_type": column.data_type} for column in item.columns]} for item in values]

    def lineage(self, owner_id: str, workspace_id: str, relation_ref: str) -> RelationLineageResponse:
        _, catalog, relation = self._resolve(owner_id, workspace_id, relation_ref)
        nodes = [RelationLineageNode(id="selected", kind="relation", label=relation.value.name, relation_ref=relation.ref)]
        edges: list[RelationLineageEdge] = []
        analysis = None
        warnings: list[str] = []
        if isinstance(relation.value, (PostgresView, PostgresMaterializedView)):
            analysis = QueryAnalysis.model_validate(analyze_query_definition(relation.value.query_definition, self._analysis_relations(catalog), current_namespace=catalog.namespace))
            warnings.extend(analysis.warnings)
            for index, source in enumerate(analysis.sources):
                node_id = f"source-{index}"
                match = next((item for item in self._relations(workspace_id, catalog) if item.value.name == source.name), None)
                nodes.append(RelationLineageNode(id=node_id, kind="relation", label=source.name, relation_ref=match.ref if match else None))
                edges.append(RelationLineageEdge(source_id=node_id, target_id="selected", classification="dependency", verified=source.resolved))
        elif isinstance(relation.value, PostgresTable):
            relevant = [fk for fk in catalog.relationships if fk.source_table == relation.value.name or fk.target_table == relation.value.name]
            for index, fk in enumerate(relevant):
                other = fk.target_table if fk.source_table == relation.value.name else fk.source_table
                match = next((item for item in self._relations(workspace_id, catalog) if item.value.name == other), None)
                node_id = f"relation-{index}"
                nodes.append(RelationLineageNode(id=node_id, kind="relation", label=other, relation_ref=match.ref if match else None))
                source_id, target_id = ("selected", node_id) if fk.source_table == relation.value.name else (node_id, "selected")
                edges.append(RelationLineageEdge(source_id=source_id, target_id=target_id, classification="dependency", verified=True))
        return RelationLineageResponse(workspace_id=workspace_id, relation_ref=relation.ref, relation_fingerprint=relation.fingerprint, complete=not warnings, warnings=warnings, nodes=nodes, edges=edges, analysis=analysis)

    def rows(self, owner_id: str, workspace_id: str, relation_ref: str, *, cursor: str | None, page_size: int) -> RelationRowPage:
        workspace, _, relation = self._resolve(owner_id, workspace_id, relation_ref)
        offset = self._read_cursor(cursor, relation.fingerprint, "rows")
        assert workspace.connection_id is not None and workspace.namespace is not None
        quote = lambda value: '"' + value.replace('"', '""') + '"'
        statement = f"SELECT * FROM {quote(workspace.namespace)}.{quote(relation.value.name)} OFFSET {offset} LIMIT {page_size + 1}"
        with self._connections.use(owner_id, workspace.connection_id) as connection:
            results = self._postgres.execute_console(connection, (statement,), on_started=lambda _pid: True)
        result = results[0]
        rows = list(result.rows[:page_size])
        has_more = len(result.rows) > page_size or result.truncated
        return RelationRowPage(workspace_id=workspace_id, relation_ref=relation.ref, relation_fingerprint=relation.fingerprint, columns=list(result.columns), rows=[list(row) for row in rows], next_cursor=self._cursor(offset + len(rows), relation.fingerprint, "rows") if has_more else None, truncated=has_more)
