---
name: migration-safety
description: Use for migration preview, destructive migration review, migration apply, plan fingerprints, locks, warnings, and rollback safety.
---

# Migration Safety

1. Verify the exact profile and namespace and use the selected saved schema for that target.
2. Preview before apply. Review generated SQL, warnings, unsupported objects, data movement, locks, rewrites, and destructive steps.
3. Use `allow-preview` only when the user explicitly wants destructive operations included for review. It does not authorize apply.
4. Require a current `previewId` and `planFingerprint` for apply. Re-preview after any design, profile, namespace, or live-catalog change.
5. Apply must remain transactional and subject to Schemii stale-plan checks and UI confirmation.
6. Never interpret chat text as apply confirmation and never claim a proposal was applied.
7. Recommend disposable-data testing for risky plans and verify rollback behavior after failures.
