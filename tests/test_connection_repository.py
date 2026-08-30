import pytest
from pydantic import ValidationError

from schemii.common.connections.models import (
    PostgresConnectionCreate,
    PostgresConnectionUpdate,
)
from schemii.common.connections.store import (
    ConnectionConflictError,
    ConnectionLimitError,
    ConnectionNotFoundError,
    InMemoryConnectionRepository,
)


def request(password: str | None = "secret") -> PostgresConnectionCreate:
    return PostgresConnectionCreate(
        name="Reporting",
        host="localhost",
        database="analytics",
        username="reader",
        password=password,
        ssl_mode="require",
    )


def test_connections_are_owner_scoped_and_profiles_are_redacted() -> None:
    repository = InMemoryConnectionRepository()
    profile = repository.create("owner-a", request())

    assert repository.list("owner-a") == [profile]
    assert repository.list("owner-b") == []
    assert "password" not in profile.model_dump()
    assert repository.resolve("owner-a", profile.id).password.get_secret_value() == "secret"

    try:
        repository.get("owner-b", profile.id)
    except ConnectionNotFoundError:
        pass
    else:  # pragma: no cover - assertion branch.
        raise AssertionError("another owner accessed the connection")


def test_update_preserves_replaces_and_removes_password() -> None:
    repository = InMemoryConnectionRepository()
    original = repository.create("owner", request())

    renamed = repository.update(
        "owner",
        original.id,
        PostgresConnectionUpdate(
            expected_revision=original.revision,
            name="Renamed",
        ),
    )
    assert renamed.revision == 2
    assert repository.resolve("owner", original.id).password.get_secret_value() == "secret"

    replaced = repository.update(
        "owner",
        original.id,
        PostgresConnectionUpdate(expected_revision=2, password="new secret"),
    )
    assert replaced.credential_stored is True
    assert repository.resolve("owner", original.id).password.get_secret_value() == "new secret"

    passwordless = repository.update(
        "owner",
        original.id,
        PostgresConnectionUpdate(expected_revision=3, password=None),
    )
    assert passwordless.credential_stored is False
    assert repository.resolve("owner", original.id).password is None


def test_stale_updates_and_deletes_fail_closed() -> None:
    repository = InMemoryConnectionRepository()
    profile = repository.create("owner", request())
    repository.update(
        "owner",
        profile.id,
        PostgresConnectionUpdate(expected_revision=1, name="Changed"),
    )

    for operation in (
        lambda: repository.update(
            "owner",
            profile.id,
            PostgresConnectionUpdate(expected_revision=1, name="Stale"),
        ),
        lambda: repository.delete("owner", profile.id, 1),
    ):
        try:
            operation()
        except ConnectionConflictError as error:
            assert error.current_revision == 2
        else:  # pragma: no cover - assertion branch.
            raise AssertionError("stale mutation was accepted")


def test_connection_targets_are_exact_and_single_host() -> None:
    exact = PostgresConnectionCreate(
        name=" Reporting ",
        host=" localhost ",
        database=" analytics ",
        username=" reader ",
    )

    assert exact.name == "Reporting"
    assert exact.host == "localhost"
    assert exact.database == " analytics "
    assert exact.username == " reader "
    assert exact.ssl_mode.value == "verify-full"

    with pytest.raises(ValidationError):
        PostgresConnectionCreate(
            name="Reporting",
            host="primary,replica",
            database="analytics",
            username="reader",
        )

    with pytest.raises(ValidationError):
        PostgresConnectionCreate(
            name="Reporting",
            host="localhost",
            port=True,
            database="analytics",
            username="reader",
        )


def test_connection_repository_has_a_bounded_owner_capacity() -> None:
    repository = InMemoryConnectionRepository(max_connections_per_owner=1)
    repository.create("owner", request())

    with pytest.raises(ConnectionLimitError) as caught:
        repository.create("owner", request())

    assert caught.value.limit == 1
