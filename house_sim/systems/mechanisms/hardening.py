"""Verification-hardening prerequisites of the mechanisms domain (docs/design/VERIFY_HARDENING.md section 25 "WP-D
mechanisms", 23.3; docs/domains/mechanisms.md section 21).

  derived fields  (8.2, state-backed goals) a schema-4 predicate never reads a value a hook latched:
                    mech         the derived sub-keys of a mechanism record, recomputed from what they are computed from:
                                 a curtain's gap_cm / overlap_now_cm / coverage / exposed_frac, a blind's coverage /
                                 privacy_ok / view_in (coverings), a window's clear_opening_m2 / egress_ready (windows),
                                 a door's test_held (the test would still hold now), a prop that left, a high chair's
                                 clearance_m / hazards_in_reach
                    covered      a window's covered value, from its coverings as they are now (coverings.sync_window)
                    props_open, propping  the door a wedge still props
  settings        (10.3) settings skills for the fields this domain's verbs change and the kernel defaults do not cover
                  (B14's changed:<e>.<f> probe, the inverses of setting changes), and through the same registry the
                  damaging verbs B14's damaged:<e>.<f> probe needs; each step is a plan step of the reference skill
                  mechanisms.operate (walk up, then work it)
  hazard zones    (13.5) gas flowing from an unlit burner; a garage door coming down on its run
  mess            (6.5) what a verb of this domain drops out of a thing (common.fall_item) logs its flow; spills are real
                  spills (common.spill_out), measured by the liquid kinds' owner: this domain registers no mess kind
Writers and causes (6.2) are in world.py, garage.py and coverings.py (common.caused, common.volition).
"""
from ...kernel.plugins import derived_field, hook, settings_skill, skill
from ...kernel.predicates import MISSING
from . import closures as K
from . import common as C
from . import coverings as V
from . import windows as Win


def _rec(state, eid):
    return (state.get("entities") or {}).get(eid) or {}


# -------------------------------------------------------------------------------------------- derived fields (8.2)

@derived_field("mech")
def derived_mech(state, eid):
    """A mechanism record with its derived sub-keys recomputed from the state (module doc); the record itself when none
    of them applies. MISSING for a thing with no mech."""
    rec = _rec(state, eid)
    mech = rec.get("mech")
    if not isinstance(mech, dict):
        return MISSING
    out = dict(mech)
    kind = C.kind_of(rec)
    if kind == "curtain" and ("panels" in out or "gap_cm" in out):
        out.update(V.curtain_numbers(rec, _rec(state, V.window_of(rec)) or None))
    elif kind in ("blind", "shutter") and ("lift_frac" in out or "coverage" in out):
        out.update(V.blind_numbers(rec))
    elif kind in C.WINDOW_KINDS and ("clear_opening_m2" in out or "egress_ready" in out):
        out["clear_opening_m2"], out["egress_ready"] = Win.egress(rec)
    if "test_held" in out:
        out["test_held"] = bool(out["test_held"]) and not rec.get("open") and K.secure_now(rec)
    if "propped_by" in out and not C.prop_in_place(state, eid, rec):
        out.pop("propped_by")
    if kind == "high_chair" and "clearance_m" in out:
        out["clearance_m"], out["hazards_in_reach"] = _high_chair(state, eid)
    return out


def _high_chair(state, eid):
    from .world import high_chair_clearance

    class _View(object):        # high_chair_clearance only reads w.s
        s = state
    return high_chair_clearance(_View, eid)


WINDOW_TYPES = ("window", "sash_window", "casement_window", "hopper_window", "sliding_window", "egress_window", "skylight")


@derived_field("covered", types=WINDOW_TYPES)
def derived_covered(state, eid):
    """A window's covered value from its coverings as they are now; what the record says when it has none (a scene's
    fixed film or board)."""
    rec = _rec(state, eid)
    covers = V.coverings_of(state, eid)
    if not covers:
        return rec["covered"] if "covered" in rec else MISSING
    free = 1.0
    for cov in covers:
        free *= 1.0 - C.clamp(V.covering_coverage(state, cov), 0.0, 1.0)
    return round(1.0 - free, 3)


def _prop_door(state, eid):
    """The door this wedge still props (it stands at it and the door names it), else MISSING."""
    door = _rec(state, eid).get("props_open")
    if door and (_rec(state, door).get("mech") or {}).get("propped_by") == eid and C.prop_in_place(state, door):
        return door
    return MISSING


