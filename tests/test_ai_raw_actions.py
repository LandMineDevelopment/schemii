"""Raw console authority cannot bypass controls through an alternate SQL route."""
from types import SimpleNamespace
import pytest
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.action_policy import disabled_action_ids, requires_approval
from schemii.schemii.ai.raw_actions import RawConsoleAction, required_actions, results
from schemii.schemii.ai.tools import normalize_tool_call, tool_enabled
from schemii.common.api.errors import ApiProblem
from test_raw_console import policy_fixture
from schemii.schemii.console.raw_session import SqlCreate

@pytest.mark.parametrize(('sql', 'permission'), [('COMMIT', 'console.commit'), ('ROLLBACK', 'console.rollback'), ('BEGIN', 'console.begin'), ('EXPLAIN SELECT 1', 'query.explain'), ('EXPLAIN ANALYZE SELECT 1', 'query.analyze'), ("PREPARE TRANSACTION 'x'", 'console.commit')])
def test_sql_route_retains_independent_authority(sql, permission):
    action = {'operation':'execute', 'sql':sql}
    assert permission in required_actions(action)
    assert permission in disabled_action_ids(AiCapabilities(action_modes={'console.execute':'automatic'}), 'raw_console', action)


def test_new_actions_default_disabled_and_honor_ask():
    assert not tool_enabled('schemii_raw_console', AiCapabilities(sql_write_execute=True))
    caps = AiCapabilities(action_modes={'console.inspect':'ask'})
    assert tool_enabled('schemii_raw_console', caps)
    assert requires_approval(caps, 'raw_console', {'operation':'inspect'})
    assert not tool_enabled('schemii_raw_console', caps, {'operation':'commit'})


def test_analyze_requires_execution_and_autocommit_requires_commit():
    assert set(required_actions({'operation':'explain', 'analyze':True, 'commitMode':'each_statement'})) == {'console.explain','query.analyze','console.execute','console.commit'}


def test_reviewed_revision_is_required_and_exact_sql_preserved():
    sql = '/* user text */ SELECT 1; COMMIT;'
    with pytest.raises(ValueError):
        RawConsoleAction(operation='execute', sessionId='raw_'+'a'*32, sql=sql)
    proposal = normalize_tool_call('schemii_raw_console', {'operation':'execute','sessionId':'raw_'+'a'*32,'expectedRevision':1,'sql':sql})
    assert proposal.action['sql'] == sql


def test_revocation_before_whole_run_commit_rolls_back():
    manager, session = policy_fixture()
    body = SqlCreate(sql='SELECT 1', commit_mode='whole_run')
    receipt = manager.reserve(session, body)
    manager.run(session, receipt, body, is_authorized=lambda: 'SELECT 1' not in session['raw'].sent)
    assert session['raw'].sent == ['BEGIN','SELECT 1','ROLLBACK']
    assert receipt['status'] == 'cancelled'


def test_revocation_between_manual_statements_stops_before_commit():
    manager, session = policy_fixture()
    body = SqlCreate(sql='SELECT 1; COMMIT', commit_mode='manual')
    receipt = manager.reserve(session, body)
    manager.run(session, receipt, body, is_authorized=lambda: 'SELECT 1' not in session['raw'].sent)
    assert session['raw'].sent == ['BEGIN','SELECT 1']
    assert receipt['transactionStatus'] == 'intrans'


def test_rows_require_separate_disclosure_permission():
    with pytest.raises(ApiProblem):
        results(SimpleNamespace(), 'owner', SimpleNamespace(capabilities=AiCapabilities()), {'sessionId':'raw_'+'a'*32,'executionId':'rex_'+'b'*32})


def test_copy_ticket_checks_revocation():
    manager, _ = policy_fixture()
    with pytest.raises(ApiProblem):
        manager.authorize_ticket({'isAuthorized':lambda:False})


def test_raw_plan_supports_same_write_statement_as_human_console():
    from schemii.schemii.console.raw_session import build_raw_explain_sql
    sql = 'UPDATE things SET value=1 RETURNING value'
    assert build_raw_explain_sql(sql, True).endswith(sql)
    assert 'ANALYZE' in build_raw_explain_sql(sql, True)
    with pytest.raises(ApiProblem):
        build_raw_explain_sql('SELECT 1; COMMIT', False)


def test_unknown_sql_syntax_cannot_skip_transaction_permissions():
    ids = required_actions({'operation':'execute', 'sql':'unrecognized future syntax'})
    assert {'console.begin','console.commit','console.rollback','query.explain','query.analyze'} <= set(ids)


def test_result_budget_covers_all_results_and_diagnostics_without_mutating_receipt():
    import json
    sid, eid = 'raw_'+'a'*32, 'rex_'+'b'*32
    receipt = {'status':'succeeded', 'results':[{'columns':[{'name':'v'}], 'rows':[['x'*200] for _ in range(10)]} for _ in range(4)], 'notices':[{'message':'z'*20000}], 'errorMessage':'y'*20000}
    ai = SimpleNamespace(policy=SimpleNamespace(result_context_rows=3,result_context_bytes=1600),raw_console=SimpleNamespace(get=lambda *args:{'executions':{eid:receipt}}))
    result = results(ai, 'owner', SimpleNamespace(workspace_id='ws',capabilities=AiCapabilities(structured_data_read=True)),{'sessionId':sid,'executionId':eid})
    assert sum(len(item['rows']) for item in result['results']) <= 3
    assert len(json.dumps(result,ensure_ascii=False).encode()) <= 1600
    assert result['sampled']
    assert len(receipt['results'][0]['rows']) == 10
    assert not result['notices']


def test_permission_descriptions_reflect_application_and_raw_analysis_scope():
    from schemii.schemii.ai.action_policy import permission_descriptors
    descriptions = {item['id']:item['description'] for item in permission_descriptors()}
    assert 'Saved design only' not in descriptions['workspace.delete']
    assert 'Passwords' in descriptions['connection.inspect']
    assert 'can modify data' in descriptions['query.analyze']
