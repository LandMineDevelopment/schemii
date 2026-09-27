#!/bin/sh
# Executed only by the project launcher inside the dedicated QA database.
set -eu
umask 077
fail() { printf 'QA database error: %s\n' "$1" >&2; exit 1; }
action=${1:-verify}
space=${2:-all}
case "$action" in prepare|reset|verify|check-reset|writer-prepare|writer-reset|writer-verify) ;; *) fail 'unknown database management action' ;; esac
[ "$action" != check-reset ] || { [ "$#" = 2 ] && [ "$space" != all ]; } || fail 'check-reset requires one explicit registered space'
[ "$#" -le 2 ] || fail 'too many arguments'
export PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=schemii_qa PGUSER=qa_admin
export PGPASSWORD="$(cat /run/secrets/qa_database_admin_password)"
registry=/run/secrets/qa_database_credentials
[ -s "$registry" ] || fail 'credential registry is missing or empty'
writer_registry=/run/secrets/qa_database_writable_credentials
tmp=$(mktemp -d)
restore_needed=0
cleanup() {
  result=$?
  trap - EXIT HUP INT TERM
  if [ "$restore_needed" = 1 ]; then
    printf 'Restoring the registered QA space after an interrupted reset check...\n' >&2
    if ! /opt/testing/manage.sh reset "$space" > "$tmp/recovery.log" 2>&1; then
      printf 'QA database error: automatic restoration failed; explicitly reset %s before reuse\n' "$space" >&2
      result=1
    fi
  fi
  rm -rf "$tmp"
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
# Validate the whole registry before any change, including a requested subset.
awk -F '\t' '
  NF != 3 || length($1)>63 || $1 !~ /^qa_[a-z][a-z_]*_[0-9][0-9][0-9]$/ || $2 != $1 || length($3)!=64 || $3 !~ /^[0-9a-f]+$/ || seen[$1]++ { bad=1 }
  END { exit bad || NR == 0 }
' "$registry" || fail 'registry must contain unique qa_PERSONA_NNN usernames, matching schemas, and 64-character hex passwords'
if [ "$space" != all ]; then
  awk -F '\t' -v wanted="$space" '$1 == wanted { found=1 } END { exit !found }' "$registry" || fail 'space is not in the preserved credential registry'
fi
psql_base() { psql -X --no-psqlrc --set ON_ERROR_STOP=1 "$@"; }
# Refuse to operate in any other cluster/database, even if configuration changes.
[ "$(psql_base -Atc 'SELECT current_database() || chr(9) || current_user')" = "$(printf 'schemii_qa\tqa_admin')" ] || fail 'database identity mismatch'
case "$action" in writer-*)
  case "$space" in qa_designer_004|qa_designer_010|qa_designer_011) ;; *) fail 'writable target must be qa_designer_004, qa_designer_010, or qa_designer_011' ;; esac
  [ -f "$writer_registry" ] && [ ! -L "$writer_registry" ] || fail 'writable credential registry is missing'
  awk -F '\t' '
    NF != 4 || ($4 != "qa_designer_004" && $4 != "qa_designer_010" && $4 != "qa_designer_011") ||
    $1 != "qa_write_" substr($4,4) || $2 != $1 || length($3)!=64 || $3 !~ /^[0-9a-f]+$/ || seen[$4]++ { bad=1 }
    END { exit bad }
  ' "$writer_registry" || fail 'writable credential registry is invalid'
  writer_line=$(awk -F '\t' -v wanted="$space" '$4 == wanted { print $0 }' "$writer_registry")
  [ -n "$writer_line" ] || fail "no retained writable credential for $space"
  old_ifs=$IFS
  IFS=$(printf '\t')
  read -r writer_role writer_schema writer_password writer_account <<EOF
$writer_line
EOF
  IFS=$old_ifs
  [ "$writer_account" = "$space" ] || fail 'writable credential account mismatch'
  awk -F '\t' -v wanted="$space" '$1 == wanted { found=1 } END { exit !found }' "$registry" || fail 'writable target is not a registered QA account'
  export QA_WRITER_PASSWORD="$writer_password"
  role_exists=$(psql_base -At --set role="$writer_role" <<'SQL'
SELECT count(*) FROM pg_roles WHERE rolname = :'role';
SQL
)
  if [ "$role_exists" = 0 ]; then
    [ "$action" = writer-prepare ] || fail "writable role missing for $space; prepare first"
    psql_base -q --set role="$writer_role" <<'SQL'
