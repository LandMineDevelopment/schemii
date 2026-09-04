from unittest.mock import MagicMock

import pytest

from schemii.common.postgres import PsycopgPostgresGateway
from schemii.common.postgres.errors import PostgresConnectionCapacityError


def setup_gateway(monkeypatch, *, row=None, error=None):
    gateway = PsycopgPostgresGateway()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    cursor.execute.side_effect = error
    monkeypatch.setattr(gateway, "_connect", lambda _: connection)
    begin = MagicMock()
    statements = MagicMock()
    cleanup = MagicMock()
    monkeypatch.setattr(gateway, "_begin_read_only", begin)
    monkeypatch.setattr(gateway, "_execute_statement", statements)
    monkeypatch.setattr(gateway, "_cleanup", cleanup)
    return gateway, connection, cursor, begin, statements, cleanup


@pytest.mark.parametrize("row", [{"invalid": False}, {"count": 123456}])
def test_conversion_validation_returns_no_rows_and_always_cleans_up(monkeypatch, row):
    gateway, connection, cursor, begin, statements, cleanup = setup_gateway(monkeypatch, row=row)
    assert gateway.validate_column_conversion(None, "compiled conversion check") is None
    begin.assert_called_once_with(connection)
    cursor.execute.assert_called_once_with("compiled conversion check")
    assert [call.args[1] for call in statements.call_args_list] == [
        "SET LOCAL row_security = off", "SET LOCAL extra_float_digits = 3"]
    cleanup.assert_called_once_with(connection)
    connection.commit.assert_not_called()


def test_conversion_reports_data_loss_without_returning_values(monkeypatch):
    gateway, connection, _, _, _, cleanup = setup_gateway(monkeypatch, row={"invalid": True})
    message = gateway.validate_column_conversion(None, "compiled conversion check")
    assert message and "value" in message.lower()
    cleanup.assert_called_once_with(connection)


@pytest.mark.parametrize("row", [None, {}, {"invalid": "false"}, {"count": -1}])
def test_conversion_validation_fails_closed_for_missing_or_invalid_evidence(monkeypatch, row):
    gateway, _, _, _, _, _ = setup_gateway(monkeypatch, row=row)
    assert gateway.validate_column_conversion(None, "compiled conversion check")


@pytest.mark.parametrize("state", ["22P02", "22003", "22001", "42501", "57014", "unrecognized"])
def test_postgres_validation_errors_never_expose_raw_data(monkeypatch, state):
    error = RuntimeError('invalid input value "sensitive customer value"')
    error.sqlstate = state
    gateway, connection, _, _, _, cleanup = setup_gateway(monkeypatch, error=error)
    message = gateway.validate_column_conversion(None, "compiled conversion check")
    assert message
    assert "sensitive customer value" not in message
    cleanup.assert_called_once_with(connection)


def test_capacity_exception_keeps_resource_limit_handling(monkeypatch):
    error = PostgresConnectionCapacityError("postgres.maximum_connections", 2, 2)
    gateway, connection, _, _, _, cleanup = setup_gateway(monkeypatch, error=error)
    with pytest.raises(PostgresConnectionCapacityError) as caught:
        gateway.validate_column_conversion(None, "compiled conversion check")
    assert caught.value is error
    cleanup.assert_called_once_with(connection)
