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
