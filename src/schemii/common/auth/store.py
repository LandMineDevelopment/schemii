"""Transactional account storage; PostgreSQL is authoritative on every request."""
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
import json


class AuthStore:
    def __init__(self, connection_factory=None):
        self.factory = connection_factory
        self.lock = RLock()
        self.state = {"users": {}, "sessions": {}, "roles": {}, "attempts": {}, "audit": []}

    @contextmanager
    def transaction(self, write=False):
        if self.factory is None:
            with self.lock:
                state = deepcopy(self.state)
                yield state
                if write:
                    self.state = state
            return
        with self.factory() as connection:
            with connection.cursor() as cursor:
                # Serialize identity mutations (including bootstrap and last-admin checks).
                if write:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (0x53434841555448,))
                else:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SELECT a.*, u.display_name FROM metadata.auth_accounts a JOIN metadata.users u ON u.id=a.user_id")
                users = {r['user_id']: {**r, 'id': r['user_id']} for r in cursor.fetchall()}
                cursor.execute("SELECT * FROM metadata.auth_sessions WHERE expires_at > extract(epoch from now())")
                sessions = {r['token_hash']: r for r in cursor.fetchall()}
                cursor.execute("SELECT * FROM metadata.auth_roles ORDER BY name")
                roles = {r['id']: {**r, 'user_ids': [], 'connections': [], 'dashboards': []} for r in cursor.fetchall()}
                cursor.execute("SELECT * FROM metadata.auth_user_roles")
                for row in cursor.fetchall(): roles[row['role_id']]['user_ids'].append(row['user_id'])
                for table, key in [('auth_role_connections', 'connections'), ('auth_role_dashboards', 'dashboards')]:
                    cursor.execute('SELECT * FROM metadata.' + table)
                    for row in cursor.fetchall():
                        role = row.pop('role_id')
                        roles[role][key].append(row)
                cursor.execute('SELECT * FROM metadata.auth_login_attempts WHERE expires_at > extract(epoch from now())')
                attempts = {r['username']: r for r in cursor.fetchall()}
                state = {'users': users, 'sessions': sessions, 'roles': roles, 'attempts': attempts, 'audit': []}
                before = deepcopy(state) if write else None
                yield state
                if not write: return
                for user in state['users'].values():
                    if user == before['users'].get(user['id']): continue
                    cursor.execute("INSERT INTO metadata.users(id,display_name) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name", (user['id'], user['display_name']))
                    cursor.execute("INSERT INTO metadata.auth_accounts(user_id,username,password_hash,is_admin,disabled) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,is_admin=excluded.is_admin,disabled=excluded.disabled", tuple(user[k] for k in ('id','username','password_hash','is_admin','disabled')))
                for user_id in before['users'].keys() - state['users'].keys():
                    # Preserve metadata.users and owned work; dependent sign-in grants cascade.
                    cursor.execute('DELETE FROM metadata.auth_accounts WHERE user_id=%s',(user_id,))
                for username in before['attempts'].keys() - state['attempts'].keys():
                    cursor.execute('DELETE FROM metadata.auth_login_attempts WHERE username=%s',(username,))
                for username, attempt in state['attempts'].items():
                    if attempt != before['attempts'].get(username):
                        cursor.execute('INSERT INTO metadata.auth_login_attempts VALUES(%s,%s,%s) ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,expires_at=excluded.expires_at',(username,attempt['attempts'],attempt['expires_at']))
                for token in before['sessions'].keys() - state['sessions'].keys():
                    cursor.execute('DELETE FROM metadata.auth_sessions WHERE token_hash=%s',(token,))
                cursor.execute('DELETE FROM metadata.auth_sessions WHERE expires_at <= extract(epoch from now())')
                cursor.execute('DELETE FROM metadata.auth_login_attempts WHERE expires_at <= extract(epoch from now())')
                for token, session in state['sessions'].items():
                    if token not in before['sessions']:
                        cursor.execute('INSERT INTO metadata.auth_sessions VALUES(%s,%s,%s)', (token,session['user_id'],session['expires_at']))
                for role_id in before['roles'].keys() - state['roles'].keys():
                    cursor.execute('DELETE FROM metadata.auth_roles WHERE id=%s',(role_id,))
                for role in state['roles'].values():
                    if role == before['roles'].get(role['id']): continue
                    previous = before['roles'].get(role['id'])
                    if previous:
                        if (role['name'],role['capabilities']) != (previous['name'],previous['capabilities']):
                            cursor.execute('UPDATE metadata.auth_roles SET name=%s,capabilities=%s::jsonb WHERE id=%s',
                                           (role['name'],json.dumps(role['capabilities']),role['id']))
                    else:
                        cursor.execute('INSERT INTO metadata.auth_roles VALUES(%s,%s,%s::jsonb)',
                                       (role['id'],role['name'],json.dumps(role['capabilities'])))
                    for user_id in set(previous['user_ids']) - set(role['user_ids']) if previous else ():
                        cursor.execute('DELETE FROM metadata.auth_user_roles WHERE role_id=%s AND user_id=%s',(role['id'],user_id))
                    for user_id in set(role['user_ids']) - set(previous['user_ids'] if previous else []):
                        cursor.execute('INSERT INTO metadata.auth_user_roles(user_id,role_id) VALUES(%s,%s)',(user_id,role['id']))
                    for key, table, columns in [
                        ('connections','auth_role_connections',('connection_id','owner_id','allow_authoring')),
                        ('dashboards','auth_role_dashboards',('dashboard_id','owner_id','connection_id','connection_owner_id','can_export','can_drill')),
                    ]:
                        if previous and role[key] == previous[key]:
                            continue
                        cursor.execute('DELETE FROM metadata.'+table+' WHERE role_id=%s',(role['id'],))
                        placeholders=','.join(['%s']*(len(columns)+1))
                        for grant in role[key]:
                            cursor.execute('INSERT INTO metadata.'+table+' VALUES('+placeholders+')',
                                           (role['id'],*[grant[column] for column in columns]))
                for audit in state['audit']:
                    cursor.execute('INSERT INTO metadata.auth_audit(actor_id,action,target_id) VALUES(%s,%s,%s)',audit)

    def query(self, sql, parameters=()):
        with self.factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql,parameters)
                return cursor.fetchall()
