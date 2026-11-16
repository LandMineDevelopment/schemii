-- Contract v1: five domain tables, two control tables. No raw payload columns.
CREATE SCHEMA devops AUTHORIZATION devops_owner;
CREATE SCHEMA devops_control AUTHORIZATION devops_owner;
REVOKE ALL ON SCHEMA devops, devops_control FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE devops_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
CREATE TABLE devops_control.identity (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    database_id uuid NOT NULL,
    contract_version integer NOT NULL CHECK (contract_version = 1)
);
CREATE TABLE devops_control.migrations (
    version integer PRIMARY KEY,
    checksum char(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    schema_digest char(64) NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{64}$')
);
CREATE DOMAIN devops.safe_key AS varchar(128)
    CHECK (VALUE ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$');
CREATE DOMAIN devops.sha256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN devops.git_sha AS text CHECK (VALUE ~ '^([0-9a-f]{40}|[0-9a-f]{64})$');
CREATE DOMAIN devops.milliseconds AS numeric(18,3) CHECK (VALUE >= 0 AND VALUE < 'Infinity');
CREATE TABLE devops.repositories (
    id uuid PRIMARY KEY,
    provider text NOT NULL CHECK (provider IN ('github', 'local')),
    repository_key varchar(160) NOT NULL CHECK (repository_key ~ '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$'),
    display_name devops.safe_key NOT NULL,
    UNIQUE (provider, repository_key)
);
CREATE TABLE devops.import_batches (
    repository_id uuid NOT NULL REFERENCES devops.repositories(id),
    installation_id uuid NOT NULL,
    id uuid NOT NULL,
    source_key devops.safe_key NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    change_kind text NOT NULL CHECK (change_kind IN ('initial', 'correction', 'retraction')),
    observed_at timestamptz NOT NULL,
    evidence_sha256 devops.sha256 NOT NULL,
    evidence_ref devops.safe_key NOT NULL,
    CHECK ((revision = 1 AND change_kind = 'initial') OR (revision > 1 AND change_kind <> 'initial')),
    PRIMARY KEY (repository_id, installation_id, id, revision),
    source_kind text NOT NULL CHECK (source_kind IN ('local-ci-v1', 'test-telemetry-v1')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'complete', 'partial', 'failed', 'cancelled')),
    accepted_count bigint CHECK (accepted_count >= 0),
    rejected_count bigint CHECK (rejected_count >= 0),
    reason_code text CHECK (reason_code IN ('none', 'unsupported-input', 'invalid-evidence', 'identity-conflict', 'incomplete-source', 'interrupted')),
    CHECK (finished_at >= started_at),
    CHECK (status = 'running' OR finished_at IS NOT NULL)
);
CREATE UNIQUE INDEX import_batches_source ON devops.import_batches (repository_id, installation_id, source_key) WHERE revision = 1;
CREATE TABLE devops.ci_runs (
    repository_id uuid NOT NULL REFERENCES devops.repositories(id),
    installation_id uuid NOT NULL,
    id uuid NOT NULL,
    source_key devops.safe_key NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    change_kind text NOT NULL CHECK (change_kind IN ('initial', 'correction', 'retraction')),
    observed_at timestamptz NOT NULL,
    evidence_sha256 devops.sha256 NOT NULL,
    evidence_ref devops.safe_key NOT NULL,
    CHECK ((revision = 1 AND change_kind = 'initial') OR (revision > 1 AND change_kind <> 'initial')),
    PRIMARY KEY (repository_id, installation_id, id, revision),
    import_batch_id uuid NOT NULL,
    import_batch_revision integer NOT NULL,
    FOREIGN KEY (repository_id, installation_id, import_batch_id, import_batch_revision)
        REFERENCES devops.import_batches(repository_id, installation_id, id, revision),
    head_sha devops.git_sha,
    source_fingerprint devops.sha256,
    index_fingerprint devops.sha256,
    dirty boolean,
    base_sha devops.git_sha,
    policy_sha256 devops.sha256,
    inventory_sha256 devops.sha256,
    cohort_key devops.safe_key,
    profile devops.safe_key,
    mode text CHECK (mode IN ('selected', 'feedback', 'full')),
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms devops.milliseconds,
    outcome text CHECK (outcome IN ('running', 'passed', 'feedback-passed', 'failed', 'pending', 'cancelled', 'incomplete', 'source-changed')),
    completeness text NOT NULL CHECK (completeness IN ('unknown', 'complete', 'partial', 'pending', 'cancelled')),
    acceptance boolean,
    CHECK (acceptance IS NOT TRUE OR (outcome IS NOT NULL AND outcome = 'passed' AND completeness = 'complete' AND mode IS DISTINCT FROM 'feedback')),
    CHECK (finished_at >= started_at)
);
CREATE UNIQUE INDEX ci_runs_source ON devops.ci_runs (repository_id, installation_id, source_key) WHERE revision = 1;
CREATE TABLE devops.commands (
    repository_id uuid NOT NULL REFERENCES devops.repositories(id),
    installation_id uuid NOT NULL,
    id uuid NOT NULL,
    source_key devops.safe_key NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    change_kind text NOT NULL CHECK (change_kind IN ('initial', 'correction', 'retraction')),
    observed_at timestamptz NOT NULL,
    evidence_sha256 devops.sha256 NOT NULL,
    evidence_ref devops.safe_key NOT NULL,
    CHECK ((revision = 1 AND change_kind = 'initial') OR (revision > 1 AND change_kind <> 'initial')),
    PRIMARY KEY (repository_id, installation_id, id, revision),
    import_batch_id uuid NOT NULL,
    import_batch_revision integer NOT NULL,
    FOREIGN KEY (repository_id, installation_id, import_batch_id, import_batch_revision)
        REFERENCES devops.import_batches(repository_id, installation_id, id, revision),
    run_id uuid NOT NULL,
    run_revision integer NOT NULL,
    slot_key devops.safe_key NOT NULL,
    command_key devops.safe_key NOT NULL,
    project devops.safe_key,
    shard integer CHECK (shard >= 0),
    disposition text NOT NULL CHECK (disposition IN ('executed', 'reused', 'pending', 'excluded')),
    donor_command_id uuid,
    donor_command_revision integer,
    expected_cases bigint CHECK (expected_cases >= 0),
    inventory_sha256 devops.sha256,
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms devops.milliseconds,
    outcome text CHECK (outcome IN ('running', 'passed', 'failed', 'pending', 'cancelled', 'incomplete', 'skipped', 'error', 'timed-out', 'collection-error')),
    FOREIGN KEY (repository_id, installation_id, run_id, run_revision)
        REFERENCES devops.ci_runs(repository_id, installation_id, id, revision),
    FOREIGN KEY (repository_id, installation_id, donor_command_id, donor_command_revision)
        REFERENCES devops.commands(repository_id, installation_id, id, revision),
    CHECK ((disposition = 'reused') = (donor_command_id IS NOT NULL)),
    CHECK ((donor_command_id IS NULL) = (donor_command_revision IS NULL)),
    CHECK (finished_at >= started_at),
    CHECK (disposition = 'executed' OR (started_at IS NULL AND finished_at IS NULL AND duration_ms IS NULL AND outcome IS NULL))
);
CREATE UNIQUE INDEX commands_source ON devops.commands (repository_id, installation_id, source_key) WHERE revision = 1;
CREATE TABLE devops.test_attempts (
    repository_id uuid NOT NULL REFERENCES devops.repositories(id),
    installation_id uuid NOT NULL,
    id uuid NOT NULL,
    source_key devops.safe_key NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    change_kind text NOT NULL CHECK (change_kind IN ('initial', 'correction', 'retraction')),
    observed_at timestamptz NOT NULL,
    evidence_sha256 devops.sha256 NOT NULL,
    evidence_ref devops.safe_key NOT NULL,
    CHECK ((revision = 1 AND change_kind = 'initial') OR (revision > 1 AND change_kind <> 'initial')),
    PRIMARY KEY (repository_id, installation_id, id, revision),
    import_batch_id uuid NOT NULL,
    import_batch_revision integer NOT NULL,
    FOREIGN KEY (repository_id, installation_id, import_batch_id, import_batch_revision)
        REFERENCES devops.import_batches(repository_id, installation_id, id, revision),
    command_id uuid NOT NULL,
    command_revision integer NOT NULL,
    case_key devops.sha256 NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number >= 0),
    outcome text NOT NULL CHECK (outcome IN ('passed', 'failed', 'skipped', 'cancelled', 'timed-out')),
    setup_ms devops.milliseconds,
    call_ms devops.milliseconds,
    teardown_ms devops.milliseconds,
    FOREIGN KEY (repository_id, installation_id, command_id, command_revision)
        REFERENCES devops.commands(repository_id, installation_id, id, revision)
);
CREATE UNIQUE INDEX test_attempts_source ON devops.test_attempts (repository_id, installation_id, source_key) WHERE revision = 1;
CREATE UNIQUE INDEX commands_slot ON devops.commands (repository_id, installation_id, run_id, slot_key) WHERE revision = 1;
CREATE UNIQUE INDEX attempts_case ON devops.test_attempts (repository_id, installation_id, command_id, case_key, attempt_number) WHERE revision = 1;

