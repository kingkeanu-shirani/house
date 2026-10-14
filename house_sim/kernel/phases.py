"""Multi-phase episodes: the phase controller, gaps and per-phase scoring (docs/design/TIER1_SWEEP.md sections 9
and 16.1).

Stub from the sweep's Phase 0 (tier-1 A0): the frozen signatures only, so the stage C packages can code against each
other. Every method raises NotImplementedError; the module registers nothing and nothing imports it yet. TIME-EVAL
implements it (library work may start in stage A, import-inert) and removes the TIER1-STUB markers before stage C
closes.
"""


class PhaseController:
    """Starts, ends and scores the phases of a task (sweep 9)."""

    def __init__(self, task):
        """TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("phases.PhaseController lands with TIME-EVAL (TIER1_SWEEP 9)")

    def active(self):
        """The active phase record. TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("phases.PhaseController.active lands with TIME-EVAL (TIER1_SWEEP 9)")

    def end(self, env, reason):
        """End the active phase ("done", "at_s", "when", "budget", "superseded"). TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("phases.PhaseController.end lands with TIME-EVAL (TIER1_SWEEP 9)")

    def start_next(self, env):
        """Run the gap (idle advance, scripted relocation, gap events) and start the next phase.
        TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("phases.PhaseController.start_next lands with TIME-EVAL (TIER1_SWEEP 9)")

    def record(self):
        """JSON-safe dict of the phase history (saved with the episode). TIER1-STUB(TIME-EVAL)"""
        raise NotImplementedError("phases.PhaseController.record lands with TIME-EVAL (TIER1_SWEEP 9)")