\getenv role_password QA_WRITER_PASSWORD
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L', :'role', :'role_password') \gexec
SELECT format('GRANT CONNECT ON DATABASE schemii_qa TO %I', :'role') \gexec
SQL
  else
    if ! PGPASSWORD="$writer_password" PGUSER="$writer_role" psql_base -Atc 'SELECT current_user' > "$tmp/writer-identity" 2>/dev/null; then
      fail "preserved writable credential mismatch for $space"
    fi
    [ "$(cat "$tmp/writer-identity")" = "$writer_role" ] || fail 'writable login identity mismatch'
  fi
  role_safe=$(psql_base -At --set role="$writer_role" <<'SQL'
SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls) AND rolcanlogin AND NOT EXISTS (SELECT FROM pg_auth_members WHERE member=pg_roles.oid) FROM pg_roles WHERE rolname=:'role';
SQL
)
  [ "$role_safe" = t ] || fail "writable role has unexpected elevated permissions: $space"
  control_exists=$(psql_base -Atc "SELECT count(*) FROM pg_namespace WHERE nspname='qa_fixture_control'")
  if [ "$control_exists" = 0 ]; then
    [ "$action" = writer-prepare ] || fail 'QA fixture control schema is missing; prepare first'
    psql_base -q <<'SQL'
BEGIN;
CREATE SCHEMA qa_fixture_control AUTHORIZATION qa_admin;
REVOKE ALL ON SCHEMA qa_fixture_control FROM PUBLIC;
CREATE TABLE qa_fixture_control.writable_targets (
  account text PRIMARY KEY CHECK (account IN ('qa_designer_004','qa_designer_010','qa_designer_011')),
  role_name text NOT NULL UNIQUE,
  schema_name text NOT NULL UNIQUE,
  fixture_version integer NOT NULL CHECK (fixture_version = 1)
);
REVOKE ALL ON qa_fixture_control.writable_targets FROM PUBLIC;
COMMIT;
SQL
  fi
  control_valid=$(psql_base -Atc "SELECT pg_get_userbyid(nspowner)='qa_admin' AND NOT EXISTS (SELECT FROM aclexplode(nspacl) WHERE grantee=0) FROM pg_namespace WHERE nspname='qa_fixture_control'")
  [ "$control_valid" = t ] || fail 'QA fixture control schema ownership or permissions changed'
  marker=$(psql_base -At --set account="$space" --set role="$writer_role" --set schema="$writer_schema" <<'SQL'
SELECT count(*) FROM qa_fixture_control.writable_targets WHERE account=:'account' AND role_name=:'role' AND schema_name=:'schema' AND fixture_version=1;
SQL
)
  if [ "$marker" = 0 ]; then
    [ "$action" = writer-prepare ] || fail "writable target marker is missing for $space; prepare first"
    conflict=$(psql_base -At --set account="$space" --set role="$writer_role" --set schema="$writer_schema" <<'SQL'
SELECT count(*) FROM qa_fixture_control.writable_targets WHERE account=:'account' OR role_name=:'role' OR schema_name=:'schema';
SQL
)
    [ "$conflict" = 0 ] || fail 'conflicting writable target marker'
    existing_schema=$(psql_base -At --set schema="$writer_schema" <<'SQL'
SELECT count(*) FROM pg_namespace WHERE nspname=:'schema';
SQL
)
    [ "$existing_schema" = 0 ] || fail 'unmarked writable target schema already exists'
    psql_base -q --set account="$space" --set role="$writer_role" --set schema="$writer_schema" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:qa:writer'), hashtext(:'schema'));
