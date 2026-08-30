import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_execution import AiExecutionFailure, AiExecutionRunner, known_failure
from schemii.ai_operation_maintenance import OperationLeaseLost


class KnownError(Exception):
    status = 422
    payload = {"error": {"code": "known_failure", "message": "Known failure"}}


class Authority:
    def __init__(self):
        self.authorized = 0
        self.finished = []
        self.current = {
            "id": "operation-1", "state": "running", "result": None, "error": None,
        }

    def authorize_and_claim(self, proposal_id, chat_id, revision, confirmation):
        self.authorized += 1
        return {
            **self.current, "executionOwner": True, "attemptId": "attempt-1",
            "claimToken": "claim-1",
        }, {"policyRevision": revision}

    def finish_operation(self, attempt_id, token, state, *, result=None, error=None):
        self.finished.append((attempt_id, token, state, result, error))
        self.current = {**self.current, "state": state, "result": result, "error": error}
        return dict(self.current)

    def operation(self, operation_id, chat_id):
        return dict(self.current)


class ExistingAuthority(Authority):
    execution_owner = False

    def operation_for_proposal(self, proposal_id, chat_id):
        return dict(self.current)

    def authorize_and_claim(self, proposal_id, chat_id, revision, confirmation):
        self.authorized += 1
        return {
            **self.current, "executionOwner": self.execution_owner,
            "attemptId": "attempt-1", "claimToken": "claim-1",
        }, {"policyRevision": revision}


class Maintenance:
    def __init__(self):
        self.tracked = []
        self.released = []
        self.lost = False
        self.track_error = None

    def track(self, *args):
        self.tracked.append(args)
        if self.track_error is not None:
            raise self.track_error

    def assert_owned(self, attempt_id):
        if self.lost:
            raise OperationLeaseLost()

    def release(self, attempt_id):
        self.released.append(attempt_id)


class AiExecutionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.authority = Authority()
        self.maintenance = Maintenance()
        self.runner = AiExecutionRunner(self.authority, self.maintenance)
        self.cancellations = []

    def run_operation(self, *, preflight=lambda: {"ready": True}, execute=lambda _operation, _context: {"kind": "ok"}, **options):
        return self.runner.run(
            proposal_id="proposal-1", chat_id="chat-1", policy_revision=3,
            confirmation=None, preflight=preflight, execute=execute,
            classify_failure=lambda error: known_failure(error, (KnownError,)),
            release_cancellation=self.cancellations.append, **options,
        )

    def test_preflight_failure_never_claims(self):
        with self.assertRaises(KnownError):
            self.run_operation(preflight=lambda: (_ for _ in ()).throw(KnownError()))
        self.assertEqual(self.authority.authorized, 0)
        self.assertEqual(self.maintenance.tracked, [])

    def test_existing_operation_returns_without_revalidating_mutated_resource(self):
        authority = ExistingAuthority()
        authority.current.update({"state": "succeeded", "result": {"kind": "saved"}})
        outcome = AiExecutionRunner(authority, self.maintenance).run(
            proposal_id="proposal-1", chat_id="chat-1", policy_revision=3,
            confirmation=None, preflight=lambda: self.fail("must not revalidate"),
            execute=lambda *_: self.fail("must not execute"), classify_failure=lambda _error: None,
            return_existing_before_preflight=True,
        )
        self.assertEqual((outcome.status, outcome.operation["state"]), (200, "succeeded"))
        self.assertEqual(authority.authorized, 1)

    def test_claimed_existing_operation_still_finalizes_when_preflight_fails(self):
        authority = ExistingAuthority()
        authority.execution_owner = True
        outcome = AiExecutionRunner(authority, self.maintenance).run(
            proposal_id="proposal-1", chat_id="chat-1", policy_revision=3,
            confirmation=None, preflight=lambda: (_ for _ in ()).throw(KnownError()),
            execute=lambda *_: self.fail("must not execute"),
            classify_failure=lambda error: known_failure(error, (KnownError,)),
            return_existing_before_preflight=True,
        )
        self.assertEqual((outcome.status, outcome.operation["state"]), (422, "failed"))
        self.assertEqual(authority.finished[0][2], "failed")
        self.assertEqual(self.maintenance.released, ["attempt-1"])

    def test_known_failure_finishes_failed_and_always_releases(self):
        outcome = self.run_operation(execute=lambda *_: (_ for _ in ()).throw(KnownError()))
        self.assertEqual((outcome.status, outcome.operation["state"]), (422, "failed"))
        self.assertEqual(self.authority.finished[0][2], "failed")
        self.assertEqual(self.maintenance.released, ["attempt-1"])
        self.assertEqual(self.cancellations, ["operation-1"])

    def test_projection_and_maintenance_failures_are_uncertain_and_finalized(self):
        outcome = self.run_operation(durable_result=lambda _result: (_ for _ in ()).throw(RuntimeError("projection")))
        self.assertEqual((outcome.status, outcome.operation["state"]), (500, "uncertain"))
        self.assertEqual(self.authority.finished[0][4]["code"], "execution_outcome_unknown")

        authority = Authority()
        maintenance = Maintenance()
        maintenance.track_error = RuntimeError("tracking")
        outcome = AiExecutionRunner(authority, maintenance).run(
            proposal_id="proposal-1", chat_id="chat-1", policy_revision=3,
            confirmation=None, preflight=lambda: {}, execute=lambda *_: self.fail("must not execute"),
            classify_failure=lambda _error: None,
        )
        self.assertEqual(outcome.operation["state"], "uncertain")
        self.assertEqual(authority.finished[0][2], "uncertain")
        self.assertEqual(maintenance.released, ["attempt-1"])

    def test_lease_loss_uses_authoritative_operation_without_second_finish(self):
        self.maintenance.lost = True
        self.authority.current.update({
            "state": "uncertain",
            "error": {"code": "lease_lost", "message": "Reconcile without replay"},
        })
        outcome = self.run_operation()
        self.assertEqual((outcome.status, outcome.operation["state"]), (409, "uncertain"))
        self.assertEqual(self.authority.finished, [])
        self.assertEqual(self.maintenance.released, ["attempt-1"])


if __name__ == "__main__":
    unittest.main()
