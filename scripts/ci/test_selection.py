"""Conservative, reviewed CI ownership boundaries. New paths never inherit a profile."""

from __future__ import annotations

PROFILES = frozenset(
    {
        "full",
        "reports",
        "native",
        "harness",
        "load",
        "backend-tests",
        "frontend-tests",
        "e2e-tests",
    }
)
LAYERS = {
    "full": frozenset({"static", "node", "python", "postgres", "browser"}),
    "reports": frozenset(),
    "native": frozenset({"static", "node", "python"}),
    "harness": frozenset({"static", "node", "python"}),
    "load": frozenset({"static", "node", "python"}),
    "backend-tests": frozenset({"static", "node", "python"}),
    "frontend-tests": frozenset({"node"}),
    "e2e-tests": frozenset({"node", "browser"}),
}
# Tooling closes over harness/load consumers without involving installed application owners.
PYTHON_PATHS = {
    "native": ("testing/agents",),
    "harness": ("testing/agents", "testing/harness", "tests/test_load_planner.py"),
    "load": ("testing/agents", "testing/harness", "tests/test_load_planner.py"),
}
BROWSER_PROJECTS = ("desktop-chromium", "android-chromium")
BROWSER_SHARDS = (1, 2, 3)
JOB_NAMES = {
    "Incremental Python static quality": "static",
    "Node and deterministic Python behavior": "unit",
    "Real PostgreSQL metadata behavior": "postgres",
    **{
        f"Assembled browser smoke ({project}, shard {shard}/3)": f"browser-{project}-{shard}"
        for project in BROWSER_PROJECTS
        for shard in BROWSER_SHARDS
    },
}
SOURCE_NEEDS = frozenset(
    {"static-quality", "test", "postgres-integration", "browser-smoke"}
)


def layers(profile: str) -> frozenset[str]:
    try:
        return LAYERS[profile]
    except (KeyError, TypeError) as error:
        raise ValueError("Unknown test profile") from error


def required_needs(profile: str) -> set[str]:
    selected = layers(profile)
    return (
        ({"static-quality"} if "static" in selected else set())
        | ({"test"} if {"node", "python"} & selected else set())
        | ({"postgres-integration"} if "postgres" in selected else set())
        | ({"browser-smoke"} if "browser" in selected else set())
    )


def expected_jobs(profile: str) -> dict[str, str]:
    selected = layers(profile)
    return {
        name: label
        for name, label in JOB_NAMES.items()
        if label in selected
        or label == "unit"
        and {"node", "python"} & selected
        or label.startswith("browser-")
        and "browser" in selected
    }


def expected_lanes(profile: str) -> set[tuple[str, str, int]]:
    selected = layers(profile)
    return {
        (lane, "none", 0) for lane in ("node", "python", "postgres") if lane in selected
    } | {
        ("browser", project, shard)
        for project in BROWSER_PROJECTS
        for shard in BROWSER_SHARDS
        if "browser" in selected
    }