-- Append/replay guard. JSONB is an internal row comparison, never stored input.
-- SERIALIZABLE writers retry whole transactions on serialization failures;
-- READ COMMITTED writers serialize per logical fact, unique indexes fence aliases.
CREATE FUNCTION devops.guard_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior jsonb; exact jsonb; fresh jsonb := to_jsonb(NEW); frozen text[];
BEGIN
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'append-only contract' USING ERRCODE = '23514'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(TG_TABLE_NAME || NEW.id::text, 0));
    EXECUTE format('SELECT to_jsonb(t) FROM devops.%I t WHERE repository_id=$1 AND installation_id=$2 AND id=$3 AND revision=$4', TG_TABLE_NAME)
        INTO exact USING NEW.repository_id, NEW.installation_id, NEW.id, NEW.revision;
    IF exact IS NOT NULL THEN
        IF exact = fresh THEN RETURN NULL; END IF;
        RAISE EXCEPTION 'immutable revision conflict' USING ERRCODE = '23505';
    END IF;
    EXECUTE format('SELECT to_jsonb(t) FROM devops.%I t WHERE repository_id=$1 AND installation_id=$2 AND id=$3 ORDER BY revision DESC LIMIT 1', TG_TABLE_NAME)
        INTO prior USING NEW.repository_id, NEW.installation_id, NEW.id;
    IF NEW.revision <> coalesce((prior->>'revision')::integer, 0) + 1 THEN
        RAISE EXCEPTION 'revision gap or stale revision' USING ERRCODE = '23514';
    END IF;
    frozen := ARRAY['source_key'];
    IF TG_TABLE_NAME = 'import_batches' THEN frozen := frozen || ARRAY['source_kind', 'started_at'];
    ELSIF TG_TABLE_NAME = 'ci_runs' THEN frozen := frozen || ARRAY['head_sha','source_fingerprint','index_fingerprint','dirty','base_sha','policy_sha256','inventory_sha256','cohort_key','profile','mode'];
    ELSIF TG_TABLE_NAME = 'commands' THEN
        frozen := frozen || ARRAY['run_id','slot_key','command_key','project','shard'];
        IF prior->>'disposition' <> 'pending' THEN
            frozen := frozen || ARRAY['disposition','donor_command_id','donor_command_revision','inventory_sha256','expected_cases'];
        END IF;
    ELSE frozen := frozen || ARRAY['command_id','case_key','attempt_number']; END IF;
    IF prior IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM unnest(frozen) k WHERE prior->k IS DISTINCT FROM fresh->k) THEN
            RAISE EXCEPTION 'immutable fact identity changed' USING ERRCODE = '23514';
        END IF;
        IF NEW.observed_at < (prior->>'observed_at')::timestamptz THEN
            RAISE EXCEPTION 'revision observation moved backwards' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION devops.guard_parent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent devops.commands; batch_kind text;
