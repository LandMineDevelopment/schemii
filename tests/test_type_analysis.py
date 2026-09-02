import pytest

from schemii.common.postgres.type_analysis import (
    TypeDefinitionError,
    analyze_type_definition,
)


def test_enum_contract_is_derived_from_postgresql_source() -> None:
    contract = analyze_type_definition(
        "CREATE TYPE order_status AS ENUM ('draft', 'in review', 'fulfilled')"
    )

    assert contract.name == "order_status"
    assert contract.kind == "enum"
    assert contract.enum_values == ("draft", "in review", "fulfilled")
    assert contract.base_type is None
    assert contract.checks == ()


def test_domain_contract_includes_base_behavior_and_named_checks() -> None:
    contract = analyze_type_definition(
        """
        CREATE DOMAIN positive_amount AS numeric(12, 2)
        COLLATE "C"
        DEFAULT 0
        NOT NULL
        CONSTRAINT positive_amount_nonnegative CHECK (VALUE >= 0)
        """
    )

    assert contract.name == "positive_amount"
    assert contract.kind == "domain"
    assert contract.base_type == "numeric(12, 2)"
    assert contract.base_type_name is None
    assert contract.default_expression == "0"
    assert contract.not_null is True
    assert contract.collation == '"C"'
    assert [(check.name, check.expression) for check in contract.checks] == [
        ("positive_amount_nonnegative", "value >= 0")
    ]


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("CREATE TABLE example (id integer)", "create_type_required"),
        ("CREATE TYPE public.mood AS ENUM ('ok')", "target_independent_name_required"),
        ("CREATE DOMAIN label AS public.custom_text", "target_independent_base_type_required"),
        ("CREATE TYPE mood AS ENUM ('ok', 'ok')", "duplicate_enum_value"),
        ("CREATE TYPE mood AS ENUM ('ok'); CREATE TYPE state AS ENUM ('new')", "multiple_statements"),
    ],
)
def test_unsupported_or_target_bound_type_source_is_rejected(source: str, code: str) -> None:
    with pytest.raises(TypeDefinitionError) as caught:
        analyze_type_definition(source)

    assert caught.value.code == code
