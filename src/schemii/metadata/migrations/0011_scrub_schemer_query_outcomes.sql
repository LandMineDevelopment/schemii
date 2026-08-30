UPDATE metadata_operation_outcomes AS outcome
SET result = (outcome.result - 'display') || jsonb_build_object(
    'evidence',
    jsonb_build_object(
        'rowCount', COALESCE(outcome.result #> '{display,rowCount}', '0'::jsonb),
        'columnCount', to_jsonb(CASE
            WHEN jsonb_typeof(outcome.result #> '{display,columns}') = 'array'
            THEN jsonb_array_length(outcome.result #> '{display,columns}')
            ELSE 0
        END),
        'truncated', COALESCE(outcome.result #> '{display,truncated}', 'false'::jsonb)
    )
)
FROM metadata_operations AS operation
JOIN metadata_proposals AS proposal USING (proposal_id)
JOIN metadata_chats AS chat ON chat.chat_id = operation.chat_id
WHERE outcome.operation_id = operation.operation_id
  AND chat.application_id = 'schemer'
  AND proposal.action ->> 'type' = 'read_query'
  AND outcome.result ->> 'kind' = 'sql_result'
  AND outcome.result ? 'display';

COMMENT ON COLUMN metadata_operation_outcomes.result IS
    'Durable operational evidence only. Schemer analytic rows are delivered through expiring result references and immediate responses, never retained here.';