# Frozen existing leaves, independently traced to consumers at main3e67404.
# Shared helpers/configuration, new files, documentation and every product path fall back full.
PATHS = {
    "native": frozenset(
        """
.codex/agents/developer.toml
.codex/agents/qa_reviewer.toml
.codex/agents/ui_tester.toml
.codex/config.toml
.codex/hooks.json
testing/agents/browser.py
testing/agents/doctor.py
testing/agents/guard.py
testing/agents/test_browser.py
testing/agents/test_browser_probe.py
testing/agents/test_doctor.py
testing/agents/test_guard.py
testing/agents/verify_browser_cleanup.py
testing/agents/verify_browser_isolation.py
""".split()
    ),
    "harness": frozenset(
        """
test.sh
testing/harness/browser.mjs
testing/harness/cleanup_schemii_sweep.py
testing/harness/cli.mjs
testing/harness/daemon.mjs
testing/harness/deployment.mjs
testing/harness/findings.test.mjs
testing/harness/leases.mjs
testing/harness/leases.test.mjs
testing/harness/native.mjs
testing/harness/native.test.mjs
testing/harness/prerequisites.mjs
testing/harness/prerequisites.test.mjs
testing/harness/readiness.mjs
testing/harness/readiness.test.mjs
testing/harness/schemii_followup.py
testing/harness/schemii_preseed.py
testing/harness/schemii_sweep.py
testing/harness/schemii_workspaces.py
testing/harness/store.mjs
testing/harness/test_schemii_cleanup.py
testing/harness/test_schemii_followup.py
testing/harness/test_schemii_preseed.py
testing/harness/test_schemii_sweep.py
testing/harness/workers.mjs
testing/harness/workers.test.mjs
testing/test.sh
""".split()
    ),
    "load": frozenset(
        """
testing/load/accounting.mjs
testing/load/bridge.test.mjs
testing/load/campaign.test.mjs
testing/load/children.mjs
testing/load/children.test.mjs
testing/load/cli.mjs
testing/load/compiled_columns.py
testing/load/controller.mjs
testing/load/engine.mjs
testing/load/exact-rate.k6.js
testing/load/fixture-spec.json
testing/load/fixtures.mjs
testing/load/fixtures.test.mjs
testing/load/http.mjs
testing/load/k6-policy.test.mjs
testing/load/plan.mjs
testing/load/protocol.mjs
testing/load/protocol.test.mjs
tests/test_load_planner.py
""".split()
    ),
    "frontend-tests": frozenset(
        """
tests/frontend/account-auth.test.js
tests/frontend/accounts-access.test.js
tests/frontend/admin-policy.test.js
tests/frontend/ai-app-receipts.test.js
tests/frontend/ai-copy-handoff.test.js
tests/frontend/ai-download.test.js
tests/frontend/ai-permissions.test.js
tests/frontend/ai-proposal-review.test.js
tests/frontend/ai-reasoning.test.js
tests/frontend/ai-timeline.test.js
tests/frontend/api-graph.test.js
tests/frontend/api-map.test.js
tests/frontend/api.test.js
tests/frontend/async-value-select.test.js
tests/frontend/canvas.test.js
tests/frontend/change-transition.test.js
tests/frontend/column-display-order.test.js
tests/frontend/console-copy.test.js
tests/frontend/csv.test.js
tests/frontend/dashboard-refresh.test.js
tests/frontend/data-grid.test.js
tests/frontend/database-fixtures.test.js
tests/frontend/db-map.test.js
tests/frontend/design-change.test.js
tests/frontend/design.test.js
tests/frontend/draft-analysis.test.js
tests/frontend/elapsed-time.test.js
tests/frontend/graph-viewport.test.js
tests/frontend/history-confirmation.test.js
tests/frontend/json-delta.test.js
tests/frontend/login-return.test.js
tests/frontend/migration-review.test.js
tests/frontend/query-execution.test.js
tests/frontend/query-plan.test.js
tests/frontend/relation-data-source.test.js
tests/frontend/request-coordinator.test.js
tests/frontend/runtime-boundary.test.js
tests/frontend/schemer-bar-series.test.js
tests/frontend/schemer-chart-dimensions.test.js
tests/frontend/schemer-filter-summary.test.js
tests/frontend/schemer-report.test.js
tests/frontend/schemer-result-cache.test.js
tests/frontend/schemer-time-analysis.test.js
tests/frontend/schemoo-alias-model.test.js
tests/frontend/schemoo-column-comparisons.test.js
tests/frontend/schemoo-derived.test.js
tests/frontend/schemoo-filter-links.test.js
tests/frontend/schemoo-layout.test.js
tests/frontend/schemoo-logical-relationships.test.js
tests/frontend/schemoo-model-state.test.js
tests/frontend/schemoo-preview-state.test.js
tests/frontend/schemoo-repetition.test.js
tests/frontend/schemoo-source-reconciliation.test.js
tests/frontend/schemoo-summary-lookup.test.js
tests/frontend/searchable-select.test.js
tests/frontend/sortable.test.js
tests/frontend/sql-console-activity.test.js
tests/frontend/sql-identifier.test.js
tests/frontend/sql-statements.test.js
tests/frontend/system-map.test.js
tests/frontend/transient-cue.test.js
tests/frontend/ui.test.js
tests/frontend/user-activity.test.js
tests/frontend/view-analysis.test.js
tests/frontend/view-story.test.js
tests/frontend/workspace-navigation.test.js
tests/frontend/workspace-state.test.js
tests/frontend/workspace-toolbar.test.js
""".split()
    ),
    "backend-tests": frozenset(
        """
tests/test_account_auth.py
tests/test_admin_config.py
tests/test_ai_action_context.py
tests/test_ai_actions.py
tests/test_ai_app_actions.py
tests/test_ai_app_lifecycle.py
tests/test_ai_assistant.py
tests/test_ai_authorized_execution.py
tests/test_ai_console_preferences.py
tests/test_ai_context.py
tests/test_ai_credential_store.py
tests/test_ai_design_permission_enforcement.py
tests/test_ai_design_permissions.py
tests/test_ai_failed_turn_recovery.py
tests/test_ai_individual_permissions.py
tests/test_ai_individual_tools.py
tests/test_ai_instance_provider_store.py
tests/test_ai_limits.py
tests/test_ai_migration_recovery.py
tests/test_ai_model_catalog.py
tests/test_ai_model_preferences.py
tests/test_ai_postgres_authorization.py
tests/test_ai_prompt.py
tests/test_ai_prototype.py
tests/test_ai_provider_routes.py
tests/test_ai_query_cancellation.py
tests/test_ai_raw_actions.py
tests/test_ai_read_execution.py
tests/test_ai_read_repository.py
tests/test_ai_read_workflow.py
tests/test_ai_reasoning_preferences.py
tests/test_ai_tool_contracts.py
tests/test_api.py
tests/test_application_structure.py
tests/test_bulk_jobs.py
tests/test_catalog_browser.py
tests/test_column_conversions.py
tests/test_connection_repository.py
tests/test_console.py
tests/test_console_connection_dependencies.py
tests/test_console_read_diagnostics.py
tests/test_conversion_gateway.py
tests/test_credential_lifecycle.py
tests/test_database_inspection.py
tests/test_demo_fixture.py
tests/test_design_deletion_impact.py
tests/test_design_history.py
tests/test_design_import.py
tests/test_design_repository.py
tests/test_developer_inspection.py
tests/test_frontend.py
tests/test_json_delta.py
tests/test_legacy_ai_cleanup.py
tests/test_limit_events.py
tests/test_managed_product_connections.py
tests/test_metadata_admission.py
tests/test_metadata_composition.py
tests/test_metadata_persistence.py
tests/test_migration_drift_resolution.py
tests/test_migration_execution.py
tests/test_migration_plan_retention.py
tests/test_migration_planner.py
tests/test_model_dashboard_dependencies.py
tests/test_pi_chat_flow.py
tests/test_pi_login_api.py
tests/test_pi_runtime.py
tests/test_postgres_gateway.py
tests/test_query_cancellation.py
tests/test_query_plans.py
tests/test_raw_console.py
tests/test_raw_console_review.py
tests/test_recovery.py
tests/test_retained_connection_capacity.py
tests/test_route_inspection.py
tests/test_routine_analysis.py
tests/test_runtime_hardening.py
tests/test_schemer_ai.py
tests/test_schemer_dashboard_execution.py
tests/test_schemer_dashboards.py
tests/test_schemer_multidimensional.py
tests/test_schemer_routes.py
tests/test_schemer_streaming.py
tests/test_schemer_tile_queries.py
tests/test_schemer_time_analysis.py
tests/test_schemii_managed_background_connections.py
tests/test_schemii_managed_connections_api.py
tests/test_schemoo_ai_conversations.py
tests/test_schemoo_ai_routes.py
tests/test_schemoo_ai_tools.py
tests/test_schemoo_column_comparisons.py
tests/test_schemoo_date_ranges.py
tests/test_schemoo_derived.py
tests/test_schemoo_duplicate.py
tests/test_schemoo_filters.py
tests/test_schemoo_logical_relationships.py
tests/test_schemoo_prototype.py
tests/test_schemoo_repetition.py
tests/test_schemoo_routes.py
tests/test_schemoo_source_drift.py
tests/test_schemoo_store.py
tests/test_shared_query_executions.py
tests/test_shared_report_access.py
tests/test_shared_route_and_grant_rules.py
tests/test_source_inspection.py
tests/test_system_inspection.py
tests/test_trigger_analysis.py
tests/test_type_analysis.py
tests/test_view_analysis.py
tests/test_workspace_import_transactions.py
tests/test_workspace_rename.py
tests/test_workspace_repository.py
""".split()
    ),
    "e2e-tests": frozenset(
        """
tests/e2e/account-audit.spec.js
tests/e2e/account-brand-navigation.spec.js
tests/e2e/accounts.spec.js
tests/e2e/ai-assistant.spec.js
tests/e2e/ai-copy-handoff.spec.js
tests/e2e/ai-design-batch-live.spec.js
tests/e2e/ai-diagnostic-permissions.spec.js
tests/e2e/ai-failure-recovery.spec.js
tests/e2e/ai-model-switch.spec.js
tests/e2e/ai-provider-settings.spec.js
tests/e2e/ai-read-live.spec.js
tests/e2e/ai-structured-read-live.spec.js
tests/e2e/bulk-jobs.spec.js
tests/e2e/connection-lifecycle.spec.js
tests/e2e/console-preferences.spec.js
tests/e2e/console-shared-capacity.spec.js
tests/e2e/console-toolbar.spec.js
tests/e2e/design-editor-lifecycle.spec.js
tests/e2e/inspector-data.spec.js
tests/e2e/inspector-editor.spec.js
tests/e2e/migration-disabled-apply.spec.js
tests/e2e/migration-review-mobile.spec.js
tests/e2e/permission-bundles.spec.js
tests/e2e/product-navigation.spec.js
tests/e2e/query-diagnostics.spec.js
tests/e2e/query-plan-table.spec.js
tests/e2e/quick-start-schemii.spec.js
tests/e2e/quick-start.spec.js
tests/e2e/raw-console.spec.js
tests/e2e/raw-copy-streaming.spec.js
tests/e2e/schemer-ai-live.spec.js
tests/e2e/schemer-dashboards.spec.js
tests/e2e/schemoo-ai.spec.js
tests/e2e/schemoo-aliases.spec.js
tests/e2e/schemoo-canvas-overlap.spec.js
tests/e2e/schemoo-column-comparisons.spec.js
tests/e2e/schemoo-column-filters.spec.js
tests/e2e/schemoo-connection-colors.spec.js
tests/e2e/schemoo-derived.spec.js
tests/e2e/schemoo-diagnostics.spec.js
tests/e2e/schemoo-dropdowns.spec.js
tests/e2e/schemoo-filter-authoring.spec.js
tests/e2e/schemoo-filter-dialog.spec.js
tests/e2e/schemoo-filter-navigation.spec.js
tests/e2e/schemoo-help.spec.js
tests/e2e/schemoo-membership.spec.js
tests/e2e/schemoo-model-dependencies-live.spec.js
tests/e2e/schemoo-model-editor-audit.spec.js
tests/e2e/schemoo-model-library.spec.js
tests/e2e/schemoo-pinch.spec.js
tests/e2e/schemoo-preview-fields.spec.js
tests/e2e/schemoo-prototype.spec.js
tests/e2e/schemoo-repetition-live.spec.js
tests/e2e/schemoo-saved-previews.spec.js
tests/e2e/schemoo-source-reconciliation.spec.js
tests/e2e/semantic-api-owned.spec.js
tests/e2e/shared-report-live.spec.js
tests/e2e/shared-ui-accessibility.spec.js
tests/e2e/shared-ui-audit.spec.js
tests/e2e/sql-console.spec.js
tests/e2e/toast-history-behavior.spec.js
tests/e2e/workspace-lifecycle.spec.js
tests/e2e/workspace-rename.spec.js
""".split()
    ),
}


