"""The idle counterfactual (VERIFY_HARDENING 6.2, WP1): what the same house looked like when the robot did nothing.

The schema-4 idle run waits out the whole budget plus settle plus look-ahead without early exit and records a
projection Pi(I_t) every max(300 s, budget / 96) of simulated time and at its end. The house ledger
(kernel/ledger.py) charges a physical or unattributed change only when it diverges from idle: when it differs beyond
its tolerance from both checkpoints that bracket the evaluation time (meters are interpolated).

    project(state) -> projection
        Pi of one state: every entity's existence and placement (location, relation, shelf; pos quantised to 0.25 m
        for fixed things), its setting, condition and content fields (6.3; content loads the mess balance judges,
        "guard": "mess", aside), its soil total; each room's floor soil
        and wet cells; the registered mess kinds' parcels by (kind, origin group, location key); the resource meters;
        each person's and pet's room and reachable rooms. JSON-safe (plain dicts and lists).
    Projections(checkpoints=(), meta=None)
        the checkpoints of one (spec, seed, mode) idle rollout, sorted by time: add(projection), bracket(t),
        values(key, t), to_dict() / Projections.from_dict(d), digest() (one rollout serves every mode once the first
        seed shows equal digests, 22.5)
    diverges(value, idle, t, tol=None) -> bool
        value = (key, actual): whether actual differs beyond tol from the idle value at both checkpoints that bracket
        t (a meter: from the interpolated value; a number or a list of numbers: whether it lies outside the range the
        two checkpoints span, widened by tol, since a process passes every value between them). Keys: ("exists",
        eid), ("place", eid), ("field", eid, path),
        ("soil", eid), ("room_soil", rid), ("room_wet", rid), ("mess", kind, group, class, id), ("meter", name),
        ("room", pid), ("reach", pid). idle None: True (nothing to compare with).
    value_at(idle, key, t, default=None)        the idle value at the checkpoint at or before t (a meter: interpolated)
    interval_for(budget_s) -> max(300, budget_s / 96)
    rollout(env, horizon_s, interval_s=None, on_sample=None) -> Projections
        advance env with ordinary waits for horizon_s, projecting at its start, every interval_s and at its end;
        on_sample(env, projection) after each projection (B1's success sampling may hook in)
"""
import bisect
import hashlib
import json

from .predicates import MISSING

SEP = "\x1f"
# the field classes a projection keeps: what a physical writer may change and the ledger then compares with idle
# (content: the removal guard's "below idle" test -- the dog eating the food the robot served is not a removal);
# identity fields are not kept (a physical change of one is rare and, without its idle value, charged)
PROJECTED_CLASSES = ("setting", "condition", "content")
_KEPT = {}
MIN_INTERVAL_S = 300.0
INTERVALS_PER_BUDGET = 96
WAIT_CHUNK_S = 3600.0


def interval_for(budget_s):
    """The projection interval of an idle rollout for a time budget: max(300 s, budget / 96)."""
    return max(MIN_INTERVAL_S, float(budget_s or 0.0) / INTERVALS_PER_BUDGET)


def _plain(value):
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def project(state):
    """The projection Pi of one state (see the module docstring)."""
    from . import ledger
    from .plugins import MESS_KINDS
    ents = {}
    kept = _KEPT
    for eid, record in (state.get("entities") or {}).items():
        type_name = record.get("type")
        fields = {}
        for path, value in ledger.watched_items(record):
            key = (type_name, path)
            keep = kept.get(key)
            if keep is None:
                spec = ledger.field_spec(type_name, path)
                keep = kept[key] = path.split(".", 1)[0] not in ledger.PLACEMENT_FIELDS and \
                    spec["class"] in PROJECTED_CLASSES and not (spec["class"] == "content" and spec.get("guard"))
            if keep:
                fields[path] = _plain(value)
        ents[eid] = {"p": _plain(ledger.placement(record)), "f": fields, "s": round(ledger.soil_total(record), 6)}
    rooms = {}
    soil = ledger.room_soil(state)
    wet = ledger.room_wet(state)
    for rid in state.get("rooms") or {}:
        rooms[rid] = {"soil": round(soil.get(rid, 0.0), 6), "wet": len(wet.get(rid, ()))}
    mess = {}
    for name in sorted(MESS_KINDS):
        parcels = ledger._parcels(MESS_KINDS[name].measure(state))
        origins = any(origin for (_, origin) in parcels)
        for (loc, origin), amount in parcels.items():
            group = ledger._group(origin if origins else None, name)
            key = SEP.join((name, str(group), loc[0], loc[1]))
            mess[key] = round(mess.get(key, 0.0) + amount, 9)
    meters = {}
    for name, meter in sorted(ledger.data("resources")["meters"].items()):
        meters[name] = ledger.meter_value(state, meter["path"])
    agents = {}
    for group in ("people", "pets"):
        for pid in sorted(state.get(group) or {}):
            agents[pid] = {"room": ledger.agent_room(state, group, pid),
                           "reach": sorted(ledger.reachable(state, group, pid))}
    return {"t": float(state.get("time_s", 0.0) or 0.0), "ents": ents, "rooms": rooms, "mess": mess,
            "meters": meters, "agents": agents}


