import re
from importlib.resources import files

from fastapi.testclient import TestClient

from schemii.main import create_app


def test_frontend_is_served_with_browser_security_and_cache_headers() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert '<script type="module" src="assets/app.js"></script>' in response.text
    assert 'href="assets/ui.css"' in response.text
    assert 'href="/assets/common/query-story.css"' in response.text
    assert 'href="/assets/common/transient-cue.css"' in response.text
    assert 'data-ui-icon="database"' in response.text
    assert 'id="table-inspector-toggle"' in response.text
    assert 'href="/api-map"' in response.text
    assert 'href="/db-map"' in response.text
    assert "<script>" not in response.text

    head = api.head("/")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"].startswith("text/html")


def test_index_access_method_uses_the_complete_styled_selector() -> None:
    index = (
        files("schemii.schemii")
        .joinpath("web", "index.html")
        .read_text(encoding="utf-8")
    )
    match = re.search(
        r'<select class="design-select" id="design-index-method"[^>]*>(.*?)</select>',
        index,
    )

    assert match is not None
    assert re.findall(r'<option value="([^"]+)">([^<]+)</option>', match.group(1)) == [
        ("btree", "B-tree · default"),
        ("hash", "Hash"),
        ("gist", "GiST"),
        ("spgist", "SP-GiST"),
        ("gin", "GIN"),
        ("brin", "BRIN"),
    ]
    assert "postgres-index-methods" not in index


def test_designed_tables_are_edited_directly_in_the_table_inspector() -> None:
    web = files("schemii.schemii").joinpath("web")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    source = web.joinpath("assets", "app.js").read_text(encoding="utf-8")
    styles = web.joinpath("assets", "app.css").read_text(encoding="utf-8")

    inspector = index[index.index('<aside class="inspector'):index.index("</aside>", index.index('<aside class="inspector'))]
    assert 'id="inspector-table-form"' in inspector
    assert 'id="inspector-table-name"' in inspector
    assert 'id="inspector-design-columns"' in inspector
    assert 'id="save-inspector-table-button"' in inspector
    assert 'id="edit-table-button"' not in index
    assert "submitInspectorTable" in source
    assert "designColumnValues(elements.inspectorDesignColumns)" in source
    assert "showTableDetails: !desired" in source
    assert ".inspector-table-savebar { position: sticky" in styles
    assert ".inspector.is-editable" in styles


def test_api_route_opens_the_unified_system_map_with_its_api_lens() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/api-map")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert '<script type="module" src="/assets/system-map.js"></script>' in response.text
    assert 'href="/assets/ui.css"' in response.text
    assert 'href="/assets/api-map.css"' in response.text
    assert 'href="/assets/system-map.css"' in response.text
    assert 'id="system-shell"' in response.text
    assert 'id="browse-select"' in response.text
    assert '<option value="e2e">Request journeys</option>' in response.text
    assert '<option value="api">API routes</option>' in response.text
    assert '<option value="internals">Internal components</option>' in response.text
    assert '<option value="database">Database operations</option>' in response.text
    assert 'class="lens-switch' not in response.text
    assert 'id="flow-list"' in response.text
    assert 'id="source-inspector"' in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text

    head = api.head("/api-map")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"].startswith("text/html")

    schema = api.get("/openapi.json").json()
    assert "/api-map" not in schema["paths"]


def test_database_route_opens_the_same_unified_system_map() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/db-map")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert '<script type="module" src="/assets/system-map.js"></script>' in response.text
    assert 'href="/assets/ui.css"' in response.text
    assert 'href="/assets/api-map.css"' in response.text
    assert 'href="/assets/system-map.css"' in response.text
    assert 'id="system-shell"' in response.text
    assert 'id="entry-picker-trigger"' in response.text
    assert 'id="entry-dialog"' in response.text
    assert 'id="dialog-entry-search"' in response.text
    assert 'id="show-outcomes"' in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text

    head = api.head("/db-map")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"].startswith("text/html")

    schema = api.get("/openapi.json").json()
    assert "/db-map" not in schema["paths"]


