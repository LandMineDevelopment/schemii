"""Conservative, reviewed CI ownership boundaries. New paths never inherit a profile."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

CACHE_PROFILE = "schemer-result-cache"
CACHE_SOURCE = "src/schemii/schemer/web/result-cache.js"
CACHE_TEST = "tests/frontend/schemer-result-cache.test.js"
INSPECTION_PROFILE = "developer-inspection"
INSPECTION_SOURCES = frozenset(
    {
        "src/schemii/common/source_inspection.py",
        "src/schemii/common/api/inspection.py",
        "src/schemii/common/postgres/inspection.py",
    }
)
INSPECTION_TEST = "tests/test_source_inspection.py"
INSPECTION_PYTHON = (
    "tests/test_source_inspection.py",
    "tests/test_route_inspection.py",
    "tests/test_database_inspection.py",
    "tests/test_system_inspection.py",
    "tests/test_developer_inspection.py",
    "tests/test_frontend.py",
    "tests/test_application_structure.py",
    "tests/test_runtime_hardening.py",
)
INSPECTION_BROWSER = (
    "tests/e2e/shared-ui-audit.spec.js",
    "tests/e2e/ai-diagnostic-permissions.spec.js",
)

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
        CACHE_PROFILE,
        INSPECTION_PROFILE,
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
    CACHE_PROFILE: frozenset({"node", "python", "browser"}),
    INSPECTION_PROFILE: frozenset({"static", "node", "python", "browser"}),
}
# Tooling closes over harness/load consumers without involving installed application owners.
PYTHON_PATHS = {
    CACHE_PROFILE: ("tests/test_frontend.py",),
    INSPECTION_PROFILE: INSPECTION_PYTHON,
    "native": ("testing/agents",),
    "harness": ("testing/agents", "testing/harness", "tests/test_load_planner.py"),
    "load": ("testing/agents", "testing/harness", "tests/test_load_planner.py"),
}
BROWSER_PROJECTS = ("desktop-chromium", "android-chromium")
SOURCE_JOB_NAMES = {
    "Incremental Python static quality": "static",
    "Node and deterministic Python behavior": "unit",
    "Real PostgreSQL metadata behavior": "postgres",
}


def browser_shards(profile: str) -> tuple[int, ...]:
    """Closed hosted topology; excluded profiles retain full skip verification."""
    layers(profile)
    return (
        (1,)
        if profile == INSPECTION_PROFILE
        else tuple(range(1, 4 if profile == CACHE_PROFILE else 7))
    )


def browser_matrix(profile: str) -> dict:
    shards = browser_shards(profile)
    return {
        "include": [
            {"project": project, "shard": shard, "total": len(shards)}
            for project in BROWSER_PROJECTS
            for shard in shards
        ]
    }


def source_job_names(profile: str) -> dict[str, str]:
    shards = browser_shards(profile)
    total = len(shards)
    return {
        **SOURCE_JOB_NAMES,
        **{
            f"Assembled browser smoke ({project}, shard {shard}/{total})": f"browser-{project}-{shard}-of-{total}"
            for project in BROWSER_PROJECTS
            for shard in shards
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
        for name, label in source_job_names(profile).items()
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
        for shard in browser_shards(profile)
        if "browser" in selected
    }


# Frozen existing leaves, independently traced to consumers at main3e67404.
# Shared helpers/configuration, new files and documentation fall back full.
# Product selection is closed to the reviewed cache and inspection source owners.
PATHS = {
    CACHE_PROFILE: frozenset({CACHE_SOURCE}),
    INSPECTION_PROFILE: INSPECTION_SOURCES,
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
    changed = {path for _, paths in changes for path in paths}
    if changed & INSPECTION_SOURCES:
        safe = all(status == "M" and len(paths) == 1 for status, paths in changes)
        return (
            INSPECTION_PROFILE
            if safe and changed <= INSPECTION_SOURCES | {INSPECTION_TEST}
            else "full"
        )
    # The direct test keeps its existing family unless its source is also present.
    if any(CACHE_SOURCE in paths for _, paths in changes):
        safe = all(status == "M" and len(paths) == 1 for status, paths in changes)
        changed = {path for _, paths in changes for path in paths}
        return (
            CACHE_PROFILE if safe and changed <= {CACHE_SOURCE, CACHE_TEST} else "full"
        )
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
                f"--shard={shard}/{len(browser_shards(profile))}",
                f"--profile={profile}",
            ]
            for project in BROWSER_PROJECTS
            for shard in browser_shards(profile)
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


def coverage_policy(profile=CACHE_PROFILE) -> dict:
    """Reviewed frozen discovery, independent of supplied classification/receipts."""
    if profile == INSPECTION_PROFILE:
        return inspection_policy()
    if profile != CACHE_PROFILE:
        raise ValueError("Unknown coverage policy")
    policy = json.loads(Path(__file__).with_name("coverage-profiles.json").read_text())
    if (
        set(policy) != {"schema", "profile", "files", "python", "browser"}
        or type(policy["schema"]) is not int
        or policy["schema"] != 1
        or policy["profile"] != CACHE_PROFILE
    ):
        raise ValueError("Invalid coverage policy")
    files = policy["files"]
    if (
        not isinstance(files, list)
        or len(files) != 8
        or len(set(files)) != 8
        or any(
            not isinstance(file, str)
            or not re.fullmatch(r"tests/e2e/[a-z-]+\.spec\.js", file)
            for file in files
        )
    ):
        raise ValueError("Invalid browser coverage files")
    if set(policy["browser"]) != set(BROWSER_PROJECTS):
        raise ValueError("Invalid coverage projects")
    for project, inventory in [
        ("python", policy["python"]),
        *policy["browser"].items(),
    ]:
        fields = {"files", "allowed_skips"} | (
            {"shards"} if project != "python" else set()
        )
        if (
            not isinstance(inventory, dict)
            or set(inventory) != fields
            or set(inventory["files"])
            != ({"tests/test_frontend.py"} if project == "python" else set(files))
        ):
            raise ValueError("Invalid coverage inventory")
        ids = []
        for tests in inventory["files"].values():
            if (
                not isinstance(tests, list)
                or not tests
                or any(
                    not isinstance(test, str) or not re.fullmatch(r"[0-9a-f]{64}", test)
                    for test in tests
                )
            ):
                raise ValueError("Invalid coverage cases")
            ids.extend(tests)
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate coverage case")
        skips = inventory["allowed_skips"]
        if project == "python":
            if skips != []:
                raise ValueError("Unexpected Python skip")
        else:
            if (
                skips != inventory["files"]["tests/e2e/schemer-ai-live.spec.js"]
                or len(skips) != 1
            ):
                raise ValueError("Unexpected browser skip")
            shards = inventory["shards"]
            if (
                not isinstance(shards, list)
                or len(shards) != 3
                or any(not isinstance(shard, list) or not shard for shard in shards)
            ):
                raise ValueError("Invalid coverage shards")
            assigned = [file for shard in shards for file in shard]
            if len(assigned) != len(files) or set(assigned) != set(files):
                raise ValueError("Missing or duplicate coverage file")
    return policy


def expected_scope(profile, key):
    """Return independently required cases for scoped Python/browser lanes only."""
    if profile not in {CACHE_PROFILE, INSPECTION_PROFILE} or key[0] not in {
        "python",
        "browser",
    }:
        return None
    policy = coverage_policy(profile)
    lane, project, shard = key
    if lane == "python" and (project, shard) == ("none", 0):
        inventory = policy["python"]
        files = list(inventory["files"])
    elif (
        lane == "browser"
        and project in BROWSER_PROJECTS
        and shard in browser_shards(profile)
    ):
        inventory = policy["browser"][project]
        files = inventory["shards"][shard - 1]
    else:
        raise ValueError("Invalid scoped lane")
    return {
        test: {
            "source_id": hashlib.sha256(file.encode()).hexdigest(),
            "allow_skip": test in inventory["allowed_skips"],
        }
        for file in files
        for test in inventory["files"][file]
    }


def valid_scope(profile, key, scope):
    expected = expected_scope(profile, key)
    if expected is None:
        return True
    if (
        not isinstance(scope, dict)
        or set(scope) != {"profile", "planned", "observed"}
        or scope["profile"] != profile
    ):
        return False
    planned = scope["planned"]
    dynamic = profile == INSPECTION_PROFILE and key == ("python", "none", 0)
    if not isinstance(planned, list) or any(
        not isinstance(test, str) or not re.fullmatch(r"[0-9a-f]{64}", test)
        for test in planned
    ):
        return False
    if planned != sorted(set(planned)) or (
        not set(expected) <= set(planned) if dynamic else planned != sorted(expected)
    ):
        return False
    observed = scope["observed"]
    if not isinstance(observed, list) or len(observed) != len(planned):
        return False
    seen = set()
    for value in observed:
        if not isinstance(value, dict) or set(value) != {
            "test_id",
            "source_id",
            "outcome",
            "attempt",
        }:
            return False
        test = value["test_id"]
        if (
            not isinstance(test, str)
            or test not in planned
            or test in seen
            or type(value["attempt"]) is not int
            or value["attempt"] != 0
        ):
            return False
        seen.add(test)
        owner = expected.get(
            test,
            {
                "source_id": hashlib.sha256(INSPECTION_TEST.encode()).hexdigest(),
                "allow_skip": False,
            },
        )
        if value["source_id"] != owner["source_id"] or value["outcome"] not in (
            {"passed", "skipped"} if owner["allow_skip"] else {"passed"}
        ):
            return False
    return seen == set(planned)


def inspection_policy() -> dict:
    """Require seven frozen Python files, two browser files and the whole helper."""
    policy = json.loads(
        Path(__file__).with_name("inspection-coverage.json").read_text()
    )
    if (
        not isinstance(policy, dict)
        or set(policy)
        != {"schema", "profile", "files", "python", "browser", "frozen_sha256"}
        or type(policy["schema"]) is not int
        or policy["schema"] != 1
        or policy["profile"] != INSPECTION_PROFILE
        or policy["files"] != list(INSPECTION_BROWSER)
    ):
        raise ValueError("Invalid inspection coverage policy")
    if not isinstance(policy["browser"], dict) or set(policy["browser"]) != set(
        BROWSER_PROJECTS
    ):
        raise ValueError("Invalid inspection coverage projects")
    counts = dict(zip(INSPECTION_PYTHON, (7, 8, 3, 21, 8, 29, 4, 7)))
    for project, inventory in [
        ("python", policy["python"]),
        *policy["browser"].items(),
    ]:
        fields = {
            "files",
            "allowed_skips",
            "dynamic_file" if project == "python" else "shards",
        }
        expected_files = (
            counts if project == "python" else dict(zip(INSPECTION_BROWSER, (5, 2)))
        )
        if (
            not isinstance(inventory, dict)
            or set(inventory) != fields
            or inventory["allowed_skips"] != []
            or not isinstance(inventory["files"], dict)
            or set(inventory["files"]) != set(expected_files)
        ):
            raise ValueError("Invalid inspection coverage inventory")
        ids = []
        for file, expected_count in expected_files.items():
            tests = inventory["files"][file]
            if (
                not isinstance(tests, list)
                or len(tests) != expected_count
                or tests != sorted(tests)
                or any(
                    not isinstance(test, str) or not re.fullmatch(r"[0-9a-f]{64}", test)
                    for test in tests
                )
            ):
                raise ValueError("Invalid inspection coverage cases")
            ids.extend(tests)
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate inspection coverage case")
        if (
            project == "python"
            and inventory["dynamic_file"] != INSPECTION_TEST
            or project != "python"
            and inventory["shards"] != [list(INSPECTION_BROWSER)]
        ):
            raise ValueError("Invalid inspection coverage topology")
    frozen = policy["frozen_sha256"]
    files = (set(INSPECTION_PYTHON) - {INSPECTION_TEST}) | set(INSPECTION_BROWSER)
    if (
        not isinstance(frozen, dict)
        or set(frozen) != files
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in frozen.values()
        )
    ):
        raise ValueError("Invalid frozen inspection source")
    root = Path(__file__).resolve().parents[2]
    for file, digest in frozen.items():
        path = root / file
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Changed frozen inspection source")
    return policy