def lookup(projection, key):
    """The value of a key in one projection (MISSING when absent)."""
    kind = key[0]
    if kind == "exists":
        return key[1] in projection["ents"]
    if kind in ("place", "field", "soil"):
        record = projection["ents"].get(key[1])
        if record is None:
            return MISSING
        if kind == "place":
            return record["p"]
        if kind == "soil":
            return record.get("s", 0.0)
        return record["f"].get(key[2], MISSING)
    if kind in ("room_soil", "room_wet"):
        return (projection["rooms"].get(key[1]) or {}).get("soil" if kind == "room_soil" else "wet", 0)
    if kind == "mess":
        return projection["mess"].get(SEP.join(str(k) for k in key[1:]), 0.0)
    if kind == "meter":
        return projection["meters"].get(key[1], 0.0)
    if kind in ("room", "reach"):
        record = projection["agents"].get(key[1])
        if record is None:
            return MISSING
        return record.get(kind)
    raise KeyError("unknown counterfactual key: {!r}".format(key))


class Projections(object):
    """The checkpoints of one idle rollout, sorted by time."""

    def __init__(self, checkpoints=(), meta=None):
        self.checkpoints = []
        self.times = []
        self.meta = dict(meta or {})
        for projection in checkpoints:
            self.add(projection)

    def add(self, projection):
        t = float(projection["t"])
        index = bisect.bisect_right(self.times, t)
        if index and abs(self.times[index - 1] - t) < 1e-9:
            self.checkpoints[index - 1] = projection          # the same time again: the later projection wins
            return
        self.times.insert(index, t)
        self.checkpoints.insert(index, projection)

    def __len__(self):
        return len(self.checkpoints)

    def bracket(self, t):
        """(lo, hi): the checkpoints at or before t and at or after t (the same one at a checkpoint time; the first
        or last one outside the rollout)."""
        if not self.checkpoints:
            raise ValueError("an idle rollout without checkpoints")
        t = float(t)
        index = bisect.bisect_left(self.times, t)
        if index < len(self.times) and abs(self.times[index] - t) < 1e-9:
            return self.checkpoints[index], self.checkpoints[index]
        if index == 0:
            return self.checkpoints[0], self.checkpoints[0]
        if index >= len(self.times):
            return self.checkpoints[-1], self.checkpoints[-1]
        return self.checkpoints[index - 1], self.checkpoints[index]

    def values(self, key, t):
        lo, hi = self.bracket(t)
        return lookup(lo, key), lookup(hi, key)

    def to_dict(self):
        return {"format": 1, "meta": self.meta, "checkpoints": self.checkpoints}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or raw.get("format") != 1:
            raise ValueError("not an idle rollout record (format 1)")
        return cls(raw.get("checkpoints") or (), raw.get("meta"))

    def digest(self):
        """A digest of the checkpoints (not the meta): equal digests mean the same counterfactual."""
        body = json.dumps(self.checkpoints, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _interpolated(idle, key, t):
    lo, hi = idle.bracket(t)
    a, b = lookup(lo, key), lookup(hi, key)
    if lo is hi or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a
    span = float(hi["t"]) - float(lo["t"])
    if span <= 0:
        return b
    share = (float(t) - float(lo["t"])) / span
    return float(a) + (float(b) - float(a)) * share


def _differs(key, expected, actual, tol):
    from . import ledger
    if key[0] == "place":
        if expected is MISSING or actual is MISSING or expected is None or actual is None:
            return not (expected is actual or (expected in (MISSING, None) and actual in (MISSING, None)))
        return not ledger.same_placement(_tupled(expected), _tupled(actual))
    if key[0] == "reach":
        return set(expected or ()) != set(actual or ()) if expected is not MISSING else True
    return ledger.differs(expected, actual, tol if tol is not None else ledger.DEFAULT_TOL)


def _tupled(value):
    if isinstance(value, list):
        return tuple(_tupled(v) for v in value)
    return value


def diverges(value, idle, t, tol=None):
    """Whether (key, actual) diverges from the idle rollout at time t (see the module docstring)."""
    key, actual = value
    if idle is None:
        return True
    actual = _plain(actual)
    if key[0] == "meter":
        expected = _interpolated(idle, key, t)
        return _differs(key, expected, actual, 1e-3 if tol is None else tol)
    lo, hi = idle.values(key, t)
    between = _between(lo, hi, actual, tol)
    if between is not None:
        return not between
    return _differs(key, lo, actual, tol) and _differs(key, hi, actual, tol)


def _numbers(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    if isinstance(value, list) and value and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                 for v in value):
        return [float(v) for v in value]
    return None


def _between(lo, hi, actual, tol):
    """For a number (or a list of numbers): whether actual lies within the range the two bracketing checkpoints span
    (widened by the tolerance) -- a quantity a process drives (a plant's health, grass growing, a towel drying) passes
    every value between them, so it differs from both checkpoints at almost every time without diverging. None when
    the values are not numbers of the same shape."""
    a, b, c = _numbers(lo), _numbers(hi), _numbers(actual)
    if a is None or b is None or c is None or not len(a) == len(b) == len(c):
        return None
    from . import ledger
    slack = float(tol) if tol is not None else ledger.DEFAULT_TOL
    return all(min(x, y) - slack <= z <= max(x, y) + slack for x, y, z in zip(a, b, c))


def value_at(idle, key, t, default=None):
    """The idle value of key at t: a meter interpolated, anything else from the checkpoint at or before t; default
    when idle is None or the key is absent."""
    if idle is None:
        return default
    if key[0] == "meter":
        found = _interpolated(idle, key, t)
    else:
        found = idle.values(key, t)[0]
    return default if found is MISSING else found


def rollout(env, horizon_s, interval_s=None, on_sample=None):
    """Advance env idle (ordinary waits, the robot does nothing) for horizon_s simulated seconds and return the
    Projections: one at the start, one every interval_s (default interval_for(horizon_s)) and one at the end.
    on_sample(env, projection) runs after each projection."""
    interval = float(interval_s) if interval_s else interval_for(horizon_s)
    if interval <= 0:
        raise ValueError("interval_s must be positive")
    state = env.world.s
    start = float(state.get("time_s", 0.0) or 0.0)
    end = start + max(0.0, float(horizon_s))
    rollout_ = Projections(meta={"start_s": start, "horizon_s": float(horizon_s), "interval_s": interval})

    def sample():
        projection = project(env.world.s)
        rollout_.add(projection)
        if on_sample is not None:
            on_sample(env, projection)

    sample()
    next_at = start + interval
    while True:
        now = float(env.world.s.get("time_s", 0.0) or 0.0)
        if now >= end - 1e-9:
            break
        target = min(next_at, end)
        chunk = min(WAIT_CHUNK_S, target - now)
        if chunk <= 1e-9:
            next_at += interval
            continue
        # wait takes whole seconds (a float is refused: the clock then crawled one refused step per second)
        env.step("wait", seconds=max(1, int(round(chunk))))
        later = float(env.world.s.get("time_s", 0.0) or 0.0)
        if later <= now + 1e-9:
            raise RuntimeError("an idle wait did not advance the clock at t={}".format(now))
        now = later
        if now >= next_at - 1e-9 and now < end - 1e-9:
            sample()
            while next_at <= now + 1e-9:
                next_at += interval
    if rollout_.times[-1] < float(env.world.s.get("time_s", 0.0) or 0.0) - 1e-9 or len(rollout_) == 1:
        sample()
    return rollout_
