# QA worker lease termination

A claimed lane has a ten-minute execution lease, checked every thirty seconds.
An expired session handle is fenced immediately: its lane pauses, generation
increments and token is revoked. Recovery must inspect saved state before
replaying an uncertain write; completed scenario results remain in the ledger.

The legacy Codex worker now stops outside the browser action queue. A slow or
stalled UI operation cannot keep its inference process alive until that queue
finishes or the overall worker timeout. The controller starts termination, records
its request, waits for the recorded worker group to drain, then closes the browser
through the lane queue. Browser closure still logs out that owned application
session. The account reservation remains held for explicit recovery or cleanup;
freeing an inference slot is not permission for another run to adopt its account.

`CodexWorkers.stop` validates the detached worker's PID birth time and process
session/group. It captures owned group members while stopped, delivers SIGTERM
and continues the group. Surviving birth-matched members preserve ownership after
leader exit. At five seconds it escalates that owned group to SIGKILL; another
second bounds confirmation. The leader must be reaped and inherited recording
streams closed before its execution handle is released. Ownership mismatch,
recording closure or live-process failure remains `cleanup-pending`, with the
reservation retained. A failed termination cannot be swallowed as a successful
stop. No name-based kill, bare saved PGID, or broad process cleanup is used.

Current-generation structured provider/item activity can renew a legacy worker
lease only when PID and birth identity match the recorded worker. Stderr and
unstructured/unknown heartbeat noise cannot renew it. Provider/tool counters and
broker UI action counts are separate; observed turn lifetime includes tool time
and is not proof of simultaneous model inference. Returned exit records carry the
worker generation so a delayed old exit cannot pause a newly assigned lane.

The focused tests spawn private disposable Node workers, with no Codex inference,
application, browser or deployment. The stale-worker counterexample holds its UI
queue open, verifies immediate generation/token fencing and real worker/child
exit far before a sixty-second overall timeout, then drains the UI. It checks a
separate live peer remains usable. Other tests exercise five-second escalation,
stale PID birth rejection and cleanup-pending ownership. Scratch directories are
removed only after all fixture workers have been reaped; production reports and
recorded account/resource data remain retained.

Native T3/Codex threads are controlled by the coordinator, not `CodexWorkers`.
This interface exposes interruption but no native thread/session-close tool; an
interrupted or completed turn can retain its MCP connection. Expiry fences the
ledger, records `controller-intervention-required` and preserves resources; it
cannot claim native inference termination. The coordinator must interrupt the
actual owner, reconcile uncertain writes, obtain browser-close observation and
release only its bound extension transport. Existing `native-release` records
that extension's graceful termination and automatic cleanup, not thread closure.
A terminated extension cannot be assumed reloadable inside the same native thread;
use a fresh supported worker session when capacity permits, or report the actual
capacity/tooling block. No custom lifecycle API, provider alteration or T3 patch
is introduced.

This change covers the demonstrated lease/process defect. It does not establish
#139's broader concurrent UI, stalled-provider, controller-crash or interrupted-
write application acceptance. Those checks require prepared owned fixtures,
actual native actors, independent review and the supported launcher/deployment
lease workflow.

## Interrupted native recovery and initial-state checks

`recover` now fences the current native handle and persists paused ownership
before checking browser/transport closure. It no longer requires waiting for
lease expiry just to make the owned transport releasable. A live browser blocks
reassignment, keeps the recorded owner and reports the exact prerequisite. After
observed `browser_close`, the existing bound extension teardown verifies automatic
cleanup; preparation rechecks stable identity, grants and resource ownership.
This does not interrupt or close the native agent/session. The coordinator still
uses the actual available native controls, and a fresh thread/browser connection
may require a free runtime slot. In this session the terminated MCP connection
has no reload control and all native capacity can be occupied; those are real
constraints, not a reason to invent a lifecycle API or count broker recovery as
accepted application behavior.

Generated empty-design assertions carry `phase: initial`. They remain mandatory
until the first actual claim. Once the ledger records a prior claim/attempt,
recovery rechecks every stable/default fixture assertion while retaining the
owned saved design. Readiness history records the phase and initial assertions;
it does not assert those initial values again after an authorized write. Untagged
custom checks remain required, so this is not a blanket error skip. Retained older
claim metadata remains readable without rewriting its history.

Recovery briefs contain only pending scenarios plus prior completed outcomes.
Their required reconciliation reads visible saved state before continuing an
uncertain write; provider completion, stable GET checks and this code-level
recovery test cannot substitute for that live UI evidence. Desktop/mobile
readback dependencies and independent review still apply. The coordinator's
frozen #147 pilot can test ordinary reload recovery; broker interruption/reclaim
acceptance requires a newly built deployment and its own actual owned actors.