BEGIN
    SELECT source_kind INTO batch_kind FROM devops.import_batches WHERE repository_id=NEW.repository_id AND installation_id=NEW.installation_id AND id=NEW.import_batch_id AND revision=NEW.import_batch_revision;
    IF batch_kind IS NULL OR (TG_TABLE_NAME <> 'test_attempts' AND batch_kind <> 'local-ci-v1') THEN
        RAISE EXCEPTION 'incompatible import source kind' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'ci_runs' THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME = 'test_attempts' THEN
        SELECT * INTO parent FROM devops.commands WHERE repository_id=NEW.repository_id AND installation_id=NEW.installation_id AND id=NEW.command_id AND revision=NEW.command_revision;
        IF parent.id IS NULL OR parent.disposition <> 'executed' OR parent.change_kind = 'retraction' THEN
            RAISE EXCEPTION 'attempt requires an original executed command' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.disposition = 'reused' THEN
        SELECT * INTO parent FROM devops.commands WHERE repository_id=NEW.repository_id AND installation_id=NEW.installation_id AND id=NEW.donor_command_id AND revision=NEW.donor_command_revision;
        IF parent.id IS NULL OR parent.id = NEW.id OR parent.disposition <> 'executed' OR parent.change_kind = 'retraction'
            OR parent.command_key IS DISTINCT FROM NEW.command_key OR parent.project IS DISTINCT FROM NEW.project
            OR parent.shard IS DISTINCT FROM NEW.shard OR parent.inventory_sha256 IS DISTINCT FROM NEW.inventory_sha256
            OR parent.expected_cases IS DISTINCT FROM NEW.expected_cases THEN
            RAISE EXCEPTION 'reuse requires matching original execution' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION devops.guard_repository() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior devops.repositories;
BEGIN
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'append-only registry' USING ERRCODE='23514'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 0));
    SELECT * INTO prior FROM devops.repositories WHERE id=NEW.id;
    IF prior.id IS NOT NULL THEN
        IF prior = NEW THEN RETURN NULL; END IF;
        RAISE EXCEPTION 'immutable repository conflict' USING ERRCODE='23505';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER repositories_append BEFORE INSERT OR UPDATE OR DELETE ON devops.repositories FOR EACH ROW EXECUTE FUNCTION devops.guard_repository();
