"""Realistic perception helpers for the electrical domain (kernel v2.4, docs/PERCEPTION.md).

Every helper is only consulted when the env runs perception="realistic" (env.realism is not None); oracle
observations, inspector output and action messages stay byte-for-byte as before.

What a camera-and-sensors robot can know about electrical things:
  * whether a receptacle, strip or fixture is live only with a voltage tester held while inspecting it (or by
    plugging in something that visibly runs); a strip's lit power switch shows at a glance;
  * battery charge only from a battery tester (voltage to 0.01 V and a good/weak/replace verdict), a device's own
    screen (phones, tablets, watches show percent) or its LED (earbuds, controllers: a coarse bucket);
  * printed ratings (charger outputs, bulb wattage/colour temperature, cell chemistry, breaker directory) by `read`;
  * cord and cable damage only by a close inspection within reach;
  * room light as a camera exposure estimate (about +-10 %), never a lux meter reading.
Look-alike groups (cells, LED bulbs, power bricks, USB-C cables, phones, controllers, earbuds, remotes) are declared in
the type JSON; inspector views refer to entities by id (translated to handles) and never by a label that could
resolve an unresolved look-alike.
"""
from ...kernel.realism import CHARGE4, INSPECT, READ, _bucket

SCREEN_CATEGORIES = ("phone", "tablet", "computer", "watch", "smartwatch")
SCREEN_TYPES = ("smartphone", "smartphone_lightning", "tablet", "smartwatch", "laptop", "laptop_barrel", "laptop_computer",
                "fitness_tracker")


def realism_of(obj):
    """env.realism from an env or an action context (None in oracle mode)."""
    env = getattr(obj, "env", obj)
    return getattr(env, "realism", None)


def active(obj):
    return realism_of(obj) is not None


def knows(ctx, eid, field, level):
    """True when the robot already knows `field` of eid at `level` or better (realistic mode only)."""
    realism = realism_of(ctx)
    if realism is None:
        return True
    rec = ctx.w.s["entities"].get(eid) or {}
    return realism.knowledge.level(eid, rec, field) >= level


def resolved(ctx, eid, level=INSPECT):
    """The robot can tell what eid is (not an unresolved look-alike) at the given action level."""
    realism = realism_of(ctx)
    if realism is None:
        return True
    rec = ctx.w.s["entities"].get(eid) or {}
    if rec.get("novel"):
        return False            # an unfamiliar thing is never identified (docs/PERCEPTION.md)
    known = max(level, realism.knowledge.entity_level(eid, rec))
    return "candidates" not in realism.identity(eid, rec, known)


def unfamiliar(ctx, eid):
    """A novel ("unfamiliar") thing the robot cannot identify, in realistic mode. docs/PERCEPTION.md describes such a
    thing only by its visual attributes, so this domain's inspectors add nothing about it (no cord, plug, port, battery,
    cell, receptacle or alarm keys: a key set that differs by true type names the type) and its verbs answer one generic
    refusal before any check that depends on what the thing really is (independent review I4: the inspect keys
    `battery_display`, `cord_condition` / `plug`, `battery_compartment` and a connect_cable refusal listing the true
    ports told a phone, a hair dryer and a remote apart). False in oracle mode and for unknown ids."""
    if ctx is None or not active(ctx) or not isinstance(eid, str):
        return False
    rec = ctx.w.s["entities"].get(eid) or {}
    if not rec.get("novel"):
        return False
    check = getattr(ctx, "unresolved", None)
    return bool(check(eid)) if callable(check) else True


def refuse_unfamiliar(ctx, *eids):
    """The kernel's one generic refusal (ctx.refuse_unidentified, the same words for every unidentified thing) when any
    of eids is an unfamiliar thing (unfamiliar above). Call it after the visibility / reach checks of ctx.obj and before
    any check that depends on the thing's type."""
    for eid in eids:
        if unfamiliar(ctx, eid):
            ctx.refuse_unidentified(eid)


def has_screen(rec):
    kinds = {rec.get("type")} | set(rec.get("is_a") or [])
    return rec.get("category") in SCREEN_CATEGORIES or bool(kinds & set(SCREEN_TYPES))


def battery_view(rec, level):
    """What the device itself shows about its charge: a screen's percentage, else an LED bucket."""
    frac = max(0.0, min(1.0, float(level or 0.0)))
    if has_screen(rec):
        return {"battery_display": "{}%".format(int(round(frac * 100)))}
    return {"battery_led": _bucket(frac, *CHARGE4)}


def tester_volts(volts):
    """A digital battery tester's reading: 10 mV resolution."""
    return round(float(volts), 2)


def lux_estimate(env, rid, lux):
    """Room light as the camera's exposure meter estimates it: about +-10 %, two significant figures. The error is one
    fixed draw per room (a consistent exposure bias): looking again, waiting, walking to another cell or the light
    drifting does not redraw it (kernel v2.8 noise rule), so estimates cannot be averaged toward the true value."""
    realism = realism_of(env)
    value = float(lux or 0.0)
    if realism is None:
        return lux
    rng = realism._rng("lux", rid)
    est = max(0.0, value * (1.0 + rng.gauss(0.0, 0.1)))
    if est < 1.0:
        return round(est, 1)
    digits = len(str(int(est)))
    step = 10 ** max(0, digits - 2)
    return int(round(est / step) * step)


def read_known(ctx, eid, field):
    return knows(ctx, eid, field, READ)


def was_read(ctx, eid):
    """The robot has read eid's printed labels (a `read` that still holds, or one earlier in this step). Inspector
    extras that quote printed text (rating plates, charger outputs) show only then."""
    realism = realism_of(ctx)
    if realism is None:
        return True
    rec = ctx.w.s["entities"].get(eid) or {}
    if realism.knowledge.entity_level(eid, rec) >= READ:
        return True
    return any(g[0] == eid and g[1] >= READ and g[2] is None for g in getattr(realism, "pending", ()))


def passthrough(details, *keys):
    """Mark inspector keys as already-perceived (instrument readings, honestly rendered views): the kernel passes them
    through unchanged, also for unresolved look-alikes (kernel v2.6 details["_perceived"]). Realistic mode only."""
    marked = details.setdefault("_perceived", [])
    for key in keys:
        if key not in marked:
            marked.append(key)