def test_canonical_system_map_is_served_and_hidden_from_openapi() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/system-map")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert '<script type="module" src="/assets/system-map.js"></script>' in response.text
    assert 'id="system-workspace"' in response.text
    assert 'id="journey-tabs"' in response.text
    assert 'id="journey-track"' in response.text
    assert 'id="flow-search"' in response.text
    assert 'id="close-source-inspector"' in response.text
    assert "/system-map" not in api.get("/openapi.json").json()["paths"]


def test_local_prototype_rejects_untrusted_host_headers() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/api/v1/session", headers={"Host": "rebound.attacker.example"})

    assert response.status_code == 400
    assert response.text == "Invalid host header"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert len(response.headers["x-request-id"]) == 32


def test_api_documentation_receives_general_browser_safety_headers() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/docs")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" not in response.headers


def test_every_packaged_frontend_asset_is_available_and_revalidated() -> None:
    api = TestClient(create_app(), base_url="http://localhost")
    assets = files("schemii.schemii").joinpath("web", "assets")
    common_assets = files("schemii.common").joinpath("web", "assets")

    for asset in assets.iterdir():
        if not asset.is_file():
            continue
        response = api.get(f"/assets/{asset.name}")
        assert response.status_code == 200, asset.name
        assert response.headers["cache-control"] == "public, max-age=0, must-revalidate"
        assert response.headers["x-content-type-options"] == "nosniff"

    for asset in common_assets.iterdir():
        if not asset.is_file():
            continue
        response = api.get(f"/assets/common/{asset.name}")
        assert response.status_code == 200, asset.name
        assert response.headers["cache-control"] == "public, max-age=0, must-revalidate"
        assert response.headers["x-content-type-options"] == "nosniff"

    first = api.get("/assets/app.js")
    revalidated = api.get("/assets/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert revalidated.status_code == 304
    assert revalidated.headers["cache-control"] == "public, max-age=0, must-revalidate"


def test_column_type_editor_uses_the_shared_searchable_selector() -> None:
    web = files("schemii.schemii").joinpath("web")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    app = web.joinpath("assets", "app.js").read_text(encoding="utf-8")

    assert 'href="/assets/common/searchable-select.css"' in index
    assert 'from "/assets/common/searchable-select.js"' in app
    assert 'dataset: { designColumnType: "" }' in app
    assert 'placeholder: "Search types"' in app
    assert 'className: "design-type-modifiers"' in app
    assert "composePostgresTypeModifier" in app


def test_source_derived_change_cues_follow_workspace_section_colors() -> None:
    web = files("schemii.schemii").joinpath("web")
    common = files("schemii.common").joinpath("web", "assets")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    app = web.joinpath("assets", "app.js").read_text(encoding="utf-8")
    styles = common.joinpath("transient-cue.css").read_text(encoding="utf-8")

    assert 'data-change-scope="schema"' in index
    assert 'data-change-scope="views"' in index
    assert 'data-change-scope="sql"' in index
    assert 'from "/assets/common/transient-cue.js"' in app
    assert 'from "/assets/common/change-transition.js"' in app
    assert 'from "./design-change.js"' in app
    assert 'tone: "blue"' in app
    assert '[data-change-cue-tone="amber"]' in styles
    assert '[data-change-cue-tone="purple"]' in styles
    assert '[data-change-cue-tone="blue"]' in styles
    assert "animation: change-cue-pulse .85s ease-out forwards" in styles
    assert "border-radius: var(--change-cue-radius, 5px)" in styles
    assert "0 0 0 2px rgba(var(--change-cue-rgb), 1)" in styles
    assert index.index('href="assets/app.css"') < index.index(
        'href="/assets/common/transient-cue.css"'
    )


def test_mobile_status_toasts_never_cover_or_capture_workspace_tools() -> None:
    web = files("schemii.schemii").joinpath("web", "assets")
    shared_styles = web.joinpath("ui.css").read_text(encoding="utf-8")
    app_styles = web.joinpath("app.css").read_text(encoding="utf-8")

    assert ".ui-toast" in shared_styles
    assert "pointer-events: none" in shared_styles
    assert ".ui-toast { top: 108px;" in app_styles


def test_matching_history_confirmation_does_not_repaint_the_optimistic_catalog() -> None:
    app = (
        files("schemii.schemii")
        .joinpath("web", "assets", "app.js")
        .read_text(encoding="utf-8")
    )

    assert "return { presentation, targets };" in app
    assert "applyDesignHistoryMutation(mutation, { cue: !previewMatches, render: !previewMatches });" in app
    assert "state.viewAnalysisCache.clear();\n  if (!state.catalog.tables" not in app


def test_views_use_compact_context_specific_empty_states() -> None:
    assets = files("schemii.schemii").joinpath("web", "assets")
    catalog = assets.joinpath("catalog.js").read_text(encoding="utf-8")
    styles = assets.joinpath("app.css").read_text(encoding="utf-8")

    assert 'className: "view-list-empty"' in catalog
    assert 'emptyPanel("0", "No matching views"' not in catalog
    assert ".view-detail.is-empty { display: grid; place-items: center; }" in styles


def test_postgres_import_is_exposed_only_as_new_workspace_creation() -> None:
    web = files("schemii.schemii").joinpath("web")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    app = web.joinpath("assets", "app.js").read_text(encoding="utf-8")
    api = web.joinpath("assets", "api.js").read_text(encoding="utf-8")

    assert '<option value="detached">Design workspace · no database</option>' in index
    assert '<option value="import">Database workspace · read/write (planned)</option>' in index
    assert '<option value="attached">Live database · read only</option>' in index
    assert "Create database workspace" in app
    assert "writing approved changes back to PostgreSQL is planned." in app
    assert "editing is disabled." in app
    assert "createWorkspaceImport" in app
    assert "schemii/workspaces/imports" in api
    assert "design/imports" not in api


def test_frontend_does_not_replace_unknown_routes_with_the_app_shell() -> None:
    api = TestClient(create_app(), base_url="http://localhost")

    response = api.get("/not-a-frontend-route")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "not_found"


def test_unavailable_controls_are_backed_by_the_shared_capability_registry() -> None:
    web = files("schemii.schemii").joinpath("web")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    catalog = web.joinpath("assets", "catalog.js").read_text(encoding="utf-8")
    unavailable = web.joinpath("assets", "unavailable.js").read_text(encoding="utf-8")

    registry_matches = re.findall(
        r'^  (?:(?:"([^"]+)")|([a-z][a-z0-9-]*)):\s*\{ title:',
        unavailable,
        re.MULTILINE,
    )
    registry = {quoted or bare for quoted, bare in registry_matches}
    references = set(re.findall(r'data-unavailable="([^"]+)"', index))
    references.update(re.findall(r'unavailableButton\("([^"]+)"', catalog))

    assert references == registry
    assert "restore-examples" in registry
    assert "The active API does not provide example content" in unavailable


def test_frontend_uses_only_the_active_same_origin_api_contract() -> None:
    api_source = (
        files("schemii.schemii")
        .joinpath("web", "assets", "api.js")
        .read_text(encoding="utf-8")
    )

    assert 'const API_ROOT = "/api/v1";' in api_source
    assert "credentials: \"same-origin\"" in api_source
    assert "cache: \"no-store\"" in api_source
    assert "http://" not in api_source
    assert "https://" not in api_source


def test_api_map_uses_only_the_live_same_origin_openapi_contract() -> None:
    map_source = (
        files("schemii.schemii")
        .joinpath("web", "assets", "api-map.js")
        .read_text(encoding="utf-8")
    )

    assert 'fetch("/openapi.json"' in map_source
    assert 'credentials: "same-origin"' in map_source
    assert 'cache: "no-store"' in map_source
    assert "http://" not in map_source
    assert "https://" not in map_source


def test_db_map_uses_only_same_origin_source_inspection() -> None:
    map_source = (
        files("schemii.schemii")
        .joinpath("web", "assets", "db-map.js")
        .read_text(encoding="utf-8")
    )

    assert 'fetchInspection("/_developer/database"' in map_source
    assert 'fetchInspection("/_developer/routes"' in map_source
    assert 'credentials: "same-origin"' in map_source
    assert 'cache: "no-store"' in map_source
    assert "http://" not in map_source
    assert "https://" not in map_source


def test_system_map_joins_only_same_origin_source_documents() -> None:
    map_source = (
        files("schemii.schemii")
        .joinpath("web", "assets", "system-map.js")
        .read_text(encoding="utf-8")
    )

    assert 'fetchDocument("/_developer/system"' in map_source
    assert 'fetchDocument("/_developer/routes"' in map_source
    assert 'fetchDocument("/_developer/database"' in map_source
    assert 'fetchDocument("/openapi.json"' in map_source
    assert 'credentials: "same-origin"' in map_source
    assert 'cache: "no-store"' in map_source
    assert "http://" not in map_source
    assert "https://" not in map_source


def test_api_map_route_stages_use_the_dense_summary_contract() -> None:
    assets = files("schemii.schemii").joinpath("web", "assets")
    map_source = assets.joinpath("api-map.js").read_text(encoding="utf-8")
    map_styles = assets.joinpath("api-map.css").read_text(encoding="utf-8")

    assert 'className: "stage-summary-heading"' in map_source
    assert '"data-ui-tooltip-overflow": meta' in map_source
    assert '"data-ui-tooltip-touch": "true"' in map_source
    assert 'className: "stage-detail-section stage-body-section"' in map_source
    assert 'textContent: `${item.mediaType} · contract shape`' in map_source
    assert '"Transport parameters · none"' in map_source
    assert ".stage-summary-heading { display: flex" in map_styles
    assert ".route-stage { position: relative; display: block" in map_styles
    assert ".stage-request .media-contract, .stage-response .media-contract" in map_styles
    assert ".stage-link.ui-icon-button.compact" in map_styles
    assert ".code-toolbar .ui-icon-button.compact" in map_styles


def test_frontends_share_the_visual_component_and_dock_pane_contract() -> None:
    assets = files("schemii.schemii").joinpath("web", "assets")
    ui_source = assets.joinpath("ui.js").read_text(encoding="utf-8")
    ui_styles = assets.joinpath("ui.css").read_text(encoding="utf-8")
    app_source = assets.joinpath("app.js").read_text(encoding="utf-8")
    map_source = assets.joinpath("api-map.js").read_text(encoding="utf-8")

    assert "export class DockPane" in ui_source
    assert "export const ICONS" in ui_source
    assert 'data-ui-dock-state="minimized"' in ui_styles
    assert "new DockPane" in app_source
    assert map_source.count("new DockPane") == 1
    assert "getViewportInsets" in assets.joinpath("canvas.js").read_text(encoding="utf-8")


def test_frontends_use_explicit_shared_state_and_text_action_contracts() -> None:
    web = files("schemii.schemii").joinpath("web")
    assets = web.joinpath("assets")
    index = web.joinpath("index.html").read_text(encoding="utf-8")
    map_html = web.joinpath("api-map.html").read_text(encoding="utf-8")
    map_source = assets.joinpath("api-map.js").read_text(encoding="utf-8")
    ui_source = assets.joinpath("ui.js").read_text(encoding="utf-8")
    ui_styles = assets.joinpath("ui.css").read_text(encoding="utf-8")

    assert "export function createStatePanel" in ui_source
    assert "export function renderStatePanel" in ui_source
    assert "export function closeDetailsMenus" in ui_source
    assert ".ui-state > span:first-child" not in ui_styles
    assert ".ui-state.loading > .ui-state__mark" in ui_styles
    assert 'class="ui-state__mark"' in index
    assert map_html.count('class="ui-state__mark"') == 2
    assert 'className: "swagger-link ui-button compact"' in map_source
    assert index.count("ui-button compact") >= 20