CREATE TRIGGER a_revision BEFORE INSERT OR UPDATE OR DELETE ON devops.import_batches FOR EACH ROW EXECUTE FUNCTION devops.guard_revision();
CREATE VIEW devops.current_import_batches AS SELECT * FROM (SELECT DISTINCT ON (repository_id, installation_id, id) * FROM devops.import_batches ORDER BY repository_id, installation_id, id, revision DESC) latest WHERE change_kind <> 'retraction';
CREATE TRIGGER a_revision BEFORE INSERT OR UPDATE OR DELETE ON devops.ci_runs FOR EACH ROW EXECUTE FUNCTION devops.guard_revision();
CREATE VIEW devops.current_ci_runs AS SELECT * FROM (SELECT DISTINCT ON (repository_id, installation_id, id) * FROM devops.ci_runs ORDER BY repository_id, installation_id, id, revision DESC) latest WHERE change_kind <> 'retraction';
CREATE TRIGGER a_revision BEFORE INSERT OR UPDATE OR DELETE ON devops.commands FOR EACH ROW EXECUTE FUNCTION devops.guard_revision();
CREATE VIEW devops.current_commands AS SELECT * FROM (SELECT DISTINCT ON (repository_id, installation_id, id) * FROM devops.commands ORDER BY repository_id, installation_id, id, revision DESC) latest WHERE change_kind <> 'retraction';
CREATE TRIGGER a_revision BEFORE INSERT OR UPDATE OR DELETE ON devops.test_attempts FOR EACH ROW EXECUTE FUNCTION devops.guard_revision();
CREATE VIEW devops.current_test_attempts AS SELECT * FROM (SELECT DISTINCT ON (repository_id, installation_id, id) * FROM devops.test_attempts ORDER BY repository_id, installation_id, id, revision DESC) latest WHERE change_kind <> 'retraction';
CREATE TRIGGER b_parent BEFORE INSERT ON devops.ci_runs FOR EACH ROW EXECUTE FUNCTION devops.guard_parent();
CREATE TRIGGER b_parent BEFORE INSERT ON devops.commands FOR EACH ROW EXECUTE FUNCTION devops.guard_parent();
CREATE TRIGGER b_parent BEFORE INSERT ON devops.test_attempts FOR EACH ROW EXECUTE FUNCTION devops.guard_parent();

-- Foundational datasets only; analysis/metric packs are #280.
CREATE VIEW devops.v1_ci_runs AS
SELECT 1 AS definition_version, r.*, extract(epoch FROM (r.finished_at-r.started_at))*1000 AS elapsed_ms
FROM devops.current_ci_runs r;
CREATE VIEW devops.v1_command_runs AS
SELECT 1 AS definition_version, c.*, r.head_sha, r.source_fingerprint, r.index_fingerprint,
       r.dirty, r.base_sha, r.policy_sha256, r.cohort_key, r.profile,
       extract(epoch FROM (c.finished_at-c.started_at))*1000 AS elapsed_ms
FROM devops.current_commands c JOIN devops.current_ci_runs r
ON (r.repository_id,r.installation_id,r.id) = (c.repository_id,c.installation_id,c.run_id);
CREATE VIEW devops.v1_test_attempts AS
SELECT 1 AS definition_version, a.*, c.run_id, c.slot_key, c.command_key, c.project, c.shard,
       c.inventory_sha256, r.cohort_key, r.profile, r.source_fingerprint
FROM devops.current_test_attempts a JOIN devops.current_commands c
ON (c.repository_id,c.installation_id,c.id) = (a.repository_id,a.installation_id,a.command_id)
JOIN devops.current_ci_runs r ON (r.repository_id,r.installation_id,r.id) = (c.repository_id,c.installation_id,c.run_id)
WHERE c.disposition = 'executed';
CREATE VIEW devops.v1_collection_health AS SELECT 1 AS definition_version, b.* FROM devops.current_import_batches b;
CREATE VIEW devops.v1_repositories AS SELECT * FROM devops.repositories;

REVOKE ALL ON ALL TABLES IN SCHEMA devops, devops_control FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA devops FROM PUBLIC;
GRANT USAGE ON SCHEMA devops TO devops_collector, devops_reader, devops_maintenance;
GRANT USAGE ON SCHEMA devops_control TO devops_maintenance;
GRANT INSERT, SELECT ON devops.repositories, devops.import_batches, devops.ci_runs, devops.commands, devops.test_attempts TO devops_collector;
GRANT SELECT ON devops.v1_ci_runs, devops.v1_command_runs, devops.v1_test_attempts, devops.v1_collection_health, devops.v1_repositories TO devops_reader, devops_collector;
GRANT SELECT ON ALL TABLES IN SCHEMA devops, devops_control TO devops_maintenance;
