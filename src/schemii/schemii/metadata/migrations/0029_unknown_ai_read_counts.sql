-- Large reads are paged without an eager COUNT(*). NULL means the exact total
-- was intentionally not computed; query text and transient result ownership
-- remain sufficient for paging or replay.
ALTER TABLE schemii.ai_read_runs ALTER COLUMN row_count DROP NOT NULL;
