"""Temporal evaluation: never / always / held_for / counts / responses / deadlines and milestones sampled inside
waits (docs/design/TIER1_SWEEP.md sections 8 and 16.1).

Stub from the sweep's Phase 0 (tier-1 A0): the frozen signatures only, so the stage C packages can code against each
other. Every method raises NotImplementedError; the module registers nothing and nothing imports it yet. TIME-EVAL
implements it (library work may start in stage A, import-inert) and removes the TIER1-STUB markers before stage C
closes.
"""


class Monitor:
    """Samples the task's temporal predicates at the engine's sample points (sweep 8.2)."""

    def __init__(self, task):
        """TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("monitor.Monitor lands with TIME-EVAL (TIER1_SWEEP 8.2)")

    def sample(self, env, point):
        """Sample at `point`: "action", "substep" or "end" (TIER1_INTEGRATION.md 1.9). TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("monitor.Monitor.sample lands with TIME-EVAL (TIER1_SWEEP 8.2)")

    def record(self):
        """JSON-safe dict of the monitor's state (saved with the episode). TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("monitor.Monitor.record lands with TIME-EVAL (TIER1_SWEEP 8.2)")

    def load(self, data):
        """Restore a record. TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("monitor.Monitor.load lands with TIME-EVAL (TIER1_SWEEP 8.2)")

    def decided(self, state):
        """None while the outcome is open, "fail" once a temporal predicate has failed for good (sweep 6.7).
        TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("monitor.Monitor.decided lands with TIME-EVAL (TIER1_SWEEP 6.7)")