INSERT INTO qa_fixture_control.writable_targets(account,role_name,schema_name,fixture_version) VALUES (:'account',:'role',:'schema',1);
SELECT format('CREATE SCHEMA %I AUTHORIZATION %I', :'schema', :'role') \gexec
SELECT format('ALTER ROLE %I IN DATABASE schemii_qa SET search_path TO %I, pg_catalog', :'role', :'schema') \gexec
COMMIT;
SQL
  else
    [ "$marker" = 1 ] || fail 'duplicate writable target marker'
    existing_schema=$(psql_base -At --set schema="$writer_schema" <<'SQL'
SELECT count(*) FROM pg_namespace WHERE nspname=:'schema';
SQL
)
    if [ "$existing_schema" = 1 ]; then
      owner=$(psql_base -At --set schema="$writer_schema" <<'SQL'
SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=:'schema';
SQL
)
      [ "$owner" = "$writer_role" ] || fail 'writable target schema has unexpected owner'
    elif [ "$action" != writer-reset ]; then
      fail "writable target schema is missing for $space; reset it"
    fi
    if [ "$action" = writer-reset ]; then
      psql_base -q --set schema="$writer_schema" --set role="$writer_role" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:qa:writer'), hashtext(:'schema'));
SELECT format('DROP SCHEMA IF EXISTS %I CASCADE', :'schema') \gexec
SELECT format('CREATE SCHEMA %I AUTHORIZATION %I', :'schema', :'role') \gexec
SELECT format('ALTER ROLE %I IN DATABASE schemii_qa SET search_path TO %I, pg_catalog', :'role', :'schema') \gexec
COMMIT;
SQL
    fi
  fi
  # Preparation must also refuse a marked target that retained test objects or
  # acquired privileges outside its intended schema.
  [ "$(psql_base -At --set schema="$writer_schema" --set role="$writer_role" <<'SQL'
SELECT CASE WHEN
 (SELECT pg_get_userbyid(nspowner)=:'role' FROM pg_namespace WHERE nspname=:'schema')
 AND has_schema_privilege(:'role', :'schema', 'USAGE')
 AND has_schema_privilege(:'role', :'schema', 'CREATE')
 AND NOT EXISTS (SELECT FROM pg_namespace n, LATERAL aclexplode(n.nspacl) acl WHERE n.nspname=:'schema' AND acl.grantee=0)
 AND has_database_privilege(:'role', current_database(), 'CONNECT')
 AND NOT has_database_privilege(:'role', current_database(), 'CREATE')
 AND NOT EXISTS (
   SELECT FROM pg_namespace
   WHERE nspname <> :'schema'
     AND (has_schema_privilege(:'role',oid,'CREATE')
       OR (nspname NOT IN ('public','information_schema')
         AND nspname !~ '^pg_'
         AND has_schema_privilege(:'role',oid,'USAGE')))
 )
 THEN 'verified' ELSE 'invalid' END;
SQL
)" = verified ] || fail "writable target permissions differ for $space"
  login_schema=$(PGPASSWORD="$writer_password" PGUSER="$writer_role" psql_base -Atc 'SELECT current_schema()') || fail 'writable role cannot connect'
  [ "$login_schema" = "$writer_schema" ] || fail 'writable role search path differs'
  object_count=$(psql_base -At --set schema="$writer_schema" <<'SQL'
-- Every object contained in a schema depends on its namespace. This includes
-- standalone routines, types, domains, and other objects absent from pg_class.
SELECT count(DISTINCT (classid,objid,objsubid))
FROM pg_depend
WHERE refclassid='pg_namespace'::regclass
  AND refobjid=to_regnamespace(:'schema')
  AND refobjsubid=0;
SQL
)
  [ "$object_count" = 0 ] || fail "writable target was not emptied for $space"
  printf '{"action":"%s","account":"%s","role":"%s","schema":"%s","objects":%s,"credentials":"preserved"}\n' "$action" "$space" "$writer_role" "$writer_schema" "$object_count"
  exit 0
