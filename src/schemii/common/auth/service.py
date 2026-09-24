"""Password sessions and explicit, revocable role grants."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException

from schemii.common.api.errors import ApiProblem
from schemii.common.connections.models import SCHEMII_CONNECTION_OWNER_ID
from .store import AuthStore

COOKIE = 'schemii_session'
SESSION_SECONDS = 12 * 60 * 60
PROVISIONER_ROLE_ID = 'role_application_provisioners'
BOOTSTRAP_PRODUCTS_ROLE_ID = 'role_bootstrap_products'
DIRECT_ROLE_PREFIX = 'role_personal_'
PRODUCT_CAPABILITIES = ('schemii:access', 'schemoo:access', 'schemer:access')
SCHEMER_AUTHOR = 'schemer:author'
PROVISION_CAPABILITY = 'accounts:provision'


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + digest.hex()


def password_matches(password, stored):
    salt, expected = stored.split(':')
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(actual.hex(), expected)


def public_user(user):
    return {key: user[key] for key in ('id','username','display_name','is_admin','disabled')}


def direct_role_id(user_id):
    return DIRECT_ROLE_PREFIX + user_id


def assign_direct_access(state, user_id, username, access):
    """Reuse the role authorization path for permissions assigned to one person."""
    role_id = direct_role_id(user_id)
    if not any(access[key] for key in ('capabilities','connections','dashboards')):
        state['roles'].pop(role_id, None)
        return
    state['roles'][role_id] = dict(id=role_id, name=f'Direct access: {username}',
        capabilities=access['capabilities'], user_ids=[user_id],
        connections=access['connections'], dashboards=access['dashboards'])


def assign_roles(state, user_id, role_ids):
    """Change only this person's memberships; role scopes remain shared."""
    selected = set(role_ids)
    if len(selected) != len(role_ids) or PROVISIONER_ROLE_ID in selected or any(
            role_id.startswith(DIRECT_ROLE_PREFIX) for role_id in selected):
        raise HTTPException(422, 'Invalid role selection')
    if selected - state['roles'].keys():
        raise HTTPException(422, 'Unknown role')
    for role_id, role in state['roles'].items():
        if role_id == PROVISIONER_ROLE_ID or role_id.startswith(DIRECT_ROLE_PREFIX):
            continue
        members = role['user_ids']
        if role_id in selected and user_id not in members:
            if len(members) >= 1000:
                raise HTTPException(409, f'Role “{role["name"]}” already has 1000 members')
            members.append(user_id)
        elif role_id not in selected and user_id in members:
            role['user_ids'] = [member for member in members if member != user_id]


