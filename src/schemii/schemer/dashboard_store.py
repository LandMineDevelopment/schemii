"""Owner-scoped dashboard repositories with optimistic concurrency."""
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
import json
import secrets
from psycopg.types.json import Jsonb
from schemii.common.errors import MetadataStorageUnavailableError
from .dashboard_models import Dashboard, DashboardCreate, DashboardUpdate


class DashboardNotFoundError(RuntimeError):
    pass


class DashboardConflictError(RuntimeError):
    def __init__(self, revision):
        self.current_revision = revision
        super().__init__("This dashboard changed. Reload before saving or deleting it.")


class DashboardLimitError(RuntimeError):
    pass


class DashboardStorageUnavailableError(MetadataStorageUnavailableError):
    pass


def _check(request):
    if len(json.dumps(request.model_dump(mode="json")).encode()) > 1_048_576:
        raise DashboardLimitError("Dashboard configuration exceeds 1 MiB")


def _update(current, request):
    if current.revision != request.expected_revision:
        raise DashboardConflictError(current.revision)
    if current.model_id != request.model_id:
        raise ValueError("A dashboard cannot switch models. Create a new dashboard instead.")
    return Dashboard.model_validate({**current.model_dump(),
        **request.model_dump(exclude={"expected_revision"}),
        "revision": current.revision + 1, "updated_at": datetime.now(timezone.utc)})


class InMemoryDashboardRepository:
    def __init__(self):
        self._records = {}
        self._lock = RLock()

    def list(self, owner):
        with self._lock:
            return [v.model_copy(deep=True) for (o, _), v in self._records.items() if o == owner]

    def get(self, owner, dashboard_id):
        with self._lock:
            if (owner, dashboard_id) not in self._records:
                raise DashboardNotFoundError("Dashboard was not found")
            return self._records[owner, dashboard_id].model_copy(deep=True)

    def create(self, owner, request):
        request = DashboardCreate.model_validate(request)
        _check(request)
        with self._lock:
            if len(self.list(owner)) >= 100:
                raise DashboardLimitError("Dashboard limit of 100 reached")
            now = datetime.now(timezone.utc)
            dashboard = Dashboard(**request.model_dump(), id=f"dashboard_{secrets.token_hex(16)}",
                                  owner_id=owner, revision=1, created_at=now, updated_at=now)
            self._records[owner, dashboard.id] = dashboard.model_copy(deep=True)
            return dashboard

    def update(self, owner, dashboard_id, request):
        request = DashboardUpdate.model_validate(request)
        _check(request)
        with self._lock:
            updated = _update(self.get(owner, dashboard_id), request)
            self._records[owner, dashboard_id] = updated.model_copy(deep=True)
            return updated

    def delete(self, owner, dashboard_id, expected_revision):
        with self._lock:
            current = self.get(owner, dashboard_id)
            if current.revision != expected_revision:
                raise DashboardConflictError(current.revision)
            del self._records[owner, dashboard_id]


class PostgresDashboardRepository:
    def __init__(self, connection_factory):
        self._factory = connection_factory

    @contextmanager
    def _transaction(self):
        try:
            with self._factory() as connection:
                with connection.cursor() as cursor:
                    yield cursor
        except (DashboardNotFoundError, DashboardConflictError, DashboardLimitError, ValueError):
            raise
        except Exception as error:
            raise DashboardStorageUnavailableError("Dashboard storage is temporarily unavailable") from error

    @staticmethod
    def _row(cursor, owner, dashboard_id, lock=False):
        cursor.execute("SELECT * FROM schemer.dashboards WHERE owner_id=%s AND id=%s" +
                       (" FOR UPDATE" if lock else ""), (owner, dashboard_id))
        row = cursor.fetchone()
        if row is None:
            raise DashboardNotFoundError("Dashboard was not found")
        return Dashboard.model_validate(row)

    def list(self, owner):
        with self._transaction() as cursor:
            cursor.execute("SELECT * FROM schemer.dashboards WHERE owner_id=%s ORDER BY created_at,id", (owner,))
            return [Dashboard.model_validate(row) for row in cursor.fetchall()]

    def get(self, owner, dashboard_id):
        with self._transaction() as cursor:
            return self._row(cursor, owner, dashboard_id)

    def create(self, owner, request):
        request = DashboardCreate.model_validate(request)
        _check(request)
        with self._transaction() as cursor:
            cursor.execute("SELECT id FROM metadata.users WHERE id=%s FOR UPDATE", (owner,))
            cursor.execute("SELECT count(*) AS count FROM schemer.dashboards WHERE owner_id=%s", (owner,))
            if cursor.fetchone()["count"] >= 100:
                raise DashboardLimitError("Dashboard limit of 100 reached")
            cursor.execute("""INSERT INTO schemer.dashboards
                (id,owner_id,name,model_id,model_revision,optional_filters,selections,tiles)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (f"dashboard_{secrets.token_hex(16)}", owner, request.name, request.model_id,
                 request.model_revision, Jsonb(request.optional_filters),
                 Jsonb({k:v.model_dump(mode="json") for k,v in request.selections.items()}),
                 Jsonb([t.model_dump(mode="json") for t in request.tiles])))
            return Dashboard.model_validate(cursor.fetchone())

    def update(self, owner, dashboard_id, request):
        request = DashboardUpdate.model_validate(request)
        _check(request)
        with self._transaction() as cursor:
            updated = _update(self._row(cursor, owner, dashboard_id, True), request)
            cursor.execute("""UPDATE schemer.dashboards SET name=%s,model_revision=%s,optional_filters=%s,
                selections=%s,tiles=%s,revision=%s,updated_at=%s WHERE owner_id=%s AND id=%s RETURNING *""",
                (updated.name, updated.model_revision, Jsonb(updated.optional_filters),
                 Jsonb({k:v.model_dump(mode="json") for k,v in updated.selections.items()}),
                 Jsonb([t.model_dump(mode="json") for t in updated.tiles]), updated.revision,
                 updated.updated_at, owner, dashboard_id))
            return Dashboard.model_validate(cursor.fetchone())

    def delete(self, owner, dashboard_id, expected_revision):
        with self._transaction() as cursor:
            current = self._row(cursor, owner, dashboard_id, True)
            if current.revision != expected_revision:
                raise DashboardConflictError(current.revision)
            cursor.execute("DELETE FROM schemer.dashboards WHERE owner_id=%s AND id=%s", (owner, dashboard_id))