esac
if [ "$action" = check-reset ]; then
  /opt/testing/manage.sh verify "$space" > "$tmp/before.json"
  before=$(sed -n 's/.*"lastVerifiedDataDigest":"\([0-9a-f]*\)".*/\1/p' "$tmp/before.json")
  [ "${#before}" = 32 ] || fail 'baseline check did not report a data checksum'
  restore_needed=1
  # Check view drift independently, before table/sequence drift can mask it.
  psql_base -q --set schema="$space" > "$tmp/perturb-view.log" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:qa:reset'), hashtext(:'schema'));
SELECT format('SET LOCAL search_path TO %I, pg_catalog', :'schema') \gexec
CREATE OR REPLACE VIEW customer_totals AS SELECT c.id,c.name,c.region,count(o.id) AS order_count,coalesce(sum(o.amount),0)+1 AS total FROM customers c LEFT JOIN orders o ON o.customer_id=c.id GROUP BY c.id,c.name,c.region;
COMMIT;
SQL
  if /opt/testing/manage.sh verify "$space" > "$tmp/view-drift.log" 2>&1; then
    fail 'fixture verifier failed to detect deliberate view-only drift'
  fi
  /opt/testing/manage.sh reset "$space" > "$tmp/reset-view.json"
  # Verify the reader can consume the view, and cannot advance its sequence.
  psql_base -q --set schema="$space" --set role="$space" > "$tmp/perturb-view-grant.log" <<'SQL'
SELECT format('REVOKE SELECT ON %I.customer_totals FROM %I', :'schema', :'role') \gexec
SQL
  if /opt/testing/manage.sh verify "$space" > "$tmp/view-grant-drift.log" 2>&1; then
    fail 'fixture verifier failed to detect a missing view SELECT grant'
  fi
  /opt/testing/manage.sh reset "$space" > "$tmp/reset-view-grant.json"
  psql_base -q --set schema="$space" --set role="$space" > "$tmp/perturb-sequence-grant.log" <<'SQL'
SELECT format('GRANT USAGE ON SEQUENCE %I.orders_id_seq TO %I', :'schema', :'role') \gexec
SQL
  if /opt/testing/manage.sh verify "$space" > "$tmp/sequence-grant-drift.log" 2>&1; then
    fail 'fixture verifier failed to detect an unexpected sequence grant'
  fi
  /opt/testing/manage.sh reset "$space" > "$tmp/reset-sequence-grant.json"
  psql_base -q --set schema="$space" > "$tmp/perturb.log" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:qa:reset'), hashtext(:'schema'));
SELECT format('SET LOCAL search_path TO %I, pg_catalog', :'schema') \gexec
UPDATE orders SET amount=amount+999 WHERE id=1;
DELETE FROM orders WHERE id=513;
CREATE TABLE reset_check_scratch (id integer PRIMARY KEY, note text NOT NULL);
INSERT INTO reset_check_scratch VALUES (1,'Disposable reset validation');
SELECT setval(pg_get_serial_sequence('orders','id'),9000,true);
COMMIT;
SQL
  # Prove the ordinary verifier detects the deliberate drift before resetting.
  if /opt/testing/manage.sh verify "$space" > "$tmp/drift.log" 2>&1; then
    fail 'fixture verifier failed to detect deliberate data/object/sequence drift'
  fi
  /opt/testing/manage.sh reset "$space" > "$tmp/reset.json"
  /opt/testing/manage.sh verify "$space" > "$tmp/after.json"
  after=$(sed -n 's/.*"lastVerifiedDataDigest":"\([0-9a-f]*\)".*/\1/p' "$tmp/after.json")
  [ "$before" = "$after" ] || fail 'reset did not restore the original baseline checksum'
  restore_needed=0
  printf '{"action":"check-reset","space":"%s","outcome":"passed","driftDetected":true,"viewDriftDetected":true,"viewGrantDriftDetected":true,"sequenceGrantDriftDetected":true,"beforeDigest":"%s","afterDigest":"%s","credentials":"preserved-and-authenticated"}\n' "$space" "$before" "$after"
  exit 0
fi
if [ "$action" = prepare ]; then
  psql_base -q <<'SQL'
