"""The robot's memory outside world state: the known map, last-seen beliefs, snapshots and prior knowledge
(docs/design/TIER1_SWEEP.md sections 7 and 16.1).

Stub from the sweep's Phase 0 (tier-1 A0): the frozen signatures only, so the stage C packages can code against each
other. Every method raises NotImplementedError; the module registers nothing and nothing imports it yet. PEOPLE-IO
implements it (library work may start in stage A, import-inert); TIME-EVAL's engine calls Mind.from_config at reset
and mind.update at the end of every step (TIER1_INTEGRATION.md 1.9). Markers TIER1-STUB(PEOPLE-IO) are removed
before stage C closes.
"""


class Mind:
    """{"map", "seen", "snapshots", "prior"} (sweep 7.1); saved by env.save, rebuilt identically on replay."""

    @classmethod
    def from_config(cls, config, env):
        """The mind at reset, from the config's prior_knowledge section (sweep 7.4). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.from_config lands with PEOPLE-IO (TIER1_SWEEP 7.1)")

    def update(self, env, perceived_ids):
        """Refresh the map and beliefs from the ids perceived this step (sweep 7.1). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.update lands with PEOPLE-IO (TIER1_SWEEP 7.1)")

    def view(self):
        """Read-only dict for `mind.*` predicate paths (sweep 7.1). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.view lands with PEOPLE-IO (TIER1_SWEEP 7.1)")

    def recall(self, prefix):
        """The `recall` forms map / seen: / seen:room: / snap: (sweep 7.3). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.recall lands with PEOPLE-IO (TIER1_SWEEP 7.3)")

    def snapshot(self, name, env):
        """Remember the perceived scene under `name` (sweep 7.1). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.snapshot lands with PEOPLE-IO (TIER1_SWEEP 7.1)")

    def diff(self, name, env):
        """Compare the perceived scene with snapshot `name` (sweep 7.1). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.diff lands with PEOPLE-IO (TIER1_SWEEP 7.1)")

    def dump(self):
        """JSON-safe dict of the whole mind (env.save, prior_knowledge import). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.dump lands with PEOPLE-IO (TIER1_SWEEP 7.5)")

    def load(self, data):
        """Restore a dump (env.load). TIER1-STUB(PEOPLE-IO)"""
        raise NotImplementedError("mind.Mind.load lands with PEOPLE-IO (TIER1_SWEEP 7.5)")
