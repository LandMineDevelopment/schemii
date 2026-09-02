import pytest

from schemii.common.postgres.trigger_analysis import (
    TriggerDefinitionError,
    analyze_trigger_definition,
)


def test_trigger_contract_is_derived_from_source() -> None:
    contract = analyze_trigger_definition(
        """
        CREATE TRIGGER orders_touch
        BEFORE INSERT OR UPDATE OF total, status ON orders
        FOR EACH ROW
        WHEN (NEW.total > 0 AND OLD.status <> NEW.status)
        EXECUTE FUNCTION touch_order('audit')
        """
    )

    assert contract.name == "orders_touch"
    assert contract.relation_name == "orders"
    assert contract.timing == "before"
    assert contract.events == ("insert", "update")
    assert contract.orientation == "row"
    assert contract.function_name == "touch_order"
    assert contract.function_arguments == ("audit",)
    assert contract.update_columns == ("total", "status")
    assert contract.referenced_columns == ("total", "status")
    assert contract.when_expression == "new.total > 0 AND old.status <> new.status"


def test_constraint_and_transition_contracts_are_derived() -> None:
    constraint = analyze_trigger_definition(
        """
        CREATE CONSTRAINT TRIGGER orders_check
        AFTER INSERT ON orders DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_order()
        """
    )
    transition = analyze_trigger_definition(
        """
        CREATE TRIGGER orders_audit AFTER UPDATE ON orders
        REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
        FOR EACH STATEMENT EXECUTE FUNCTION audit_orders()
        """
    )

    assert constraint.constraint is True
    assert constraint.deferrable is True
    assert constraint.initially_deferred is True
    assert transition.transition_relations == (
        "OLD TABLE AS old_rows",
        "NEW TABLE AS new_rows",
    )


@pytest.mark.parametrize(
    ("definition", "code"),
    [
        ("SELECT 1", "create_trigger_required"),
        (
            "CREATE TRIGGER audit AFTER INSERT ON public.orders EXECUTE FUNCTION audit_orders()",
            "target_independent_relation_required",
        ),
        (
            "CREATE TRIGGER one AFTER INSERT ON orders EXECUTE FUNCTION f(); SELECT 2",
            "multiple_statements",
        ),
        ("CREATE TRIGGER not_complete", "invalid_syntax"),
    ],
)
def test_invalid_trigger_contracts_are_rejected(definition: str, code: str) -> None:
    with pytest.raises(TriggerDefinitionError) as caught:
        analyze_trigger_definition(definition)

    assert caught.value.code == code
