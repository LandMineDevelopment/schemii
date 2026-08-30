from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _service(compose: str, name: str) -> str:
    lines = compose.splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ")
            and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_compose_keeps_postgres_private_and_never_mounts_docker_socket() -> None:
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    schemii = _service(compose, "schemii")
    ingress = _service(compose, "ingress")
    postgres = _service(compose, "postgres")
    networks = compose.split("\nnetworks:\n", 1)[1]

    assert "/docker.sock" not in compose
    assert "ports:" not in schemii
    assert "ports:" not in postgres
    assert '"127.0.0.1:${SCHEMII_TEST_APP_PORT:-8001}:8080"' in ingress
    assert "  database:\n    internal: true" in networks
    assert "  app-ingress:\n    internal: true" in networks
    assert "postgres:17-alpine@sha256:" in compose
    assert compose.count("ports:") == 1
    assert "condition: service_completed_successfully" in compose
    assert "condition: service_healthy\n        restart: true" in ingress
    assert "      - database\n      - app-ingress" in schemii
    assert "      - app-ingress\n      - loopback" in ingress


def test_containerized_application_is_non_root_and_read_only() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.test.yaml").read_text(encoding="utf-8")
    schemii = _service(compose, "schemii")
    ingress = _service(compose, "ingress")
    seed = _service(compose, "postgres-seed")

    assert "USER 10001:10001" in dockerfile
    assert "schemii.main:app" in dockerfile
    assert "--constraint constraints.docker.txt" in dockerfile
    assert "read_only: true" in schemii
    assert 'user: "101:101"' in ingress
    assert "read_only: true" in ingress
    assert "user: postgres" in seed
    assert "no-new-privileges:true" in schemii
    assert "no-new-privileges:true" in ingress
    assert "no-new-privileges:true" in seed


def test_seed_contains_archived_tutorial_and_catalog_coverage_namespaces() -> None:
    seed = (ROOT / "dev" / "postgres" / "seed.sql").read_text(encoding="utf-8")

    for expected in (
        "CREATE SCHEMA bookstore",
        "CREATE TABLE bookstore.order_items",
        "CREATE MATERIALIZED VIEW bookstore.monthly_sales",
        "CREATE SCHEMA catalog_lab",
        "PARTITION BY RANGE",
        "FOREIGN KEY (tenant_id, account_id)",
        "EXCLUDE USING gist",
        "CREATE PROCEDURE catalog_lab.remove_finished_jobs",
        "WITH NO DATA",
        "fixture=v1",
    ):
        assert expected in seed
