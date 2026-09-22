"""Real persistence checks using isolated accounts in the selected metadata DB."""
from uuid import uuid4

from schemii.common.auth.routes import AccountCreate, AccountUpdate
from schemii.common.auth.service import AuthService


def test_sessions_roles_and_disable_are_visible_across_auth_instances(postgres_metadata):
    factory=postgres_metadata.connection_factory
    first=AuthService(factory,enabled=True,setup_token='integration-only-not-used')
    second=AuthService(factory,enabled=True,setup_token='integration-only-not-used')
    username='it_'+uuid4().hex
    role_id='role_'+uuid4().hex
    user=first.create_user(AccountCreate(username=username,display_name='Integration account',password='integration-password-123'))
    try:
        token,_=first.login(username,'integration-password-123')
        assert second.resolve(token)['id']==user['id']
        assert second.capabilities(user['id'])==[]
        with first.store.transaction(write=True) as state:
            state['roles'][role_id]=dict(id=role_id,name=role_id,capabilities=['author'],user_ids=[user['id']],connections=[],dashboards=[])
        assert second.capabilities(user['id'])==['author']
        with second.store.transaction(write=True) as state:
            state['roles'][role_id]['capabilities']=[]
        assert first.capabilities(user['id'])==[]
        first.update_user(user['id'],AccountUpdate(disabled=True),user['id'])
        assert second.resolve(token) is None
        assert second.user(user['id']) is None
        with factory() as connection:
            row=connection.execute('SELECT password_hash FROM metadata.auth_accounts WHERE user_id=%s',(user['id'],)).fetchone()
            assert 'integration-password' not in row['password_hash']
            assert connection.execute('SELECT count(*) AS n FROM metadata.auth_sessions WHERE user_id=%s',(user['id'],)).fetchone()['n']==0
    finally:
        with factory() as connection:
            connection.execute('DELETE FROM metadata.auth_roles WHERE id=%s',(role_id,))
            connection.execute('DELETE FROM metadata.auth_accounts WHERE user_id=%s',(user['id'],))
            connection.execute('DELETE FROM metadata.auth_login_attempts WHERE username=%s',(username,))
            connection.execute('DELETE FROM metadata.auth_audit WHERE actor_id=%s OR target_id=%s',(user['id'],user['id']))
            connection.execute('DELETE FROM metadata.users WHERE id=%s',(user['id'],))


def test_shared_report_receipt_separates_actor_from_connection_owner(postgres_metadata):
    from datetime import datetime, timezone
    import psycopg
    import pytest
    from pydantic import SecretStr
    from schemii.common.connections.models import PostgresConnectionCreate
    from schemii.schemii.console.repository import (
        ConsoleNotFoundError, ConsoleTarget, PostgresConsoleRepository,
    )

    factory=postgres_metadata.connection_factory
    author=postgres_metadata.owner_id
    viewer='integration_viewer_'+uuid4().hex
    workspace='ws_'+uuid4().hex
    profile=postgres_metadata.repositories.connections.create(author,PostgresConnectionCreate(
        name='Shared reporting identity',host='application-postgres',port=5432,
        database='application_data',username='report_viewer',password=SecretStr('fixture-only-password'),
    ))
    repository=PostgresConsoleRepository(factory)
    now=datetime.now(timezone.utc)
    try:
        with factory() as connection:
            connection.execute('INSERT INTO metadata.users(id,display_name) VALUES(%s,%s)',(viewer,'Report viewer'))
            connection.execute('INSERT INTO schemii.workspaces(id,owner_id,name) VALUES(%s,%s,%s)',(workspace,viewer,'Viewer workspace'))
        target=ConsoleTarget(profile.id,profile.revision,profile.database,'public',connection_owner_id=author)
        shared=repository.reserve(viewer,None,'con_'+uuid4().hex,None,target,('SELECT 1',),100,now)
        loaded=repository.get(viewer,shared.execution.id)
        assert loaded.owner_id==viewer
        assert loaded.target.connection_owner_id==author
        assert loaded.target.connection_id==profile.id
        with pytest.raises(ConsoleNotFoundError):
            repository.get(author,shared.execution.id)

        # Ordinary private reads retain the existing optional-owner API contract.
        private=repository.reserve(author,None,'con_'+uuid4().hex,None,
            ConsoleTarget(profile.id,profile.revision,profile.database,'public'),('SELECT 1',),100,now)
        assert repository.get(author,private.execution.id).target.connection_owner_id is None
        with factory() as connection:
            row=connection.execute('SELECT owner_id,connection_owner_id FROM schemii.console_executions WHERE id=%s',(private.execution.id,)).fetchone()
            assert row['owner_id']==row['connection_owner_id']==author

        # The relaxed source owner applies only to workspace-free report reads.
        with pytest.raises(psycopg.errors.CheckViolation) as failure:
            with factory() as connection:
                connection.execute('UPDATE schemii.console_executions SET workspace_id=%s,workspace_revision=1 WHERE id=%s',(workspace,shared.execution.id))
        assert failure.value.diag.constraint_name=='console_executions_workspace_private_identity'
    finally:
        with factory() as connection:
            connection.execute('DELETE FROM schemii.console_executions WHERE owner_id IN (%s,%s)',(author,viewer))
            connection.execute('DELETE FROM metadata.users WHERE id=%s',(viewer,))