derived_field("props_open", _prop_door)


@derived_field("propping")
def derived_propping(state, eid):
    return _prop_door(state, eid)


# ------------------------------------------------------------------------------------ settings skills (10.3, B14)

def _step(eid, verb, **args):
    step = {"skill": "mechanisms.operate", "target": eid, "verb": verb}
    step.update(args)
    return step


@skill("mechanisms.operate")
def operate(r, target, verb, **args):
    """Walk up to a closure or control and work it with one verb of this domain: the battery's settings and damage
    probes (VERIFY_HARDENING 10.3; P11: privileged routing is fine)."""
    from .skills import _approach
    _approach(r, target)
    args = {k: v for k, v in args.items() if v is not None}
    r.do(verb, target=target, **args)


@settings_skill("latched")
def _latched(state, eid):
    rec = _rec(state, eid)
    if "latched" not in rec or rec.get("locked"):
        return []
    if rec.get("latched"):
        return [(False, _step(eid, "unlatch"))]
    return [(True, _step(eid, "latch", lift=True))] if not rec.get("open") else []


@settings_skill("child_lock")
def _child_lock(state, eid):
    rec = _rec(state, eid)
    if not C.mget(rec, "child_lock_kind") or C.mget(rec, "child_lock_broken"):
        return []
    if rec.get("child_lock"):
        return [(False, _step(eid, "unlatch", part="child_lock"))] if C.mget(rec, "child_lock_kind") != "magnetic" else []
    return [(True, _step(eid, "latch", part="child_lock"))]


@settings_skill("mech.panels")
def _panels(state, eid):
    rec = _rec(state, eid)
    if C.kind_of(rec) != "curtain" or C.mget(rec, "rod") == "down" or C.mget(rec, "jammed"):
        return []
    panels = C.mget(rec, "panels") or {}
    extent = 0.0 if C.num(panels.get("left")) < 0.5 else 1.0        # pull_handle extent: how far open
    return [({"left": 1.0 - extent, "right": 1.0 - extent}, _step(eid, "pull_handle", extent=extent, grip="wand"))]


@settings_skill("mech.lift_frac")
def _lift(state, eid):
    rec = _rec(state, eid)
    if C.kind_of(rec) not in ("blind", "shutter") or C.mget(rec, "jammed"):
        return []
    if C.num(C.mget(rec, "lift_frac", 1.0), 1.0) > 0.5:
        return [(0.0, _step(eid, "lower", to_frac=0.0))]
    return [(1.0, _step(eid, "raise", to_frac=1.0))]


@settings_skill("mech.tilt_deg")
def _tilt(state, eid):
    rec = _rec(state, eid)
    if C.kind_of(rec) not in ("blind", "shutter") or C.mget(rec, "style", "venetian") not in ("venetian", "vertical", "shutter"):
        return []
    angle = 0.0 if abs(C.num(C.mget(rec, "tilt_deg"))) > 30.0 else 70.0
    return [(angle, _step(eid, "tilt_slats", angle_deg=angle))]


@settings_skill("mech.cord_cleated")
def _cleat(state, eid):
    rec = _rec(state, eid)
    if C.kind_of(rec) not in ("blind", "shutter") or C.mget(rec, "lift") in ("cordless", "motorized"):
        return []
    if C.mget(rec, "cord_cleated"):
        return [(False, _step(eid, "unlatch", part="cord"))]
    return [(True, _step(eid, "latch", part="cord"))]


@settings_skill("mech.open_frac")
def _open_frac(state, eid):
    """A window's sash set to another height (`open` is the kernel default's)."""
    rec = _rec(state, eid)
    if C.kind_of(rec) not in C.WINDOW_KINDS or rec.get("locked") or C.hidden(rec).get("stuck"):
        return []
    value = 0.5 if C.num(C.mget(rec, "open_frac", 1.0 if rec.get("open") else 0.0)) < 0.25 else 0.0
    return [(value, _step(eid, "raise" if value else "lower", to_frac=value))]


@settings_skill("mech.hasp")
def _hasp(state, eid):
    hasp = C.mget(_rec(state, eid), "hasp")
    if not isinstance(hasp, dict) or hasp.get("padlock"):
        return []
    value = dict(hasp, closed=not hasp.get("closed"))
    return [(value, _step(eid, "unlatch" if hasp.get("closed") else "latch", part="hasp"))]


