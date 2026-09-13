from pathlib import Path

import pytest

from schemii.common.admin_config import AdminConfig


def test_admin_config_controls_console_history_and_transient_result_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schemii.toml"
    path.write_text(
        """
[postgres.connections]
maximum_total = 8
maximum_per_identity = 2
acquire_timeout_seconds = 1.5

[console.results]
page_rows = 250
page_memory_bytes = 1048576
maximum_live_read_sessions = 4
result_ttl_seconds = 120

[console.history]
limit = 25
""".strip(),
        encoding="utf-8",
    )

    config = AdminConfig.from_env({"SCHEMII_CONFIG_FILE": str(path)})

    assert config.console_results.page_rows == 250
    assert config.console_history.limit == 25
    assert config.postgres_connections.maximum_total == 8


def test_admin_config_loads_all_resource_policy_groups() -> None:
    config = AdminConfig.from_document(
        {
            "postgres": {
                "catalog": {"maximum_tables": 321},
                "timeouts": {"migration_statement_seconds": 45},
            },
            "console": {
                "maximum_statements_per_run": 12,
                "transaction_idle_seconds": 60,
                "transaction_maximum_seconds": 600,
                "maximum_saved_queries_per_workspace": 25,
                "results": {"maximum_cell_bytes": 64_000},
            },
            "migrations": {"review_ttl_seconds": 600},
            "resources": {
                "maximum_connections_per_user": 10,
                "maximum_workspaces_per_user": 20,
                "design_history_actions_per_workspace": 30,
            },
            "metadata": {
                "limit_events": {"retention_days": 7, "maximum_events": 500}
            },
            "ai": {
                "maximum_proposals_per_turn": 100,
                "maximum_tool_rounds": 80,
            },
        }
    )

    assert config.postgres_catalog.maximum_tables == 321
    assert config.postgres_timeouts.migration_statement_seconds == 45
    assert config.console.maximum_statements_per_run == 12
    assert config.console_results.maximum_cell_bytes == 64_000
    assert config.migrations.review_ttl_seconds == 600
    assert config.resources.maximum_workspaces_per_user == 20
    assert config.limit_events.maximum_events == 500
    assert config.ai.maximum_proposals_per_turn == 100
    assert config.ai.maximum_tool_rounds == 80


def test_admin_config_rejects_cross_policy_capacity_that_starves_requests() -> None:
    with pytest.raises(ValueError, match="leave at least one"):
        AdminConfig.from_document(
            {
                "postgres": {"connections": {"maximum_total": 4}},
                "console": {"results": {"maximum_live_read_sessions": 4}},
            }
        )


def test_admin_config_allows_query_history_to_be_disabled() -> None:
    config = AdminConfig.from_document(
        {"console": {"history": {"limit": 0}}}
    )

    assert config.console_history.limit == 0


def test_admin_config_rejects_unbounded_query_history() -> None:
    with pytest.raises(ValueError, match="console.history.limit"):
        AdminConfig.from_document(
            {"console": {"history": {"limit": 10_001}}}
        )
