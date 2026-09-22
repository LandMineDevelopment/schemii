CREATE TABLE metadata.auth_accounts (
    user_id text PRIMARY KEY REFERENCES metadata.users(id),
    username text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    is_admin boolean NOT NULL DEFAULT false,
    disabled boolean NOT NULL DEFAULT false
);
CREATE TABLE metadata.auth_sessions (
    token_hash text PRIMARY KEY,
    user_id text NOT NULL REFERENCES metadata.auth_accounts(user_id) ON DELETE CASCADE,
    expires_at double precision NOT NULL
);
CREATE INDEX auth_sessions_user_idx ON metadata.auth_sessions(user_id);
CREATE TABLE metadata.auth_roles (
    id text PRIMARY KEY,
    name text UNIQUE NOT NULL,
    capabilities jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE metadata.auth_user_roles (
    user_id text NOT NULL REFERENCES metadata.auth_accounts(user_id) ON DELETE CASCADE,
    role_id text NOT NULL REFERENCES metadata.auth_roles(id) ON DELETE CASCADE,
    PRIMARY KEY(user_id, role_id)
);
CREATE TABLE metadata.auth_role_connections (
    role_id text NOT NULL REFERENCES metadata.auth_roles(id) ON DELETE CASCADE,
    connection_id text NOT NULL,
    owner_id text NOT NULL,
    allow_authoring boolean NOT NULL DEFAULT false,
    PRIMARY KEY(role_id, connection_id),
    FOREIGN KEY(owner_id,connection_id) REFERENCES metadata.postgres_connections(owner_id,id)
);
CREATE TABLE metadata.auth_role_dashboards (
    role_id text NOT NULL REFERENCES metadata.auth_roles(id) ON DELETE CASCADE,
    dashboard_id text NOT NULL,
    owner_id text NOT NULL REFERENCES metadata.users(id),
    connection_id text NOT NULL,
    connection_owner_id text NOT NULL,
    can_export boolean NOT NULL DEFAULT false,
    can_drill boolean NOT NULL DEFAULT false,
    PRIMARY KEY(role_id,dashboard_id),
    FOREIGN KEY(connection_owner_id,connection_id) REFERENCES metadata.postgres_connections(owner_id,id)
);
CREATE TABLE metadata.auth_audit (
    id bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_id text,
    action text NOT NULL,
    target_id text
);
CREATE TABLE metadata.auth_login_attempts (
    username text PRIMARY KEY,
    attempts integer NOT NULL,
    expires_at double precision NOT NULL
);