REVOKE ALL ON DATABASE schemii_qa FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
fi
count=0
digest=null
tab=$(printf '\t')
while IFS="$tab" read -r username schema password; do
  [ "$space" = all ] || [ "$space" = "$username" ] || continue
  export QA_ROLE_PASSWORD="$password"
  role_exists=$(psql_base -At --set role="$username" <<'SQL'
SELECT count(*) FROM pg_roles WHERE rolname = :'role';
SQL
)
  if [ "$role_exists" = 0 ]; then
    [ "$action" = prepare ] || fail "role missing for $username; prepare first"
    psql_base -q --set role="$username" <<'SQL'
\getenv role_password QA_ROLE_PASSWORD
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L', :'role', :'role_password') \gexec
SELECT format('GRANT CONNECT ON DATABASE schemii_qa TO %I', :'role') \gexec
SQL
  else
    # Authentication against TCP/SCRAM proves the persisted password still works.
    # Never rotate an existing role to hide a registry mismatch.
    if ! PGPASSWORD="$password" PGUSER="$username" psql_base -Atc 'SELECT current_user' > "$tmp/identity" 2>/dev/null; then
      fail "preserved credential mismatch for $username"
    fi
    [ "$(cat "$tmp/identity")" = "$username" ] || fail "unexpected login identity for $username"
  fi
  role_safe=$(psql_base -At --set role="$username" <<'SQL'
SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls) AND rolcanlogin AND NOT EXISTS (SELECT FROM pg_auth_members WHERE member=pg_roles.oid) FROM pg_roles WHERE rolname=:'role';
SQL
)
  [ "$role_safe" = t ] || fail "unexpected elevated database permissions for $username"
  exists=$(psql_base -At --set schema="$schema" <<'SQL'
SELECT count(*) FROM pg_namespace WHERE nspname=:'schema';
SQL
)
  if [ "$exists" = 1 ]; then
    owner=$(psql_base -At --set schema="$schema" <<'SQL'
SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=:'schema';
SQL
)
    [ "$owner" = qa_admin ] || fail "unexpected fixture schema owner: $schema"
    marker=$(psql_base -At --set schema="$schema" <<'SQL'
SELECT format('SELECT version FROM %I.fixture_version', :'schema') \gexec
SQL
) || fail "existing schema lacks its QA ownership marker: $schema"
    [ "$marker" = 1 ] || fail "unrecognized fixture version in $schema"
  fi
  if [ "$action" = reset ] || { [ "$action" = prepare ] && [ "$exists" = 0 ]; }; then
    psql_base -q --set schema="$schema" --set role="$username" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('schemii:qa:reset'), hashtext(:'schema'));
