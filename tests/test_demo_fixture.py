"""Behavioral checks for the resettable demo fixture orchestration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_demo_fixture():
    path = ROOT / "dev" / "postgres" / "demo-fixture.py"
    spec = importlib.util.spec_from_file_location("schemii_demo_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_cleanup_uses_the_application_metadata_composition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _load_demo_fixture()
    scenario_directory = tmp_path / "demo-scenarios" / "baseline"
    scenario_directory.mkdir(parents=True)
    manifest = {
        "id": "baseline",
        "designAlterations": [],
        "targetAlteration": "target.sql",
    }
    metadata_config = SimpleNamespace(password_file="/unused")
    application_metadata = object()
    services = SimpleNamespace(metadata=application_metadata)
    events: list[str] = []

    monkeypatch.setattr(fixture, "_scenario", lambda: (scenario_directory, manifest))
    monkeypatch.setattr(
        fixture,
        "_required_environment",
        lambda name: {
            "SCHEMII_DEMO_SOURCE_REVISION": "a" * 40,
            "SCHEMII_DEMO_POSTGRES_USER": "schemii",
        }[name],
    )
    monkeypatch.setattr(fixture, "_metadata_config", lambda: metadata_config)
    monkeypatch.setattr(fixture, "read_secret_file", lambda *_args: "secret")
    monkeypatch.setattr(fixture, "_fixture_digest", lambda *_args: "digest")

    def create_services():
        events.append("compose")
        return services

    class CleanupReached(RuntimeError):
        pass

    def cleanup(config, repositories, **_kwargs):
        assert config is metadata_config
        assert repositories is application_metadata
        events.append("cleanup")
        raise CleanupReached

    monkeypatch.setattr(fixture, "create_services", create_services)
    monkeypatch.setattr(fixture, "_cleanup_and_create_connection", cleanup)

    with pytest.raises(CleanupReached):
        fixture.main()

    assert events == ["compose", "cleanup"]


def test_demo_console_queries_are_seeded_as_first_visit_starters(
    tmp_path: Path,
) -> None:
    fixture = _load_demo_fixture()
    query_path = tmp_path / "guided.sql"
    query_path.write_text("  SELECT 42 AS answer;\n", encoding="utf-8")
    created: list[tuple[str, str, object]] = []

    class Console:
        def create_saved_query(self, owner_id, workspace_id, request):
            created.append((owner_id, workspace_id, request))

    fixture._seed_console_queries(
        SimpleNamespace(console=Console()),
        "ws_" + "a" * 32,
        tmp_path,
        [{"name": "01 · Guided query", "file": "guided.sql"}],
    )

    assert len(created) == 1
    owner_id, workspace_id, request = created[0]
    assert owner_id == fixture.LOCAL_PROTOTYPE_USER_ID
    assert workspace_id == "ws_" + "a" * 32
    assert request.name == "01 · Guided query"
    assert request.sql == "SELECT 42 AS answer;"
    assert request.starter is True