class AuthService:
    def __init__(self, connection_factory=None, *, enabled=None, setup_token=None):
        if os.getenv('SCHEMII_AUTH_ENABLED', '0') not in {'0','1'}:
            raise ValueError('SCHEMII_AUTH_ENABLED must be 0 or 1')
        self.enabled = os.getenv('SCHEMII_AUTH_ENABLED', '0') == '1' if enabled is None else enabled
        self.store = AuthStore(connection_factory)
        token_file = os.getenv('SCHEMII_SETUP_TOKEN_FILE')
        self.setup_token = setup_token or (Path(token_file).read_text().strip() if token_file else os.getenv('SCHEMII_SETUP_TOKEN', ''))
        self._dummy_hash = password_hash(secrets.token_urlsafe(32))
        if self.store.factory and not self.enabled and not self.setup_required():
            raise ValueError('Existing accounts require SCHEMII_AUTH_ENABLED=1; refusing unauthenticated downgrade')
        if self.enabled and not self.setup_token and self.setup_required():
            raise ValueError('First account requires SCHEMII_SETUP_TOKEN_FILE')

    def setup_required(self):
        if self.store.factory:
            return not self.store.query("SELECT 1 FROM metadata.auth_accounts LIMIT 1")
        with self.store.transaction() as state: return not state['users']

    def user(self, user_id):
        if self.store.factory:
            rows=self.store.query("SELECT a.user_id AS id,a.username,a.is_admin,a.disabled,u.display_name FROM metadata.auth_accounts a JOIN metadata.users u ON u.id=a.user_id WHERE a.user_id=%s AND NOT a.disabled",(user_id,))
            return public_user(rows[0]) if rows else None
        with self.store.transaction() as state:
            user = state['users'].get(user_id)
            return public_user(user) if user and not user['disabled'] else None

    def is_admin(self, user_id):
        if not self.enabled: return True
        return PROVISION_CAPABILITY in self.capabilities(user_id)

    def capabilities(self, user_id):
        if not self.enabled: return sorted((*PRODUCT_CAPABILITIES, SCHEMER_AUTHOR, PROVISION_CAPABILITY))
        if self.store.factory:
            rows=self.store.query('SELECT r.capabilities FROM metadata.auth_roles r JOIN metadata.auth_user_roles ur ON ur.role_id=r.id JOIN metadata.auth_accounts a ON a.user_id=ur.user_id WHERE ur.user_id=%s AND NOT a.disabled',(user_id,))
            return sorted({c for row in rows for c in row['capabilities']})
        with self.store.transaction() as state:
            user = state['users'].get(user_id)
            if not user or user['disabled']: return []
            return sorted({c for r in state['roles'].values() if user_id in r['user_ids'] for c in r['capabilities']})

    def connection_access(self, user_id, connection_id, product):
        """Resolve one managed profile whose grant and product rights belong to the same role.

        Private profiles belong to the caller and are resolved by ConnectionService.
        Different matching database identities are ambiguous and must fail closed.
        """
        capability = f'{product}:access'
        if capability not in PRODUCT_CAPABILITIES: raise ValueError('Unknown product')
        if self.store.factory:
            rows = self.store.query(
                'SELECT g.role_id,g.owner_id,g.connection_id FROM metadata.auth_role_connections g '
                'JOIN metadata.auth_roles r ON r.id=g.role_id '
                'JOIN metadata.auth_user_roles ur ON ur.role_id=r.id '
                'JOIN metadata.auth_accounts a ON a.user_id=ur.user_id '
                'WHERE ur.user_id=%s AND NOT a.disabled AND g.connection_id=%s AND g.owner_id=%s '
                'AND g.allow_authoring AND r.capabilities ? %s '
                + ('AND r.capabilities ? %s' if product == 'schemer' else ''),
                (user_id, connection_id, SCHEMII_CONNECTION_OWNER_ID, capability, SCHEMER_AUTHOR) if product == 'schemer'
                else (user_id, connection_id, SCHEMII_CONNECTION_OWNER_ID, capability),
            )
        else:
            with self.store.transaction() as state:
                user = state['users'].get(user_id)
                rows = [dict(role_id=role['id'], owner_id=grant['owner_id'], connection_id=grant['connection_id'])
                        for role in state['roles'].values() if user and not user['disabled']
                        and user_id in role['user_ids'] and capability in role['capabilities']
                        and (product != 'schemer' or SCHEMER_AUTHOR in role['capabilities'])
                        for grant in role['connections']
                        if grant['connection_id'] == connection_id and grant['owner_id'] == SCHEMII_CONNECTION_OWNER_ID
                        and grant['allow_authoring']]
        owners = {row['owner_id'] for row in rows}
        if len(owners) > 1: raise HTTPException(409, 'Conflicting database identities are assigned by roles')
        return sorted(rows, key=lambda row: row['role_id'])[0] if rows else None

    def _grants(self, user_id, key):
        if self.store.factory:
            table={'connections':'auth_role_connections','dashboards':'auth_role_dashboards'}[key]
            owner_field = 'owner_id' if key == 'connections' else 'connection_owner_id'
            return self.store.query('SELECT g.* FROM metadata.'+table+' g JOIN metadata.auth_user_roles ur ON ur.role_id=g.role_id JOIN metadata.auth_accounts a ON a.user_id=ur.user_id WHERE ur.user_id=%s AND NOT a.disabled AND g.'+owner_field+'=%s',(user_id,SCHEMII_CONNECTION_OWNER_ID))
        with self.store.transaction() as state:
            user = state['users'].get(user_id)
            if not user or user['disabled']: return []
            owner_field = 'owner_id' if key == 'connections' else 'connection_owner_id'
            return [{**g,'role_id':r['id']} for r in state['roles'].values() if user_id in r['user_ids']
                    for g in r[key] if g[owner_field] == SCHEMII_CONNECTION_OWNER_ID]

    def connection_grants(self, user_id): return self._grants(user_id, 'connections')
    def dashboard_grants(self, user_id): return self._grants(user_id, 'dashboards')

    def resolve(self, token):
        if not token: return None
        if self.store.factory:
            rows=self.store.query('SELECT a.user_id AS id,a.username,a.is_admin,a.disabled,u.display_name FROM metadata.auth_sessions s JOIN metadata.auth_accounts a ON a.user_id=s.user_id JOIN metadata.users u ON u.id=a.user_id WHERE s.token_hash=%s AND s.expires_at > extract(epoch from now()) AND NOT a.disabled',(hashlib.sha256(token.encode()).hexdigest(),))
            return public_user(rows[0]) if rows else None
        with self.store.transaction() as state:
            session = state['sessions'].get(hashlib.sha256(token.encode()).hexdigest())
            if not session or session['expires_at'] <= time.time(): return None
            user = state['users'].get(session['user_id'])
            return public_user(user) if user and not user['disabled'] else None

    def create_user(self, data, actor=None, setup_token=None):
        hashed = password_hash(data.password)
        with self.store.transaction(write=True) as state:
            setup = setup_token is not None
            if setup and (state['users'] or not self.setup_token or not hmac.compare_digest(setup_token,self.setup_token)):
                raise HTTPException(403,'Account setup is unavailable or token is invalid')
            username = data.username.casefold()
            if any(u['username'] == username for u in state['users'].values()): raise HTTPException(409,'Username already exists')
            user_id = 'user_local_prototype' if setup else 'user_' + uuid4().hex
            user = dict(id=user_id,username=username,display_name=data.display_name,password_hash=hashed,is_admin=True if setup else data.is_admin,disabled=False)
            state['users'][user_id] = user
            if user['is_admin']:
                provisioners = state['roles'].setdefault(PROVISIONER_ROLE_ID, dict(
                    id=PROVISIONER_ROLE_ID, name='Application provisioners',
                    capabilities=[PROVISION_CAPABILITY], user_ids=[], connections=[], dashboards=[]))
                provisioners['user_ids'].append(user_id)
            if setup:
                state['roles'][BOOTSTRAP_PRODUCTS_ROLE_ID] = dict(
                    id=BOOTSTRAP_PRODUCTS_ROLE_ID, name='Bootstrap product access',
                    capabilities=[*PRODUCT_CAPABILITIES, SCHEMER_AUTHOR],
                    user_ids=[user_id], connections=[], dashboards=[])
            else:
                assign_roles(state, user_id, getattr(data, 'role_ids', []))
                direct = getattr(data, 'direct_access', None)
                if direct is not None:
                    assign_direct_access(state, user_id, username, direct.model_dump())
            state['audit'].append((actor or user_id,'account.create',user_id))
        return public_user(user)

    def login(self, username, password):
        username = username.casefold()
        with self.store.transaction(write=True) as state:
            now = time.time()
            state['attempts'] = {k:v for k,v in state['attempts'].items() if v['expires_at'] > now}
            attempt = state['attempts'].setdefault(username, {'attempts':0,'expires_at':now+900})
            if attempt['attempts'] >= 10:
                raise ApiProblem(429, 'sign_in_rate_limited', 'Too many sign-in attempts. Try again in 15 minutes.')
            attempt['attempts'] += 1
            state['audit'].append((None,'session.attempt',None))
        # Hash outside the write transaction so expensive password work does not hold DB locks.
        with self.store.transaction() as state:
            candidate = next((u for u in state['users'].values() if u['username'] == username.casefold()), None)
            stored = candidate['password_hash'] if candidate else self._dummy_hash
        matches = password_matches(password, stored)
        with self.store.transaction(write=True) as state:
            user = state['users'].get(candidate['id']) if candidate else None
            if not matches or not user or user['disabled'] or user['password_hash'] != stored:
                raise ApiProblem(401, 'invalid_credentials', 'Incorrect username or password. Check both fields and try again.')
            state['attempts'].pop(username, None)
            token = secrets.token_urlsafe(32)
            now = time.time()
            state['sessions'] = {key:s for key,s in state['sessions'].items() if s['expires_at'] > now}
            state['sessions'][hashlib.sha256(token.encode()).hexdigest()] = dict(user_id=user['id'],expires_at=now+SESSION_SECONDS)
            state['audit'].append((user['id'],'session.login',user['id']))
        return token, public_user(user)

    def logout(self, token):
        with self.store.transaction(write=True) as state:
            state['sessions'].pop(hashlib.sha256((token or '').encode()).hexdigest(), None)

    def update_user(self, user_id, data, actor):
        updates = data.model_dump(exclude_unset=True)
        role_ids = updates.pop('role_ids', None)
        updates.pop('direct_access', None)
        direct_access = data.direct_access.model_dump() if data.direct_access is not None else None
        if 'password' in updates: updates['password_hash'] = password_hash(updates.pop('password'))
        with self.store.transaction(write=True) as state:
            user = state['users'].get(user_id)
            if user is None: raise HTTPException(404,'Account not found')
            next_user = {**user,**updates}
            if user['is_admin'] and not user['disabled'] and (next_user['disabled'] or not next_user['is_admin']):
                if not any(u['id'] != user_id and u['is_admin'] and not u['disabled'] for u in state['users'].values()):
                    raise HTTPException(409,'Keep at least one active administrator')
            state['users'][user_id] = next_user
            provisioners = state['roles'].get(PROVISIONER_ROLE_ID)
            if next_user['is_admin'] and provisioners is None:
                provisioners = state['roles'][PROVISIONER_ROLE_ID] = dict(
                    id=PROVISIONER_ROLE_ID, name='Application provisioners',
                    capabilities=[PROVISION_CAPABILITY], user_ids=[], connections=[], dashboards=[])
            if provisioners:
                provisioners['user_ids'] = [member for member in provisioners['user_ids'] if member != user_id]
                if next_user['is_admin']: provisioners['user_ids'].append(user_id)
            if next_user['disabled'] or 'password_hash' in updates:
                state['sessions'] = {key:s for key,s in state['sessions'].items() if s['user_id'] != user_id}
            if role_ids is not None:
                assign_roles(state, user_id, role_ids)
            if direct_access is not None:
                assign_direct_access(state, user_id, user['username'], direct_access)
            state['audit'].append((actor,'account.update',user_id))
        return public_user(next_user)

    def delete_user(self, user_id, actor):
        """Remove sign-in and grants while preserving the person's owned work."""
        with self.store.transaction(write=True) as state:
            user = state['users'].get(user_id)
            if user is None:
                raise HTTPException(404, 'Account not found')
            if user['is_admin'] and not user['disabled'] and not any(
                    other['id'] != user_id and other['is_admin'] and not other['disabled']
                    for other in state['users'].values()):
                raise HTTPException(409, 'Keep at least one active administrator')
            del state['users'][user_id]
            state['roles'].pop(direct_role_id(user_id), None)
            state['sessions'] = {key: session for key, session in state['sessions'].items()
                                 if session['user_id'] != user_id}
            state['attempts'].pop(user['username'], None)
            for role in state['roles'].values():
                if user_id in role['user_ids']:
                    role['user_ids'] = [member for member in role['user_ids'] if member != user_id]
            state['audit'].append((actor, 'account.delete', user_id))

    def audit(self, actor, action, target):
        if self.store.factory:
            with self.store.factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute('INSERT INTO metadata.auth_audit(actor_id,action,target_id) VALUES(%s,%s,%s)',(actor,action,target))
        else:
            with self.store.transaction(write=True) as state:
                state['audit'].append((actor,action,target))