def select_paths(changes: list[tuple[str, list[str]]]) -> str:
    """Select only modifications wholly within one independently owned family."""
    selected = set()
    for status, paths in changes:
        if status != "M" or len(paths) != 1:
            return "full"
        matches = {profile for profile, owned in PATHS.items() if paths[0] in owned}
        if len(matches) != 1:
            return "full"
        selected.update(matches)
    return selected.pop() if len(selected) == 1 else "full"


def commands(profile: str, base: str) -> list[list[str]]:
    """The complete checks for a reviewed profile, with no changed-test filters."""
    selected = layers(profile)
    result = []
    if profile == "reports":
        return [
            [
                "python3",
                "scripts/ci/validate_reports.py",
                "--classification",
                ".schemii/test-selection/classification.json",
                "--output",
                ".schemii/test-selection/report-validation.json",
            ]
        ]
    if "node" in selected:
        result.append(["npm", "test"])
    if "static" in selected:
        result.append(["python", "scripts/check_python_quality.py", base])
    if "python" in selected:
        result.append(
            ["python", "scripts/ci/python-tests.py", *PYTHON_PATHS.get(profile, ())]
        )
        result.append(["python", "-m", "compileall", "-q", "src", "tests", "testing"])
    if "postgres" in selected:
        result.append(
            [
                "python",
                "-m",
                "pytest",
                "-q",
                "-p",
                "scripts.ci.pytest_timing",
                "tests/integration",
            ]
        )
    if "browser" in selected:
        result.extend(
            [
                ["npm", "ci"],
                ["node", "--test", "tests/browser-infrastructure/shards.test.mjs"],
                ["./start.sh"],
            ]
        )
        result.extend(
            [
                "node",
                "scripts/ci/run-browser-shard.mjs",
                f"--project={project}",
                f"--shard={shard}/3",
            ]
            for project in BROWSER_PROJECTS
            for shard in BROWSER_SHARDS
        )
        if profile == "full":
            result.extend(
                [
                    ["./start.sh", "--backup", ".schemii/test-selection/recovery"],
                    [
                        "./start.sh",
                        "--verify-backup",
                        ".schemii/test-selection/recovery",
                    ],
                ]
            )
    return result
