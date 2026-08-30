---
name: approval-gated-implementation
description: Use when planning or executing phased features, implementation checklists, roadmap steps, or any work where the user wants to approve each phase before code changes.
---

# Approval-Gated Implementation

Use this format before starting each implementation phase. Do not edit application code for an unapproved phase.

## Required Plan Format

Start with a short phase title and explicitly state that no code changes will be made until approval.

Cover these sections when relevant:

1. **Scope**: The best coherent behavior delivered by this phase, bounded by its approved product outcome rather than by minimizing implementation size.
2. **Storage And Data Model**: New or changed records, versions, stable IDs, ownership, persistence, validation, and compatibility.
3. **API And Services**: Routes, request/response contracts, authorization, shared modules, and process boundaries.
4. **User Interaction**: Default mode, editing workflow, empty/loading/error/conflict states, desktop behavior, and mobile behavior.
5. **Expected Files**: Likely additions and modifications. Avoid claiming exact files when discovery may change them.
6. **Tests And Verification**: Focused behavior tests, complete suites, server smoke tests, database checks, and render checks.
7. **Risks**: Data loss, stale writes, compatibility, locking, permissions, destructive operations, performance, and security.
8. **Explicit Non-Goals**: Adjacent behavior intentionally deferred to later approval-gated phases.
9. **Acceptance Criteria**: Observable conditions that must be true before marking the phase complete.

End by asking the user to approve the phase or specify changes.

## Execution Rules

- After approval, implement only the approved scope.
- If discovery materially changes storage, API, security, destructive behavior, or user interaction, stop and request approval for the revised plan.
- Optimize for the best long-term design that meets the product need while remaining simple, maintainable, reusable, and non-duplicative. Do not choose a weaker design merely because it produces the smallest diff.
- Prefer extending focused shared contracts and modules over copied implementations, especially across sibling applications.
- Keep application-specific workflows, authorization, persistence ownership, and conflict semantics separate when sharing them would weaken safety or comprehension.
- Add an abstraction when at least two concrete consumers share a stable contract or when one foundational boundary is clearly reusable. Do not introduce speculative frameworks for hypothetical reuse.
- Evaluate alternatives using correctness, user value, maintainability, reuse, capability isolation, migration cost, and operational safety; implementation size is a constraint, not the primary objective.
- Preserve existing user-owned data and unrelated worktree changes.
- Mark checklist items complete only after implementation and required verification pass.
- Update the durable implementation checklist in the same change.
- Report any tests or runtime checks that could not be performed.

## Concision

Keep plans concrete and scannable. Include enough detail for an informed decision, but do not bury the approval question beneath speculative future architecture.