@settings_skill("pump.locked")
def _pump_head(state, eid):
    pump = _rec(state, eid).get("pump")
    if not isinstance(pump, dict) or "locked" not in pump or pump.get("kind") == "air":
        return []
    return [(not pump.get("locked"), _step(eid, "turn", part="pump_head", value="unlocked" if pump.get("locked") else "locked"))]


@settings_skill("trigger.nozzle")
def _nozzle(state, eid):
    trigger = _rec(state, eid).get("trigger")
    if not isinstance(trigger, dict) or "nozzle" not in trigger:
        return []
    modes = [m for m in trigger.get("modes") or ("off", "spray", "stream") if m != trigger.get("nozzle")]
    return [(modes[0], _step(eid, "turn", part="nozzle", value=modes[0]))] if modes else []


@settings_skill("mech.mode", types=("pet_door",))
def _pet_mode(state, eid):
    current = C.mget(_rec(state, eid), "mode", "open")
    value = "locked" if current != "locked" else "open"
    return [(value, _step(eid, "turn", part="dial", value=value))]


# damaging verbs (B14's damaged:<e>.<f>; the value is the worse one)

@settings_skill("mech.strain")
def _strain(state, eid):
    """Forcing a hinged door or gate the wrong way (the room the robot works from is chosen by the door's swing)."""
    rec = _rec(state, eid)
    if C.kind_of(rec) not in ("hinged_door", "gate", "french_door", "storm_door", "double_door") or rec.get("open") or rec.get("locked"):
        return []
    return [(int(C.num(C.mget(rec, "strain"))) + 1, {"skill": "mechanisms.force_wrong_way", "target": eid})]


@skill("mechanisms.force_wrong_way")
def force_wrong_way(r, target):
    """Stand on either side of a shut hinged door and force it against its stop (handles._wrong_way)."""
    from .skills import _approach
    _approach(r, target)
    pulling = C.swing_into(r.state, target) != r.state["robot"]["room"]
    r.do("pull_handle" if pulling else "push_handle", target=target, force="firm")


@settings_skill("mech.jammed", types=("curtains", "tieback_curtains", "sheer_curtains", "blackout_curtains"))
def _curtain_jam(state, eid):
    """Hauling a curtain by its fabric bunches the rings."""
    rec = _rec(state, eid)
    if C.kind_of(rec) != "curtain" or C.mget(rec, "jammed") or C.mget(rec, "rod") == "down":
        return []
    return [(True, _step(eid, "pull_handle", grip="fabric", force="normal", extent=1.0))]


@settings_skill("mech.skew_cm", types=("venetian_blind", "wood_blind"))
def _blind_skew(state, eid):
    """Pulling a cord-lock blind's cord off vertical raises one side faster."""
    rec = _rec(state, eid)
    if C.mget(rec, "lift") != "cord_lock" or C.mget(rec, "jammed"):
        return []
    return [(C.num(C.mget(rec, "skew_cm")) + 5.0, _step(eid, "raise", to_frac=1.0, cord_angle_deg=40.0))]


@settings_skill("broken", types=("sliding_door", "french_door", "storm_door", "glass_front_cabinet"))
def _pane(state, eid):
    """A firm push on a door's or cabinet's pane cracks it."""
    return [(True, _step(eid, "push_handle", where="glass", force="firm"))] if not _rec(state, eid).get("broken") else []


# -------------------------------------------------------------------------------------------------- hazard zones

@hook("hazard_zones")
def mechanism_hazard_zones(state):
    """[{"id", "room", "subjects"}] of this domain's active hazards a warning is about: gas flowing from an unlit burner
    (the room: the gas spreads) and a garage door on its way down (the door itself). The curtains and fires are the fire
    owner's."""
    out = []
    entities = state.get("entities") or {}
    for eid in sorted(entities):
        rec = entities[eid]
        mech = rec.get("mech") if isinstance(rec, dict) else None
        if not isinstance(mech, dict):
            continue
        gas = mech.get("gas")
        if isinstance(gas, dict) and gas.get("flow") and not gas.get("lit"):
            out.append({"id": "gas_unlit:{}".format(eid), "room": C.room_of(state, eid), "subjects": [eid]})
        if mech.get("state") == "closing" and C.kind_of(rec) == "overhead_door":
            out.append({"id": "garage_door:{}".format(eid), "room": None, "subjects": [eid]})
    return out
