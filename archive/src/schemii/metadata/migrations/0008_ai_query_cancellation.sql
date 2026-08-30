ALTER TABLE metadata_proposals DROP CONSTRAINT metadata_proposals_state_check;
ALTER TABLE metadata_proposals
    ADD CONSTRAINT metadata_proposals_state_check
        CHECK (state IN ('ready', 'authorized', 'rejected', 'expired', 'revoked', 'cancelled')),
    ADD COLUMN cancellation_requested_at timestamptz,
    ADD CONSTRAINT metadata_proposal_cancellation_complete CHECK (
        state <> 'cancelled' OR cancellation_requested_at IS NOT NULL
    );

ALTER TABLE metadata_operations DROP CONSTRAINT metadata_operations_state_check;
ALTER TABLE metadata_operations
    ADD CONSTRAINT metadata_operations_state_check
        CHECK (state IN ('ready', 'running', 'succeeded', 'failed', 'uncertain', 'cancelled'));

ALTER TABLE metadata_operation_attempts DROP CONSTRAINT metadata_operation_attempts_state_check;
ALTER TABLE metadata_operation_attempts
    ADD CONSTRAINT metadata_operation_attempts_state_check
        CHECK (state IN ('running', 'succeeded', 'failed', 'uncertain', 'abandoned', 'cancelled'));

ALTER TABLE metadata_operation_outcomes DROP CONSTRAINT metadata_operation_outcomes_state_check;
ALTER TABLE metadata_operation_outcomes
    ADD CONSTRAINT metadata_operation_outcomes_state_check
        CHECK (state IN ('succeeded', 'failed', 'uncertain', 'cancelled'));

CREATE INDEX metadata_query_results_operation
    ON metadata_query_result_references ((binding ->> 'operationId'))
    WHERE binding ? 'operationId';
