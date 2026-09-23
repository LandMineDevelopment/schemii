"""Authorize a saved dashboard without borrowing its author's execution identity.

An execution keeps the viewer as its owner. Only the explicitly granted source
profile is resolved as another owner, through a bounded internal capability.
"""
from contextlib import contextmanager
from types import SimpleNamespace

from schemii.common.api.errors import ApiProblem
from schemii.schemoo.store import ModelNotFoundError
from .dashboard_store import DashboardNotFoundError


def _enabled(request):
    auth = getattr(request.app.state, "auth", None)
    return auth if auth is not None and auth.enabled else None


def available_dashboards(request, actor):
    services = request.app.state.services
    auth = _enabled(request)
    author = auth is None or {"schemer:access", "schemer:author"}.issubset(auth.capabilities(actor))
    dashboards = {item.id: item for item in services.dashboards.list(actor)} if author else {}
    if auth:
        for grant in auth.dashboard_grants(actor):
            try:
                item = services.dashboards.get(grant["owner_id"], grant["dashboard_id"])
            except DashboardNotFoundError:
                continue
            dashboards.setdefault(item.id, item)
    return list(dashboards.values())


class ReportAccess:
    def __init__(self, services, auth, actor, grant, dashboard, model, profile, session_token=None):
        self.services, self.auth, self.actor = services, auth, actor
        self.grant, self.dashboard, self.model, self.profile = grant, dashboard, model, profile
        self.session_token = session_token
        self.resource_id = dashboard.id
        self.connection_owner_id = grant["connection_owner_id"]

    def check(self):
        user = self.auth.resolve(self.session_token) if self.session_token is not None else self.auth.user(self.actor)
        if not user or user.get("disabled"):
            raise ApiProblem(403, "report_access_revoked", "Your report access has been removed.")
        if "schemer:access" not in self.auth.capabilities(self.actor):
            raise ApiProblem(403, "report_access_revoked", "Your Schemer access has been removed.")
        if self.grant not in self.auth.dashboard_grants(self.actor):
            raise ApiProblem(403, "report_access_revoked", "Your report access changed. Reload the report.")
        matches = [item for item in self.auth.connection_grants(self.actor)
                   if item["connection_id"] == self.grant["connection_id"]
                   and item["owner_id"] == self.grant["connection_owner_id"]
                   and item.get("role_id") == self.grant.get("role_id")]
        if not matches:
            raise ApiProblem(403, "report_access_revoked", "The role no longer has access to this database.")
        current = self.services.connections.get(self.grant["connection_owner_id"], self.grant["connection_id"])
        if current.revision != self.profile.revision:
            raise ApiProblem(409, "report_connection_changed", "The report connection changed. Reload the report.")
        current_dashboard = self.services.dashboards.get(self.grant["owner_id"], self.dashboard.id)
        if current_dashboard.revision != self.dashboard.revision:
            raise ApiProblem(409, "dashboard_revision_conflict", "The dashboard changed. Reload the report.")

    def _validate(self, actor, connection_id):
        if actor != self.actor or connection_id != self.profile.id:
            raise ApiProblem(403, "report_source_forbidden", "This report cannot use that database connection.")
        self.check()

    def get(self, actor, connection_id):
        self._validate(actor, connection_id)
        return self.profile

    @contextmanager
    def use(self, actor, connection_id):
        self._validate(actor, connection_id)
        with self.services.connections.use(self.grant["connection_owner_id"], connection_id) as target:
            if target.revision != self.profile.revision:
                raise ApiProblem(409, "report_connection_changed", "The report connection changed. Reload the report.")
            yield target


class ReportModels:
    def __init__(self, access):
        self.access = access

    def get(self, actor, model_id):
        access = self.access
        access.check()
        if actor != access.actor or model_id != access.model.id:
            raise ModelNotFoundError("The model was not found")
        current = access.services.models.get(access.grant["owner_id"], model_id)
        return current.model_copy(update={"connection_id": access.profile.id})


class ReportConsole:
    def __init__(self, console, access):
        self.console, self.access = console, access

    def reserve_read_target(self, owner, **kwargs):
        self.access.check()
        self.access.auth.audit(owner, "report.execute", f"{self.access.resource_id}:{kwargs.get('connection_id', '')}")
        return self.console.reserve_read_target(owner, connection_access=self.access, **kwargs)

    def run(self, owner, execution_id):
        self.access.check()
        return self.console.run(owner, execution_id, connection_access=self.access)

    def page(self, *args, **kwargs):
        self.access.check()
        result = self.console.page(*args, **kwargs)
        self.access.check()
        return result

    def __getattr__(self, name):
        # Cleanup remains possible even when a grant is revoked.
        return getattr(self.console, name)