SELECT format('DROP SCHEMA IF EXISTS %I CASCADE', :'schema') \gexec
SELECT format('CREATE SCHEMA %I AUTHORIZATION qa_admin', :'schema') \gexec
SELECT format('SET LOCAL search_path TO %I, pg_catalog', :'schema') \gexec
\i /opt/testing/baseline.sql
SELECT format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', :'schema') \gexec
SELECT format('GRANT USAGE ON SCHEMA %I TO %I', :'schema', :'role') \gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', :'schema', :'role') \gexec
SELECT format('ALTER ROLE %I IN DATABASE schemii_qa SET search_path TO %I, pg_catalog', :'role', :'schema') \gexec
COMMIT;
SQL
  fi
  if [ "$action" = verify ] || [ "$action" = reset ] || [ "$exists" = 0 ]; then
    [ "$exists" = 1 ] || [ "$action" != verify ] || fail "missing fixture schema $schema"
    verified=$(psql_base -At --set schema="$schema" --set role="$username" <<'SQL'
SELECT format('SET search_path TO %I, pg_catalog', :'schema') \gexec
WITH expected_orders AS (
 SELECT n::bigint AS id,(n % 32)+1 AS customer_id, DATE '2025-01-01' + (n % 90) AS ordered_on,(ARRAY['pending','paid','cancelled'])[(n % 3)+1] AS status,((n*137)%100000)::numeric(10,2)/100 AS amount,CASE WHEN n % 7 = 0 THEN NULL ELSE 'Fixture order ' || n END AS notes FROM generate_series(1,513) n
), expected_customers AS (
 SELECT n AS id,'Customer ' || lpad(n::text,3,'0') AS name,CASE WHEN n % 2 = 0 THEN 'east' ELSE 'west' END AS region FROM generate_series(1,32) n
), expected_totals AS (
 SELECT c.id,c.name,c.region,count(o.id) AS order_count,coalesce(sum(o.amount),0) AS total FROM expected_customers c LEFT JOIN expected_orders o ON o.customer_id=c.id GROUP BY c.id,c.name,c.region
)
SELECT CASE WHEN
 (SELECT count(*) FROM orders)=513 AND (SELECT count(*) FROM customers)=32
 AND NOT EXISTS ((SELECT * FROM orders EXCEPT SELECT * FROM expected_orders) UNION ALL (SELECT * FROM expected_orders EXCEPT SELECT * FROM orders))
 AND NOT EXISTS ((SELECT * FROM customers EXCEPT SELECT * FROM expected_customers) UNION ALL (SELECT * FROM expected_customers EXCEPT SELECT * FROM customers))
 AND (SELECT count(*) FROM customer_totals)=32
 AND NOT EXISTS ((SELECT id,name,region,order_count,total FROM customer_totals EXCEPT SELECT * FROM expected_totals) UNION ALL (SELECT * FROM expected_totals EXCEPT SELECT id,name,region,order_count,total FROM customer_totals))
 AND (SELECT count(*)=1 AND min(version)=1 FROM fixture_version)
 AND (SELECT last_value=513 AND is_called FROM orders_id_seq)
 AND (SELECT array_agg(relname::text ORDER BY relname) FROM pg_class WHERE relnamespace=to_regnamespace(:'schema') AND relkind IN ('r','p','v','m','S','f'))=ARRAY['customer_totals','customers','fixture_version','orders','orders_id_seq']
 AND (SELECT count(*) FROM pg_constraint WHERE conrelid='orders'::regclass AND contype='f' AND confrelid='customers'::regclass AND convalidated)=1
 AND has_schema_privilege(:'role', :'schema', 'USAGE')
 AND NOT has_schema_privilege(:'role', :'schema', 'CREATE')
 AND NOT has_database_privilege(:'role', current_database(), 'CREATE')
 AND NOT EXISTS (SELECT FROM pg_class WHERE relnamespace=to_regnamespace(:'schema') AND CASE WHEN relkind IN ('r','p','v','m') THEN NOT has_table_privilege(:'role',oid,'SELECT') OR has_table_privilege(:'role',oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') ELSE false END)
 AND NOT EXISTS (SELECT FROM pg_class WHERE relnamespace=to_regnamespace(:'schema') AND CASE WHEN relkind='S' THEN has_sequence_privilege(:'role',oid,'USAGE,SELECT,UPDATE') ELSE false END)
 AND NOT EXISTS (SELECT FROM pg_namespace WHERE nspname LIKE 'qa\_%' ESCAPE '\' AND nspname <> :'schema' AND has_schema_privilege(:'role',oid,'USAGE'))
 THEN 'verified' ELSE 'invalid' END;
SQL
)
    [ "$(printf '%s\n' "$verified" | tail -n 1)" = verified ] || fail "baseline or permissions differ in $schema; reset this space"
    # Use the preserved login for the data check as well as catalog privilege tests.
    rows=$(PGPASSWORD="$password" PGUSER="$username" psql_base -Atc 'SELECT count(*) FROM orders') || fail "reader cannot query $schema"
    [ "$rows" = 513 ] || fail "reader sees unexpected data in $schema"
    digest=$(PGPASSWORD="$password" PGUSER="$username" psql_base -Atc "SELECT md5((SELECT string_agg(row_to_json(c)::text, E'\n' ORDER BY id) FROM customers c) || E'\n' || (SELECT string_agg(row_to_json(o)::text, E'\n' ORDER BY id) FROM orders o))")
    digest="\"$digest\""
  fi
  count=$((count+1))
done < "$registry"
printf '{"action":"%s","space":"%s","spaces":%s,"baseline":{"version":1,"customers":32,"orders":513},"lastVerifiedDataDigest":%s,"credentials":"preserved"}\n' "$action" "$space" "$count" "$digest"
