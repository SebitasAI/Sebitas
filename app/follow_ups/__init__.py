"""Reactivation nudges for stuck conversations.

The agent calls `schedule_follow_up` at end of a turn when the
conversation will be blocked until the user responds. A background
worker fires the nudge after `wait_hours`, UNLESS the user replied to
the thread between creation and fire time (auto-cancellation).

Phase 1 (this slice): single nudge per follow-up, auto-cancel on reply.
Phase 2 candidates: escalation (re-nudge after 2x wait), manual cancel
via agent tool, integration-connect pending detector, "user promised
in memory" detector.
"""
