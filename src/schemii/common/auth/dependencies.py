"""Role grants retain their managed database profiles until explicitly removed."""
from schemii.common.connections.dependencies import ConnectionDependentResource
from schemii.common.connections.store import ConnectionInUseError


class AccountConnectionDependencies:
    dependency_name = 'account_roles'

    def __init__(self, auth):
        self.auth = auth

    def dependencies_for_connection(self, owner_id, connection_id):
        with self.auth.store.transaction() as state:
            roles = [role for role in state['roles'].values() if any(
                grant['owner_id'] == owner_id and grant['connection_id'] == connection_id
                for grant in role['connections'])]
        return tuple(ConnectionDependentResource(
            provider=self.dependency_name,kind='role',resource_id=role['id'],
            revision=1,name=role['name'],deletion_blocked=True,
            blocking_reason='Remove this database connection from the account role before deleting or changing its identity.'
        ) for role in roles)

    def count_for_connection(self, owner_id, connection_id):
        return len(self.dependencies_for_connection(owner_id,connection_id))

    def guard_connection_mutation(self,cursor,owner_id,connection_id,operation,changed_fields):
        if operation != 'delete' and not changed_fields.intersection({'host','port','database','username'}):
            return
        cursor.execute('SELECT count(*) AS count FROM metadata.auth_role_connections WHERE owner_id=%s AND connection_id=%s',(owner_id,connection_id))
        count=cursor.fetchone()['count']
        if count:
            raise ConnectionInUseError({self.dependency_name:count})
