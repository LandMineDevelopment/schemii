"""Real PostgreSQL AI metadata contract; run only through ./start.sh.

The launcher pipes this script into the installed application container. It
creates an isolated metadata owner, exercises durable audit rows, and deletes
only that owner afterward. It never connects to a user target or AI provider.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from uuid import uuid4

from psycopg.errors import CheckViolation
from schemii.common.metadata.factory import create_metadata_repositories
from schemii.common.metadata.migrations import MIGRATION_PACKAGE as COMMON
from schemii.schemii.metadata import MIGRATION_PACKAGE as SCHEMII
from schemii.schemoo.metadata.migrations import MIGRATION_PACKAGE as SCHEMOO
from schemii.schemer.metadata.migrations import MIGRATION_PACKAGE as SCHEMER
from schemii.schemii.ai.action_policy import required_action_ids
from schemii.schemii.ai.models import AiCapabilities
from schemii.schemii.ai.repository import AiNotFoundError, PostgresAiRepository
from schemii.schemii.ai.tools import normalize_tool_call
from schemii.schemii.workspaces.models import WorkspaceCreateRecord
from schemii.schemii.workspaces.postgres_store import PostgresWorkspaceRepository


def main():
    metadata = create_metadata_repositories(os.environ, migration_packages=(COMMON, SCHEMII, SCHEMOO, SCHEMER))
    if metadata.storage != 'postgresql' or metadata.connection_factory is None:
        raise RuntimeError('AI operation contract requires the launcher PostgreSQL metadata stack')
    factory = metadata.connection_factory
    owner = 'ai-operation-contract-' + uuid4().hex
    checked = []
    try:
        workspace = PostgresWorkspaceRepository(factory).create(owner, WorkspaceCreateRecord(name='AI operation contract'))
        repository = PostgresAiRepository(factory)
        caps = AiCapabilities(action_modes={'console.create':'automatic','query.saved.read':'automatic'})
        chat = repository.create_chat(owner, workspace.id, 'Metadata contract', 'contract', 'no-inference', caps)
        turn, _ = repository.create_turn(owner, chat.id, 'Check durable action kinds', None, 1000, 10, 100)
        repository.claim_turn(owner, chat.id, turn.id)
        for tool, arguments in [
            ('schemii_raw_console', {'operation':'create'}),
            ('schemii_app_action', {'operation':'list_saved_queries','args':{}}),
        ]:
            normalized = normalize_tool_call(tool, arguments)
            digest = hashlib.sha256(json.dumps(normalized.action, sort_keys=True).encode()).hexdigest()
            proposal = repository.create_proposal(owner, chat.id, turn.id, normalized.capability,
                normalized.action_type, normalized.summary, normalized.action, digest,
                workspace.revision, 1, normalized.destructive,
                datetime.now(timezone.utc) + timedelta(minutes=5), chat.revision)
            operation, claimed, payload = repository.begin_operation(owner, chat.id, proposal.id,
                proposal.revision, digest, chat.revision, normalized.capability,
                datetime.now(timezone.utc), required_actions=required_action_ids(normalized.action_type, normalized.action))
            assert operation.kind == normalized.action_type
            assert operation.status == 'running' and claimed.status == 'executing'
            assert payload == normalized.action
            summary = {'effect':'metadata_contract_only','targetExecuted':False,'providerCalled':False}
            finished = repository.finish_operation(owner, chat.id, operation.id, status='succeeded', result_summary=summary)
            fresh = PostgresAiRepository(factory)
            persisted, persisted_action = fresh.operation_action(owner, chat.id, operation.id)
            assert persisted.id == finished.id and persisted.kind == normalized.action_type
            assert persisted.status == 'succeeded' and persisted.result_summary == summary
            assert persisted_action == normalized.action
            assert fresh.get_proposal(owner, chat.id, proposal.id).digest == digest
            assert fresh.get_proposal(owner, chat.id, proposal.id).status == 'succeeded'
            try:
                fresh.get_operation(owner + '-other', chat.id, operation.id)
            except AiNotFoundError:
                pass
            else:
                raise AssertionError('Operation audit escaped its owner boundary')
            # Exercise the database CHECK itself, bypassing application enums.
            try:
                with factory() as connection, connection.cursor() as cursor:
                    cursor.execute('UPDATE schemii.ai_operations SET kind=%s WHERE owner_id=%s AND id=%s',
                                   ('invented_contract_kind', owner, operation.id))
            except CheckViolation as error:
                assert error.diag.constraint_name == 'ai_operations_kind_check'
            else:
                raise AssertionError('Database accepted an invented operation kind')
            assert fresh.get_operation(owner, chat.id, operation.id).kind == normalized.action_type
            checked.append(normalized.action_type)
        repository.finish_turn(owner, chat.id, turn.id, 'Metadata contract passed')
        failed, _ = repository.create_turn(owner, chat.id, 'Failure recovery check', None, 1000, 10, 100)
        repository.claim_turn(owner, chat.id, failed.id)
        repository.fail_turn(owner, chat.id, failed.id, 'contract_failure', 'Deliberate metadata contract failure')
        fresh = PostgresAiRepository(factory)
        assert fresh.get_chat(owner, chat.id).status == 'failed'
        history = fresh.list_messages(owner, chat.id, 100)
        followup, message = fresh.create_turn(owner, chat.id, 'Follow up after failure', None, 1000, 10, 100)
        assert followup.id != failed.id and followup.status == 'queued'
        assert fresh.get_chat(owner, chat.id).status == 'working'
        assert fresh.get_turn(owner, chat.id, failed.id).status == 'failed'
        assert fresh.list_messages(owner, chat.id, 100)[:-1] == history
        assert message.text == 'Follow up after failure'
        fresh.claim_turn(owner, chat.id, followup.id)
        fresh.finish_turn(owner, chat.id, followup.id, 'Failure recovery verified')
        checked.append('failed_turn_followup')
    finally:
        with factory() as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM metadata.users WHERE id=%s', (owner,))
        with factory() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) AS count FROM schemii.ai_operations WHERE owner_id=%s', (owner,))
            assert cursor.fetchone()['count'] == 0
    print('AI PostgreSQL operation contract passed: ' + ', '.join(checked) + '; ownership, CHECK rejection, durable audit, isolated cleanup')


if __name__ == '__main__':
    main()
