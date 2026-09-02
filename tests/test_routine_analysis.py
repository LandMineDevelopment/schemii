import pytest

from schemii.common.postgres.routine_analysis import (
    RoutineDefinitionError,
    analyze_routine_definition,
)


def test_function_contract_is_derived_from_source() -> None:
    contract = analyze_routine_definition(
        """
        CREATE OR REPLACE FUNCTION calculate_total(
            IN subtotal numeric(12, 2),
            tax_rate numeric(5, 4) DEFAULT 0,
            OUT total numeric(12, 2)
        )
        RETURNS numeric(12, 2)
        LANGUAGE sql
        IMMUTABLE
        AS $routine$
            SELECT subtotal * (1 + tax_rate)
        $routine$;
        """
    )

    assert contract.name == "calculate_total"
    assert contract.kind == "function"
    assert contract.language == "sql"
    assert contract.return_type == "numeric(12, 2)"
    assert contract.identity_arguments == "numeric(12, 2), numeric(5, 4)"
    assert contract.arguments == "IN subtotal numeric(12, 2), tax_rate numeric(5, 4) DEFAULT 0"


def test_procedure_and_variadic_identity_are_derived() -> None:
    contract = analyze_routine_definition(
        """
        CREATE PROCEDURE refresh_cache(IN scope text, VARIADIC tags text[])
        LANGUAGE plpgsql
        AS $$ BEGIN NULL; END $$
        """
    )

    assert contract.kind == "procedure"
    assert contract.return_type is None
    assert contract.identity_arguments == "text, VARIADIC text[]"


def test_set_returning_and_table_return_contracts_are_supported() -> None:
    set_returning = analyze_routine_definition(
        "CREATE FUNCTION labels() RETURNS SETOF text LANGUAGE sql AS $$ SELECT 'x' $$"
    )
    table_returning = analyze_routine_definition(
        "CREATE FUNCTION labels_with_ids() RETURNS TABLE(id bigint, label text) LANGUAGE sql AS $$ SELECT 1, 'x' $$"
    )

    assert set_returning.return_type == "SETOF text"
    assert table_returning.return_type == "TABLE (id bigint, label text)"
    assert table_returning.arguments == ""


@pytest.mark.parametrize(
    ("definition", "code"),
    [
        ("SELECT 1", "create_routine_required"),
        (
            "CREATE FUNCTION public.example() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$",
            "target_independent_name_required",
        ),
        (
            "CREATE FUNCTION example() RETURNS integer AS $$ SELECT 1 $$",
            "language_required",
        ),
        (
            "CREATE PROCEDURE example() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$",
            "invalid_syntax",
        ),
        (
            "CREATE FUNCTION one() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$; SELECT 2",
            "multiple_statements",
        ),
    ],
)
def test_invalid_routine_contracts_are_rejected(definition: str, code: str) -> None:
    with pytest.raises(RoutineDefinitionError) as caught:
        analyze_routine_definition(definition)

    assert caught.value.code == code
