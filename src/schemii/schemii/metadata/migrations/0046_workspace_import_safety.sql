ALTER TABLE schemii.workspace_design_imports
ADD COLUMN safety_evidence_version integer
    CHECK (safety_evidence_version >= 1),
ADD COLUMN migration_guidance text NOT NULL DEFAULT
    'Historical import coverage does not establish operation-specific preservation safety.';

COMMENT ON COLUMN schemii.workspace_design_imports.safety_evidence_version IS
    'Version of the captured import safety evidence; historical imports remain unknown. This field does not authorize migration operations.';
COMMENT ON COLUMN schemii.workspace_design_imports.migration_guidance IS
    'Captured import compatibility guidance, preserved across reloads without upgrading historical coverage or review authority.';