class ReportCatalogs:
    def __init__(self, base, access):
        self.base, self.access = base, access

    def get(self, services, actor, connection_id, namespace, *, fresh=False):
        # Database GRANT changes do not change a saved profile's revision.
        # Always refresh shared catalogs and intersect with current privileges.
        catalog = self.base.get(services, actor, connection_id, namespace, fresh=True)
        with self.access.use(actor, connection_id) as target:
            readable = services.postgres.readable_columns(target, namespace)
        tables = []
        for table in catalog["tables"]:
            columns = [c for c in table["columns"] if (table["name"], c["name"]) in readable]
            if columns:
                table["columns"] = columns
                table["primaryKey"] = table.get("primaryKey", []) if all((table["name"], c) in readable for c in table.get("primaryKey", [])) else []
                table["uniqueKeys"] = [key for key in table.get("uniqueKeys", [])
                                       if all((table["name"], c) in readable for c in key)]
                tables.append(table)
        catalog["tables"] = tables
        catalog["relationships"] = [r for r in catalog["relationships"]
            if (r["sourceTable"], r["sourceColumn"]) in readable
            and (r["targetTable"], r["targetColumn"]) in readable]
        return catalog


def prepare_dashboard(request, actor, dashboard_id, *, export=False, drill=False):
    services, auth = request.app.state.services, _enabled(request)
    try:
        dashboard = services.dashboards.get(actor, dashboard_id)
    except DashboardNotFoundError:
        if auth is None:
            raise
    else:
        if auth is None or {"schemer:access", "schemer:author"}.issubset(auth.capabilities(actor)):
            return dashboard, owned_report_services(request, actor, dashboard.id), {"edit": True, "export": True, "drill": True}
    grants = [g for g in auth.dashboard_grants(actor) if g["dashboard_id"] == dashboard_id]
    if not grants:
        raise DashboardNotFoundError("The dashboard was not found")
    # Ambiguous identities cannot be combined or silently ranked by privilege.
    identities = {(g["connection_owner_id"], g["connection_id"]) for g in grants}
    if len(identities) != 1:
        raise ApiProblem(409, "report_access_ambiguous", "Your roles assign different connections to this report. Ask an administrator to choose one.")
    eligible = [g for g in grants if (not export or g["can_export"]) and (not drill or g["can_drill"])]
    if not eligible:
        raise ApiProblem(403, "report_action_forbidden", "Your role does not allow this report action.")
    grant = eligible[0]
    dashboard = services.dashboards.get(grant["owner_id"], dashboard_id)
    model = services.models.get(grant["owner_id"], dashboard.model_id)
    source = services.connections.get(model.connection_owner_id or grant["owner_id"], model.connection_id)
    profile = services.connections.get(grant["connection_owner_id"], grant["connection_id"])
    if (source.host, source.port, source.database) != (profile.host, profile.port, profile.database):
        raise ApiProblem(409, "report_source_mismatch", "The assigned connection must target the report's database server and database.")
    access = ReportAccess(services, auth, actor, grant, dashboard, model, profile,
                          getattr(request, "cookies", {}).get("schemii_session"))
    access.check()
    scoped = SimpleNamespace(**vars(services))
    scoped.models = ReportModels(access)
    scoped.connections = access
    scoped.console = ReportConsole(services.console, access)
    scoped.model_catalogs = ReportCatalogs(services.model_catalogs, access)
    scoped.report_access = access
    return dashboard, scoped, {"edit": False, "export": any(g["can_export"] for g in grants),
                               "drill": any(g["can_drill"] for g in grants)}


class OwnerReportAccess:
    def __init__(self, services, auth, actor, resource_id, token):
        self.services, self.auth, self.actor = services, auth, actor
        self.resource_id, self.token = resource_id, token
        self.connection_owner_id = actor
        self.connections = services.connections.for_product("schemer")

    def check(self):
        user = self.auth.resolve(self.token)
        if not user or not {"schemer:access", "schemer:author"}.issubset(self.auth.capabilities(self.actor)):
            raise ApiProblem(403, "report_access_revoked", "Your report access has been removed. Sign in again or contact an administrator.")

    def get(self, actor, connection_id):
        self.check()
        if actor != self.actor:
            raise ApiProblem(403, "report_source_forbidden", "This connection belongs to another account.")
        profile = self.connections.get(actor, connection_id)
        self.connection_owner_id = profile.owner_id or actor
        return profile

    @contextmanager
    def use(self, actor, connection_id):
        self.get(actor, connection_id)
        with self.connections.use(actor, connection_id) as target:
            yield target


def owned_report_services(request, actor, resource_id):
    services, auth = request.app.state.services, _enabled(request)
    if auth is None:
        return services
    access = OwnerReportAccess(services, auth, actor, resource_id, request.cookies.get('schemii_session'))
    access.check()
    scoped = SimpleNamespace(**vars(services))
    scoped.connections = access
    scoped.console = ReportConsole(services.console, access)
    scoped.report_access = access
    return scoped
